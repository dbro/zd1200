#!/usr/bin/env python3
"""Fail if tracked files violate the source-only/public-credential boundary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FORBIDDEN_SUFFIXES = frozenset({".img", ".bl7", ".ko", ".qcow2", ".tgz", ".tar", ".zip", ".pem", ".key", ".p12", ".pfx"})
FORBIDDEN_PATHS = frozenset({
    "zd-dropbear2222/dropbear",
    "zd-dropbear2222/dropbearconvert",
    "zd-dropbear2222/dropbearkey",
    "zd-dropbear2222/sftp-server",
})
PRIVATE_MARKERS = (
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN " + b"OPENSSH PRIVATE KEY-----",
)


def tracked_files(root: Path = ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [Path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item]


def violations(root: Path = ROOT) -> list[str]:
    found: list[str] = []
    for relative in tracked_files(root):
        portable = relative.as_posix()
        if relative.suffix.lower() in FORBIDDEN_SUFFIXES:
            found.append(f"forbidden tracked artifact suffix: {portable}")
            continue
        if portable in FORBIDDEN_PATHS:
            found.append(f"unprovenanced diagnostic payload: {portable}")
            continue
        try:
            contents = (root / relative).read_bytes()
        except OSError as exc:
            found.append(f"cannot inspect tracked file {portable}: {exc}")
            continue
        if any(marker in contents for marker in PRIVATE_MARKERS):
            found.append(f"private-key marker in tracked file: {portable}")
    return found


def main() -> None:
    found = violations()
    if found:
        print("repository hygiene check failed:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in found), file=sys.stderr)
        raise SystemExit(1)
    print("repository hygiene check passed")


if __name__ == "__main__":
    main()
