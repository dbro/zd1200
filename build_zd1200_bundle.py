#!/usr/bin/env python3
"""Build a deterministic, local Docker bundle from an exact ZD download.

The input may be the original TAC-encrypted download or its decrypted gzip-TAR
form. Vendor bytes remain local: the output ZIP is written only to the path
chosen by the operator. The current runtime profile transforms the ZD kernel;
R600 AP BL7 repacking is reported as unavailable rather than guessed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from patch_binary_artifact import apply_rules, extract_artifact, rebuild_artifact, rules_for_artifact
from release_manifest import RELEASES, ReleaseManifest, release_by_id
from ruckus_tac_decrypt import decrypt_file
from verify_release_archive import verify_decrypted_archive, verify_encrypted_input


REPO_FILES = (
    ".dockerignore", ".env.example", "Dockerfile", "docker-compose.yml",
    "boot-initrd-handoff", "boot-initrd-init", "boot-initrd-inittab",
    "limit-process-cpu.py", "make-runtime-initrd.sh", "make-synthetic-cf.py",
    "patch_binary_artifact.py", "run-zd1200-qemu.sh", "run-zd1200-web.sh",
    "write-boarddata.py", "zd-controller-wrapper.sh", "README.md",
    "ZD1200-LAB-GUIDE.md", "VALIDATION.md", "binary_patch_catalog.json",
    "binary_patch_catalog.py", "binary_patch_catalog.schema.json",
    "release_manifest.json", "release_manifest.py", "release_manifest.schema.json",
    "verify_release_archive.py", "ruckus_tac_decrypt.py", "ruckus_bl7.py", "LICENSE",
    "THIRD_PARTY_NOTICES.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    return info


def make_payload(source_dir: Path, destination: Path) -> None:
    """Create a reproducible payload TAR while preserving vendor symlinks."""
    with destination.open("wb") as raw, gzip.GzipFile(
        filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0
    ) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
        for name in ("firmwares", "aidfs", "ap-models", "file_list.txt"):
            archive.add(source_dir / name, arcname=name, recursive=True, filter=_tar_filter)


def copy_tree_safe(source: Path, destination: Path) -> None:
    for relative in REPO_FILES:
        source_file = source / relative
        if not source_file.is_file():
            raise ValueError(f"repository file missing: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)
    (destination / "host").mkdir()
    for relative in ("host/zd1200-bridge", "host/zd1200-bridge.service", "host/zd1200-bridge.env.example"):
        source_file = source / relative
        target = destination / relative
        shutil.copy2(source_file, target)


def patch_kernel(source: Path, destination: Path, vmlinux: Path | None = None) -> list[str]:
    source_bytes = source.read_bytes()
    messages: list[str] = []
    payload, context = extract_artifact(source_bytes, "zd1200_kernel_elf", report=messages.append)
    if vmlinux is not None:
        vmlinux.write_bytes(payload)
    patched_payload, changed = apply_rules(
        payload, rules_for_artifact("zd1200_kernel_elf"), report=messages.append
    )
    result = rebuild_artifact(source_bytes, patched_payload, "zd1200_kernel_elf", context)
    destination.write_bytes(result)
    messages.append(f"kernel output SHA-256: {hashlib.sha256(result).hexdigest()}")
    messages.append(f"kernel changed: {'yes' if changed else 'already patched'}")
    return messages


def extract_verified_archive(archive_path: Path, staging: Path, release: ReleaseManifest) -> Path:
    verify_decrypted_archive(archive_path, release)
    staging.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        # Python 3.12+'s data filter rejects links/devices that escape the
        # extraction root. verify_decrypted_archive has already checked the
        # member names and link targets for older Python implementations.
        archive.extractall(staging, filter="data")
    metadata = staging / "metadata"
    if not metadata.is_file():
        raise ValueError("verified archive did not extract a top-level metadata file")
    return staging


def write_first_readme(path: Path, release: ReleaseManifest) -> None:
    path.write_text(
        f"""# ZD1200 local bundle — {release.version}.{release.build}\n\n
