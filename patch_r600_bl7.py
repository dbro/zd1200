#!/usr/bin/env python3
"""Patch an unsigned ap-11n-scorpion BL7 image without modifying the input file.

The R600 rootfs uses Ruckus' historical LZMA SquashFS format.  Supply the
matching ``unsquashfs`` and ``mksquashfs`` binaries from the GPL-2.0
``ruckus_ap_firmware_mod`` project (or equivalent compatible builds).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile

from patch_binary_artifact import apply_rules, rules_for_artifact
from ruckus_bl7 import parse_bl7


def _run(tool: Path, *args: str) -> None:
    subprocess.run([str(tool), *args], check=True)


def patch_image(
    source: Path,
    destination: Path,
    *,
    unsquashfs: Path,
    mksquashfs: Path,
) -> list[str]:
    image = parse_bl7(source.read_bytes())
    messages: list[str] = [f"input version: {image.version}"]
    with tempfile.TemporaryDirectory(prefix="r600-bl7-") as temporary:
        root = Path(temporary)
        rootfs_image = root / "rootfs.img"
        rootfs_dir = root / "rootfs"
        rebuilt_rootfs = root / "rootfs.patched.img"
        rootfs_image.write_bytes(image.rootfs)
        _run(unsquashfs, "-dest", str(rootfs_dir), str(rootfs_image))
        modules = sorted(rootfs_dir.glob("lib/modules/*/net/wlan.ko"))
        if len(modules) != 1:
            raise ValueError(f"expected exactly one ap-11n-scorpion wlan.ko, found {len(modules)}")
        module = modules[0]
        original = module.read_bytes()
        patched, changed = apply_rules(
            original, rules_for_artifact("ap_11n_scorpion_wlan_ko"), report=messages.append
        )
        if not changed:
            messages.append("ap-11n-scorpion wlan.ko was already patched")
        module.write_bytes(patched)
        _run(mksquashfs, str(rootfs_dir), str(rebuilt_rootfs), "-all-root", "-noappend")
        rebuilt = image.rebuild(rootfs=rebuilt_rootfs.read_bytes())
        temporary_output = destination.with_suffix(destination.suffix + ".tmp")
        temporary_output.write_bytes(rebuilt)
        temporary_output.replace(destination)
    messages.append(f"output bytes: {destination.stat().st_size}")
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--unsquashfs", type=Path, required=True)
    parser.add_argument("--mksquashfs", type=Path, required=True)
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"input not found: {args.input}")
    if args.input.resolve() == args.output.resolve():
        parser.error("output must differ from input")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for message in patch_image(
        args.input, args.output, unsquashfs=args.unsquashfs, mksquashfs=args.mksquashfs
    ):
        print(message)


if __name__ == "__main__":
    main()
