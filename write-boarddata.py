#!/usr/bin/env python3
"""Write a full ZoneDirector board-data record into a synthetic CF disk image.

Why this exists
---------------
The ZD1200 kernel's v54bsp driver (drivers/v54bsp/board_data.c) reads the
board data (serial number, MACs, model, ...) from a CompactFlash card, not
from NOR/MTD flash: `ar531x_get_board_config()` in nar5520_bsp.c issues
raw 512-byte sector reads via `nar5520_cf_read_write()` on the block device
named by `boarddata_dev_path` (resolved from the kernel `root=` argument,
e.g. /dev/hda in the QEMU lab).

The two records live at fixed positions on that disk:

    record                      sector              byte offset
    --------------------------  ------------------  ------------
    struct ar531x_boarddata     region2_start + 0   0
    struct rks_boarddata        region2_start + 64  0x8000

where region2_start = 3920881 (CONFIG_V54_ZD_PLATFORM == 1) or 3981601
(platform 0).  The offsets are RKS_BOARD_CONFIG_START (= 0) and
RKS_BOARD_DATA_STARAT (= RKS_BD_OFFSET = DATA_PART_SIZE*8 = 0x8000 bytes),
applied as `sector_start + bytes_offset/512`.

The kernel only validates the magic fields when loading these records:
rks_boarddata.magic must be RKS_BD_MAGIC 0x52434B53 ("SKCR") and
ar531x_boarddata.magic must be AR531X_BD_MAGIC 0x35333131 ("1135").
User-space tools additionally verify rks_boarddata.cksum: the 16-bit sum of
all 0xd0 bytes with the cksum field zeroed (see rksBoardDataChecksum() in
the vendor libbsp.so), computed only when rev <= 4.

MACs: a real ZD1200 carries two NIC MACs with MAC2 = MAC1 + 1.  This tool
takes the base MAC (MAC1) and derives MAC2 automatically, then writes both
into the records: ar531x_boarddata.{wlan0Mac,enet0Mac} = MAC1 and
ar531x_boarddata.{wlan1Mac,enet1Mac} = MAC2, plus
rks_boarddata.enetxMac[0] = MAC1, rks_boarddata.enetxMac[1] = MAC2 and
rks_boarddata.MACbase = MAC1.

With a valid record in place, the stock kernel populates its `rbd` struct
from the CF at boot, so /proc/v54bsp/serial and the MAC queries return the
values written here -- no kernel patch is needed for serial number or MACs.

This mirrors (and supersedes the magic-only seeds of) make-synthetic-cf.py.
"""

import argparse
import struct
from pathlib import Path

RKS_BD_MAGIC = 0x52434B53          # "SKCR"
AR531X_BD_MAGIC = 0x35333131       # "1135"
RKS_BOOTREC_MAGIC = 0x524B4254     # "RKBT"
RKS_BD_REV = 4
AR531X_BD_REV = 5

REGION2_START = 3920881            # CONFIG_V54_ZD_PLATFORM == 1 (ZD1200)
SECTOR = 512
RKS_BD_OFFSET = 0x8000             # DATA_PART_SIZE(0x1000) * 8

RKS_STRUCT_SIZE = 0xD0             # sizeof(struct rks_boarddata), rev 4
AR531X_STRUCT_SIZE = 0x80          # sizeof(struct ar531x_boarddata), rev 5


def parse_mac(mac: str) -> bytes:
    m = bytes(int(x, 16) for x in mac.split(":"))
    assert len(m) == 6, f"bad MAC {mac!r}"
    return m


def mac_plus_one(mac: bytes) -> bytes:
    """MAC2 = MAC1 + 1 (carry over the last octet)."""
    v = (mac[0] << 40) | (mac[1] << 32) | (mac[2] << 24) | \
        (mac[3] << 16) | (mac[4] << 8) | mac[5]
    v = (v + 1) & 0xFFFFFFFFFFFF
    return v.to_bytes(6, "big")


def rks_cksum(blob: bytes) -> int:
    """16-bit checksum as computed by libbsp.so rksBoardDataChecksum():
    sum of all RKS_STRUCT_SIZE bytes with the cksum field zeroed."""
    assert len(blob) == RKS_STRUCT_SIZE
    work = bytearray(blob)
    work[4:6] = b"\x00\x00"
    return sum(work) & 0xFFFF


