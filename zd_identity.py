#!/usr/bin/env python3
"""Create or load a persistent, locally administered ZD board identity.

The synthetic CF board-data records and QEMU's e1000e NIC must use the same
base MAC.  A generated identity is retained in the Docker state volume so a
container recreation neither changes the controller identity nor collides with
another freshly generated bundle on the same LAN.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path


SERIAL_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")
IDENTITY_FILE = "board-identity.env"


def validate_serial(serial: str) -> str:
    if not SERIAL_RE.fullmatch(serial):
        raise ValueError("serial must contain 1..15 letters, digits, '_' or '-'")
    return serial


def parse_mac(value: str) -> bytes:
    parts = value.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        raise ValueError("MAC must use six colon-separated hexadecimal octets")
    try:
        mac = bytes(int(part, 16) for part in parts)
    except ValueError as exc:
        raise ValueError("MAC must use six colon-separated hexadecimal octets") from exc
    if mac[0] & 1 or not mac[0] & 2:
        raise ValueError("MAC must be a locally administered unicast address")
    mac2 = (int.from_bytes(mac, "big") + 1).to_bytes(6, "big", signed=False)
    if mac2[0] & 1 or not mac2[0] & 2:
        raise ValueError("MAC leaves no valid locally administered unicast MAC2")
    return mac


def format_mac(mac: bytes) -> str:
    return mac.hex(":")


def generate_identity() -> tuple[str, str]:
    # Twelve decimal digits match the conventional serial presentation without
    # encoding any host or user information.  02: prefix is local/unicast.
    serial = f"{secrets.randbelow(10**12):012d}"
    mac = bytes([0x02]) + secrets.token_bytes(5)
    return serial, format_mac(mac)


def parse_identity(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read identity file {path}: {exc}") from exc
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key in values or key not in {"ZD_SERIAL", "ZD_MAC1"}:
            raise ValueError(f"invalid identity file {path}")
        values[key] = value
    if set(values) != {"ZD_SERIAL", "ZD_MAC1"}:
        raise ValueError(f"invalid identity file {path}")
    return validate_serial(values["ZD_SERIAL"]), format_mac(parse_mac(values["ZD_MAC1"]))


def write_identity(path: Path, serial: str, mac: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(f"ZD_SERIAL={serial}\nZD_MAC1={mac}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def resolve_identity(state_dir: Path, serial_override: str, mac_override: str) -> tuple[str, str, str]:
    if bool(serial_override) != bool(mac_override):
        raise ValueError("set both ZD_SERIAL and ZD_MAC1, or leave both unset")
    if serial_override:
        return validate_serial(serial_override), format_mac(parse_mac(mac_override)), "operator override"
    path = state_dir / IDENTITY_FILE
    if path.exists():
        serial, mac = parse_identity(path)
        return serial, mac, "persistent state"
    serial, mac = generate_identity()
    write_identity(path, serial, mac)
    return serial, mac, "generated and persisted"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--serial", default="")
    parser.add_argument("--mac", default="")
    args = parser.parse_args()
    try:
        serial, mac, source = resolve_identity(args.state_dir, args.serial, args.mac)
    except ValueError as exc:
        parser.error(str(exc))
    # Tab-separated, validated values allow the caller to avoid sourcing a
    # state file as shell code.
    print(f"{serial}\t{mac}\t{source}")


if __name__ == "__main__":
    main()
