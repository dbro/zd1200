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

with rootfs.open("rb") as rootfs_stream:
    rootfs_magic = rootfs_stream.read(3)
if rootfs_magic == b"\x1f\x8b\x08":
    rootfs_data = gzip.decompress(rootfs.read_bytes())
else:
    rootfs_data = rootfs.read_bytes()

# Conservative CF-like layout: p1 and p3 remain 160 MiB platform areas.  The
# rootfs (p2) is rounded up to a 16-MiB boundary because 10.1.2's 174-MiB
# rootfs is larger than the 160-MiB partition used by newer releases.  p4 gets
# the balance, including the fixed board-data sector near the end of the disk.
ALIGN_SECTORS = (16 * 1024 * 1024) // SECTOR
p1_start, p1_sectors = 2048, 327680
p2_start = p1_start + p1_sectors
rootfs_sectors = (len(rootfs_data) + SECTOR - 1) // SECTOR
p2_sectors = ((rootfs_sectors + ALIGN_SECTORS - 1) // ALIGN_SECTORS) * ALIGN_SECTORS
p3_start, p3_sectors = p2_start + p2_sectors, 327680
p4_start = p3_start + p3_sectors
p4_sectors = DISK_SIZE // SECTOR - p4_start
if p4_sectors <= 0:
    raise SystemExit("rootfs leaves no writable synthetic CF partition")

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
