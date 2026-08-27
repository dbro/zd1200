#!/usr/bin/env python3
"""Build a disposable, partitioned disk around the extracted ZD rootfs."""

from pathlib import Path
import gzip
import os
import struct

base = Path(__file__).resolve().parent
rootfs = base / "image" / "rootfs.ext2"
disk = Path(os.environ.get("SYNTHETIC_DISK", base / "synthetic-cf.img"))
disk.parent.mkdir(parents=True, exist_ok=True)

SECTOR = 512
DISK_SIZE = 2 * 1024 * 1024 * 1024

# Conservative CF-like layout: three ext2-sized areas. The first area is the
# boarddata/himem partition that the kernel opens through the ext2 layer.
p1_start, p1_sectors = 2048, 327680      # 160 MiB
p2_start, p2_sectors = 329728, 327680    # 160 MiB
p3_start, p3_sectors = 657408, 327680    # 160 MiB
p4_start, p4_sectors = 985088, 3000000   # writable area / remaining CF

with rootfs.open("rb") as rootfs_stream:
    rootfs_magic = rootfs_stream.read(3)
if rootfs_magic == b"\x1f\x8b\x08":
    rootfs_data = gzip.decompress(rootfs.read_bytes())
else:
    rootfs_data = rootfs.read_bytes()

if len(rootfs_data) > p2_sectors * SECTOR:
    raise SystemExit("rootfs does not fit in synthetic root partition")

with disk.open("wb") as handle:
    handle.truncate(DISK_SIZE)

mbr = bytearray(SECTOR)
entries = [
    (0x00, 0x83, p1_start, p1_sectors),
    (0x80, 0x83, p2_start, p2_sectors),
    (0x00, 0x83, p3_start, p3_sectors),
    (0x00, 0x83, p4_start, p4_sectors),
]

for index, (boot, part_type, start, count) in enumerate(entries):
    # CHS is intentionally saturated; modern kernels use the LBA fields.
    offset = 446 + index * 16
    mbr[offset] = boot
    mbr[offset + 1:offset + 4] = b"\xfe\xff\xff"
    mbr[offset + 4] = part_type
    mbr[offset + 5:offset + 8] = b"\xfe\xff\xff"
    struct.pack_into("<II", mbr, offset + 8, start, count)

mbr[510:512] = b"\x55\xaa"
with disk.open("r+b") as handle:
    handle.write(mbr)
    data = rootfs_data
    # The physical appliance seeds its writable partition with the same base
    # filesystem tree. ZoneDirector then mounts it at /writable and uses its
    # etc/config, database, certificate, and image directories as defaults.
    for start in (p1_start, p2_start, p3_start, p4_start):
        handle.seek(start * SECTOR)
        handle.write(data)

    # The boarddata helper reads raw 512-byte sectors. For the first record it
    # adds 0x8000 / 1024 = 32 sectors to 0x3BD3F1; the second starts at the
    # base sector. These are only signatures for the first bring-up attempt.
    base_sector = 0x3BD3F1
    for delta in (16, 32, 64, 128, 0x8000 // 9, 0x8000 // 10,
                  0x8000 // 11, 0x8000 // 12):
        handle.seek((base_sector + delta) * SECTOR)
        handle.write(b"SKCR")
    handle.seek(base_sector * SECTOR)
    handle.write(b"1135")

print(f"created {disk} ({DISK_SIZE // (1024 * 1024)} MiB)")
