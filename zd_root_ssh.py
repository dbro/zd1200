#!/usr/bin/env python3
"""Validate one OpenSSH public key for the optional vendor-Dropbear listener."""

from __future__ import annotations

import base64
import binascii
import re
import struct
import sys


# Verified from the 10.5.1 vendor Dropbear binary. In particular, it does not
# advertise ssh-ed25519, so rejecting that otherwise common modern key avoids
# a configuration which would silently never authenticate.
ALGORITHMS = frozenset({
    "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
})
COMMENT_RE = re.compile(r"^[^\x00-\x1f\x7f]*$")


def validate_public_key(value: str) -> str:
    if "\n" in value or "\r" in value or not value:
        raise ValueError("public key must be one non-empty OpenSSH line")
    fields = value.split(None, 2)
    if len(fields) not in {2, 3} or fields[0] not in ALGORITHMS:
        raise ValueError("public key must use a supported OpenSSH key algorithm")
    try:
        decoded = base64.b64decode(fields[1], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("public key has invalid base64 data") from exc
    if len(decoded) < 16:
        raise ValueError("public key data is unexpectedly short")
    algorithm_bytes = fields[0].encode("ascii")
    if len(decoded) < 4 or struct.unpack_from(">I", decoded)[0] != len(algorithm_bytes) \
            or decoded[4:4 + len(algorithm_bytes)] != algorithm_bytes:
        raise ValueError("public key data does not match its declared algorithm")
    if len(fields) == 3 and not COMMENT_RE.fullmatch(fields[2]):
        raise ValueError("public-key comment contains a control character")
    return " ".join(fields)


def main() -> None:
    try:
        print(validate_public_key(sys.argv[1]))
    except IndexError:
        raise SystemExit("usage: zd_root_ssh.py '<OpenSSH public key>'")
    except ValueError as exc:
        raise SystemExit(f"invalid root SSH public key: {exc}") from exc


if __name__ == "__main__":
    main()