This bundle was built locally from an operator-supplied Ruckus download.\n
No firmware is fetched at runtime. Review `.env.example`, attach only a
dedicated isolated Ethernet adapter, then run `docker compose build` and
`docker compose up -d`. See `README.md` and `VALIDATION.md`.\n\n
## Important scope note\n\n
The ZD kernel compatibility patches were applied and verified. The AP
payload is preserved from the vendor archive but the R600 BL7 repacker
is not yet implemented; the R600 mesh-repair option is therefore not
applied by this bundle. Do not claim mesh validation from this bundle
until the report says the AP payload was transformed and the physical
mesh test in `VALIDATION.md` passes.\n""",
        encoding="utf-8",
    )


def build_bundle(input_path: Path, output_path: Path, *, release: ReleaseManifest, repo_root: Path) -> dict[str, object]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zd1200-bundle-") as temporary:
        temporary_root = Path(temporary)
        with input_path.open("rb") as stream:
            is_gzip = stream.read(3) == b"\x1f\x8b\x08"
        if is_gzip:
            decrypted = input_path
            input_kind = "decrypted_gzip_tar"
        else:
            verify_encrypted_input(input_path, release)
            decrypted = temporary_root / "decrypted.img.tgz"
            decrypt_file(input_path, decrypted)
            input_kind = "encrypted_tac"
        source_dir = extract_verified_archive(decrypted, temporary_root / "vendor", release)

        bundle = temporary_root / "bundle"
        bundle.mkdir()
        copy_tree_safe(repo_root, bundle)
        image = bundle / "image"
        image.mkdir()
        shutil.copy2(source_dir / "restoreinitramfs.gz", image / "restoreinitramfs.gz")
        shutil.copy2(source_dir / "rootfs.i386.ext2.director1200.img", image / "rootfs.ext2")
        patch_messages = patch_kernel(
            source_dir / "bzImage", image / "bzImage", image / "vmlinux"
        )
        make_payload(source_dir, image / "zd1051-payload.tar.gz")

        report: dict[str, object] = {
            "manifest_version": 1,
            "release_id": release.release_id,
            "input_kind": input_kind,
            "input_sha256": sha256_file(input_path),
            "decrypted_archive_sha256": sha256_file(decrypted),
            "artifacts": {
                "zd1200_kernel_elf": {
                    "status": "patched",
                    "rules": [rule.name for rule in rules_for_artifact("zd1200_kernel_elf")],
                    "output_sha256": sha256_file(image / "bzImage"),
                    "messages": patch_messages,
                },
                "r600_wlan_ko": {
                    "status": "not_applied",
                    "reason": "BL7 AP payload repacker is not implemented",
                },
            },
            "warnings": [
                "R600 AP mesh receive repair is not included in this bundle.",
                "Run the physical AP validation matrix before describing this release as supported.",
            ],
        }
        write_first_readme(bundle / "README-FIRST.md", release)
        (bundle / "build-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        temporary_zip = output_path.with_suffix(output_path.suffix + ".tmp")
        try:
            # Most bundle bytes are already compressed firmware archives or
            # filesystem images. Storing them avoids an expensive second
            # compression pass and makes large builds practical on laptops.
            with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_STORED) as archive:
                for path in sorted(bundle.rglob("*")):
                    if path.is_dir():
                        continue
                    relative = path.relative_to(bundle).as_posix()
                    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_STORED
                    info.external_attr = (path.stat().st_mode & 0o777) << 16
                    with path.open("rb") as source, archive.open(info, "w") as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
            os.replace(temporary_zip, output_path)
        except BaseException:
            temporary_zip.unlink(missing_ok=True)
            raise
        report["output_zip_sha256"] = sha256_file(output_path)
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="encrypted or decrypted ZD1200 archive")
    parser.add_argument("output", type=Path, help="generated bundle ZIP")
    parser.add_argument("--release", default="zd1200_10_5_1_0_282")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"input not found: {args.input}")
    report = build_bundle(
        args.input, args.output, release=release_by_id(args.release, RELEASES), repo_root=args.repo_root
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
