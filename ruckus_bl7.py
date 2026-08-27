#!/usr/bin/env python3
"""Parse and rebuild unsigned Ruckus AP ``.bl7`` images.

The format is intentionally handled without guessing: this module supports
the unsigned (UI) form used by the local R600 test image and refuses signed
ISI/FSI images rather than discarding their trailer or signatures.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct


HEADER_SIZE = 160
MAGIC = b"RCKS"
UI_IMAGE_TYPE = 0
PAGE_SIZE = 0x10000


def _internet_checksum(data: bytes) -> int:
    total = 0
    for offset in range(0, len(data), 2):
        word = data[offset : offset + 2]
        if len(word) == 1:
            word += b"\0"
        total += int.from_bytes(word, "big")
        total = (total & 0xFFFF) + (total >> 16)
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


@dataclass(frozen=True)
class Bl7Image:
    header: bytes
    kernel: bytes
    rootfs: bytes
    kernel_padding: bytes

    @property
    def image_type(self) -> int:
        return struct.unpack(">I", self.header[0x84:0x88])[0]

    @property
    def version(self) -> str:
        return self.header[0x2C:0x3C].split(b"\0", 1)[0].decode("ascii", "replace")

    def rebuild(self, *, kernel: bytes | None = None, rootfs: bytes | None = None) -> bytes:
        """Return a UI image with recalculated offsets, MD5, and header checksum."""
        if self.image_type != UI_IMAGE_TYPE:
            raise ValueError("refusing to rebuild signed ISI/FSI BL7 image")
        new_kernel = self.kernel if kernel is None else bytes(kernel)
        new_rootfs = self.rootfs if rootfs is None else bytes(rootfs)
        kernel_padding_length = (- (HEADER_SIZE + len(new_kernel))) % PAGE_SIZE
        padding = b"\xff" * kernel_padding_length
        payload = new_kernel + padding + new_rootfs
        header = bytearray(self.header)
        header[4:8] = struct.pack(">I", HEADER_SIZE + len(new_kernel) + len(padding))
        header[0x10:0x14] = struct.pack(">I", len(payload))
        header[0x18:0x28] = hashlib.md5(payload).digest()
        header[0x2A:0x2C] = b"\0\0"
        header[0x2A:0x2C] = struct.pack(">H", _internet_checksum(header))
        return bytes(header) + payload


def parse_bl7(data: bytes) -> Bl7Image:
    """Split an unsigned BL7 image into header, kernel, and rootfs."""
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise ValueError("not a Ruckus BL7 image")
    header = bytes(data[:HEADER_SIZE])
    hdr_len = header[9]
    if hdr_len != HEADER_SIZE:
        raise ValueError(f"unsupported BL7 header length: {hdr_len}")
    next_image = struct.unpack(">I", header[4:8])[0]
    binl7_len = struct.unpack(">I", header[0x10:0x14])[0]
    image_type = struct.unpack(">I", header[0x84:0x88])[0]
    if image_type != UI_IMAGE_TYPE:
        raise ValueError("signed ISI/FSI BL7 images are not supported")
    payload_end = HEADER_SIZE + binl7_len
    if next_image < HEADER_SIZE or next_image > payload_end or payload_end > len(data):
        raise ValueError("BL7 offsets exceed image bounds")
    kernel_padded = bytes(data[HEADER_SIZE:next_image])
    kernel = kernel_padded.rstrip(b"\xff")
    padding = kernel_padded[len(kernel) :]
    rootfs = bytes(data[next_image:payload_end])
    payload = kernel_padded + rootfs
    if hashlib.md5(payload).digest() != header[0x18:0x28]:
        raise ValueError("BL7 payload MD5 mismatch")
    checksum_header = bytearray(header)
    expected_checksum = struct.unpack(">H", checksum_header[0x2A:0x2C])[0]
    checksum_header[0x2A:0x2C] = b"\0\0"
    if _internet_checksum(checksum_header) != expected_checksum:
        raise ValueError("BL7 header checksum mismatch")
    return Bl7Image(header, kernel, rootfs, padding)