def build_rks_boarddata(serial: str, mac1: bytes, mac2: bytes,
                        model: str, customer: str) -> bytes:
    """Build struct rks_boarddata (0xd0 bytes, RKS_BD_REV 4)."""
    b = bytearray(RKS_STRUCT_SIZE)
    struct.pack_into("<I", b, 0x00, RKS_BD_MAGIC)
    struct.pack_into("<H", b, 0x06, RKS_BD_REV)          # rev @ +6
    sn = serial.encode()[:15]
    b[0x08:0x08 + len(sn)] = sn                          # serialNumber @ +8
    cn = customer.encode()[:31]
    b[0x18:0x18 + len(cn)] = cn                          # customerID @ +0x18
    md = model.encode()[:15]
    b[0x38:0x38 + len(md)] = md                          # model @ +0x38
    b[0x58:0x5e] = mac1                                  # enetxMac[0] @ +0x58
    b[0x5e:0x64] = mac2                                  # enetxMac[1] @ +0x5e
    b[0x70] = 0                                          # boardType
    b[0x78:0x7c] = struct.pack("<I", 0x80)               # v54Config: BD_MACPOOL
    struct.pack_into("<H", b, 0x7C, 2)                   # MACcnt @ +0x7c (2 MACs)
    b[0x7e:0x84] = mac1                                  # MACbase @ +0x7e
    struct.pack_into("<I", b, 0x90, RKS_BOOTREC_MAGIC)   # bootrec @ +0x90
    b[0x98] = 1                                          # eth_port
    sn32 = serial.encode()[:31]
    b[0x9c:0x9c + len(sn32)] = sn32                      # serialNumber32 @ +0x9c
    ck = rks_cksum(bytes(b))
    struct.pack_into("<H", b, 0x04, ck)                  # cksum @ +4
    return bytes(b)


def build_ar531x_boarddata(mac1: bytes, mac2: bytes, model: str) -> bytes:
    """Build struct ar531x_boarddata (rev 5): magic, rev, name and the MACs.

    Offsets for BD_REV 5: boardName[64] @ +0x08, wlan0Mac @ +0x60,
    enet0Mac @ +0x66, enet1Mac @ +0x6c, wlan1Mac @ +0x76.
    The kernel only checks the magic dword (ar531x_get_board_config), but
    bd_get_lan_macaddr() serves enet0Mac/enet1Mac from this record.
    """
    b = bytearray(AR531X_STRUCT_SIZE)
    struct.pack_into("<I", b, 0x00, AR531X_BD_MAGIC)
    struct.pack_into("<H", b, 0x06, AR531X_BD_REV)       # BD_REV
    nm = model.encode()[:63]
    b[0x08:0x08 + len(nm)] = nm                          # boardName @ +8
    b[0x60:0x66] = mac1                                  # wlan0Mac @ +0x60
    b[0x66:0x6c] = mac1                                  # enet0Mac @ +0x66
    b[0x6c:0x72] = mac2                                  # enet1Mac @ +0x6c
    b[0x76:0x7c] = mac2                                  # wlan1Mac @ +0x76
    return bytes(b)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--disk", default="synthetic-cf.img",
                    help="CF disk image to patch (default synthetic-cf.img)")
    ap.add_argument("--serial", default="123456000789",
                    help="serial number (default 123456000789)")
    ap.add_argument("--mac", dest="mac1", default="01:01:01:01:01:02",
                    help="base MAC (MAC1); MAC2 = MAC1 + 1 is derived "
                         "(default 01:01:01:01:01:02)")
    ap.add_argument("--model", default="ZD1200", help="model string")
    ap.add_argument("--customer", default="ruckus", help="customer string")
    ap.add_argument("--platform", type=int, default=1, choices=(0, 1),
                    help="CONFIG_V54_ZD_PLATFORM (default 1)")
    args = ap.parse_args()

    disk = Path(args.disk)
    if not disk.exists():
        raise SystemExit(f"disk image not found: {disk}")
    mac1 = parse_mac(args.mac1)
    mac2 = mac_plus_one(mac1)

    region2 = 3981601 if args.platform == 0 else REGION2_START
    rbd = build_rks_boarddata(args.serial, mac1, mac2, args.model, args.customer)
    abd = build_ar531x_boarddata(mac1, mac2, args.model)

    rks_sector = region2 + RKS_BD_OFFSET // SECTOR
    ar531x_sector = region2
    rks_off = rks_sector * SECTOR
    ar531x_off = ar531x_sector * SECTOR

    with disk.open("r+b") as f:
        f.seek(ar531x_off)
        f.write(abd)
        f.seek(rks_off)
        f.write(rbd)

    ck = rks_cksum(rbd)
    mac1_s = mac1.hex(":")
    mac2_s = mac2.hex(":")
    print(f"patched {disk} ({disk.stat().st_size // (1024*1024)} MiB)")
    print(f"  ar531x_boarddata: sector {ar531x_sector} (file 0x{ar531x_off:x}), "
          f"magic {abd[:4]!r}")
    print(f"  rks_boarddata:    sector {rks_sector} (file 0x{rks_off:x}), "
          f"magic {rbd[:4]!r}, rev {struct.unpack_from('<H', rbd, 6)[0]}, "
          f"cksum {ck:#06x}")
    print(f"  serial: {args.serial!r}")
    print(f"  MAC1:   {mac1_s}   (wlan0/enet0/enetxMac[0]/MACbase)")
    print(f"  MAC2:   {mac2_s}   (= MAC1 + 1; wlan1/enet1/enetxMac[1])")
    print(f"  serialNumber @ +0x08: {rbd[0x08:0x18].rstrip(b'\\x00')!r}")
    print(f"  serialNumber32 @ +0x9c: {rbd[0x9c:0xbc].rstrip(b'\\x00')!r}")


if __name__ == "__main__":
    main()
