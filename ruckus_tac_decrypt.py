#!/usr/bin/env python3
"""Decrypt legacy Ruckus TAC-format archives locally.

This is an adapted, dependency-free implementation of the TAC branch in
``aioruckus/backupsession.py`` at upstream commit
``9bc44024601ed1798e096d99d192903fb5d16355``.  That project is licensed
under BSD-0-Clause; see ``THIRD_PARTY_NOTICES.md``.  The same byte stream was
verified against the paired encrypted/decrypted ZD1200 10.5.1.0.282 archive.

The primitive is not encryption/authentication.  It exists solely to transform
a file the operator already obtained from Ruckus into its local gzip-TAR
payload.  It performs no networking and writes output atomically.
"""

from __future__ import annotations

import argparse
import os
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO


# Explicit little-endian byte order makes upstream's native ``Q`` processing
# deterministic on Linux x86-64, Linux ARM64, and in a future browser worker.
_WORD = struct.Struct("<Q")
_INITIAL_XOR, _XOR_FLIP = struct.unpack(
    "<QQ",
    b")\x1aB\x05\xbd,\xd6\xf25\xad\xb8\xe0?T\xc58"
)


def decrypt_stream(source: BinaryIO, destination: BinaryIO, *, chunk_size: int = 1024 * 1024) -> int:
    """Decrypt a TAC stream, returning the number of output bytes written."""
    if chunk_size < 8:
        raise ValueError("chunk_size must be at least eight bytes")
    chunk_size -= chunk_size % 8
    previous_input = 0
    xor_value = _INITIAL_XOR
    written = 0
    while True:
        encrypted = source.read(chunk_size)
        if not encrypted:
            return written
        if len(encrypted) % 8:
            raise ValueError("TAC input length is not a multiple of eight bytes")
        output = bytearray(len(encrypted))
        for offset in range(0, len(encrypted), 8):
            current_input, = _WORD.unpack_from(encrypted, offset)
            _WORD.pack_into(output, offset, previous_input ^ xor_value ^ current_input)
            xor_value ^= _XOR_FLIP
            previous_input = current_input
        destination.write(output)
        written += len(output)


def decrypt_bytes(encrypted: bytes) -> bytes:
    """Convenience wrapper for small tests and browser-core equivalence tests."""
    from io import BytesIO

    source = BytesIO(encrypted)
    destination = BytesIO()
    decrypt_stream(source, destination)
    return destination.getvalue()


def decrypt_file(source_path: Path, destination_path: Path) -> int:
    """Decrypt to a sibling temporary file, replacing destination on success."""
    if source_path.resolve() == destination_path.resolve():
        raise ValueError("source and destination must be different files")
    if source_path.stat().st_size % 8:
        raise ValueError("TAC input length is not a multiple of eight bytes")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent
    )
    try:
        with source_path.open("rb") as source, os.fdopen(descriptor, "wb") as destination:
            written = decrypt_stream(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        # TAC word-aligns its payload. The last decoded byte carries the
        # number of trailing alignment bytes in its low nibble, so there is
        # no need to scan the complete gzip member to find its endpoint.
        if written:
            with open(temporary_name, "r+b") as destination:
                destination.seek(-1, os.SEEK_END)
                padding = destination.read(1)[0] & 0x0F
                if padding:
                    if padding > written:
                        raise ValueError("TAC padding exceeds decrypted output length")
                    written -= padding
                    destination.truncate(written)
                    destination.flush()
                    os.fsync(destination.fileno())
        os.replace(temporary_name, destination_path)
        return written
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="encrypted Ruckus TAC-format input")
    parser.add_argument("destination", type=Path, help="local decrypted gzip-TAR output")
    args = parser.parse_args()
    written = decrypt_file(args.source, args.destination)
    print(f"decrypted {args.source} -> {args.destination} ({written} bytes)")


if __name__ == "__main__":
    main()
