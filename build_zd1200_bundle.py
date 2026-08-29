#!/usr/bin/env python3
"""Build a deterministic, local Docker bundle from an exact ZD download.

The input may be the original TAC-encrypted download or its decrypted gzip-TAR
form. Vendor bytes remain local: the output ZIP is written only to the path
chosen by the operator. The current runtime profile transforms the ZD kernel
and, when requested, the shared ``ap-11n-scorpion`` AP platform payload.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

from patch_binary_artifact import apply_rules, extract_artifact, rebuild_artifact, rules_for_artifact
from patch_r600_bl7 import patch_image as patch_r600_image
from ruckus_bl7 import parse_bl7
from release_manifest import RELEASES, ReleaseManifest, release_by_id, release_by_input_sha256
from ruckus_tac_decrypt import decrypt_file
from verify_release_archive import verify_decrypted_archive, verify_encrypted_input


REPO_FILES = (
    ".dockerignore", ".env.example", "Dockerfile", "docker-compose.yml",
    "boot-initrd-handoff", "boot-initrd-init", "boot-initrd-inittab",
    "make-boot-initrd.sh", "pivot-exec.S", "zd-controller-wrapper.sh",
    "zd-memory-snapshot.sh",
    "limit-process-cpu.py", "make-runtime-initrd.sh", "make-synthetic-cf.py",
    "patch_binary_artifact.py", "run-zd1200-qemu.sh", "run-zd1200-web.sh",
    "prepare-compose-runtime.sh",
    "write-boarddata.py", "zd_identity.py", "zd_root_ssh.py", "zd-controller-wrapper.sh",
    "zd-healthcheck.sh", "zd-memory-snapshot.sh", "README.md",
    "ZD1200-LAB-GUIDE.md", "VALIDATION.md", "binary_patch_catalog.json",
    "binary_patch_catalog.py", "binary_patch_catalog.schema.json",
    "release_manifest.json", "release_manifest.py", "release_manifest.schema.json",
    "verify_release_archive.py", "ruckus_tac_decrypt.py", "ruckus_bl7.py",
    "patch_r600_bl7.py", "LICENSE",
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


def make_payload(source_dir: Path, destination: Path, release: ReleaseManifest) -> None:
    """Create a reproducible release payload TAR preserving vendor symlinks.

    10.1.2 stores its web assets in the root filesystem rather than a separate
    ``aidfs`` directory.  The small release-info member lets the boot handoff
    distinguish that deliberate layout difference from a truncated payload.
    """
    names = ["firmwares", "ap-models", "file_list.txt"]
    if (source_dir / "aidfs").is_dir():
        names.append("aidfs")
    release_info = (
        f"RELEASE_ID={release.release_id}\n"
        f"VERSION={release.version}\n"
        f"BUILD={release.build}\n"
        f"HAS_AIDFS={1 if 'aidfs' in names else 0}\n"
        f"RUNTIME_FTP_BOOTSTRAP={release.features['runtime_ftp_bootstrap']}\n"
        f"RUNTIME_ROOT_SSH={release.features['runtime_root_ssh']}\n"
    ).encode("ascii")
    with destination.open("wb") as raw, gzip.GzipFile(
        filename="", fileobj=raw, mode="wb", compresslevel=9, mtime=0
    ) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
        info = tarfile.TarInfo("release-info")
        info.size = len(release_info)
        info.mode = 0o644
        info.mtime = 0
        archive.addfile(info, io.BytesIO(release_info))
        for name in names:
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
    for relative in (
        "host/zd1200-bridge", "host/zd1200-bridge.service",
        "host/zd1200-bridge-watch.service",
        "host/zd1200-bridge.env.example",
    ):
        source_file = source / relative
        target = destination / relative
        shutil.copy2(source_file, target)


def make_boot_initrd(bundle: Path) -> None:
    """Build the derived boot initramfs expected by run-zd1200-web.sh."""
    # Keep this command's stdout reserved for the machine-readable final build
    # report. Toolchain warnings/progress remain visible to an interactive
    # operator on stderr.
    subprocess.run(
        ["bash", "make-boot-initrd.sh"], cwd=bundle, check=True,
        stdout=sys.stderr, stderr=sys.stderr,
    )


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


SCORPION_VALIDATED_MODELS = frozenset({"r600"})
SCORPION_EXPERIMENTAL_MODELS = frozenset({"r500", "r310", "t300", "t300e", "t301n", "t301s"})
SCORPION_IMAGE_NAMES = frozenset({"rcks_fw.bl7", "rcks_fw.bl7.bkup"})


def scorpion_payload_paths(source_dir: Path) -> tuple[list[Path], set[str]]:
    """Find platform aliases that resolve to R600's exact vendor BL7 files."""
    firmware_root = source_dir / "firmwares"
    r600_paths = sorted(
        path for path in (firmware_root / "r600").glob("*/*")
        if path.name in SCORPION_IMAGE_NAMES and path.is_file()
    )
    if not r600_paths:
        raise ValueError("vendor payload contains no R600 ap-11n-scorpion BL7 image")
    targets = {path.resolve() for path in r600_paths}
    aliases = {"r600"}
    for path in firmware_root.glob("*/*/*"):
        if path.name in SCORPION_IMAGE_NAMES and path.is_file() and path.resolve() in targets:
            aliases.add(path.relative_to(firmware_root).parts[0].lower())
    return sorted(targets), aliases


def patch_scorpion_payload(
    source_dir: Path,
    *,
    unsquashfs: Path | None = None,
    mksquashfs: Path | None = None,
    override: Path | None = None,
) -> tuple[list[str], set[str]]:
    """Patch the shared ap-11n-scorpion BL7 payload and its control metadata."""
    candidates, aliases = scorpion_payload_paths(source_dir)
    messages: list[str] = []
    if override is not None:
        replacement = override.read_bytes()
        parse_bl7(replacement)
        for path in candidates:
            path.write_bytes(replacement)
            messages.append(f"replaced ap-11n-scorpion payload {path.relative_to(source_dir)} from local UI image")
        messages.extend(update_scorpion_control_files(source_dir, aliases, len(replacement)))
        return messages, aliases
    if unsquashfs is None or mksquashfs is None:
        raise ValueError("ap-11n-scorpion payload patching requires SquashFS tools or --r600-bl7")
    for path in candidates:
        temporary = path.with_suffix(path.suffix + ".patched")
        messages.append(f"patching ap-11n-scorpion payload {path.relative_to(source_dir)}")
        messages.extend(
            patch_r600_image(path, temporary, unsquashfs=unsquashfs, mksquashfs=mksquashfs)
        )
        temporary.replace(path)
    messages.extend(update_scorpion_control_files(source_dir, aliases, candidates[0].stat().st_size))
    return messages, aliases


def update_scorpion_control_files(source_dir: Path, models: set[str], image_size: int) -> list[str]:
    """Update controls only for models resolving to the shared platform image."""
    controls = sorted({
        path.resolve()
        for model in models
        for path in (source_dir / "firmwares" / model).glob("*/*_cntrl.rcks")
        if path.is_file()
    })
    if not controls:
        raise ValueError("vendor payload contains no ap-11n-scorpion control file")
    messages: list[str] = []
    for control in controls:
        contents = control.read_text(encoding="ascii")
        updated, count = re.subn(r"(?m)^\d+$", str(image_size), contents)
        if count != 2:
            raise ValueError(
                f"expected two image-size fields in {control.relative_to(source_dir)}, found {count}"
            )
        control.write_text(updated, encoding="ascii")
        messages.append(
            f"updated ap-11n-scorpion control sizes in {control.relative_to(source_dir)} to {image_size} bytes"
        )
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


def write_first_readme(
    path: Path, release: ReleaseManifest, *, scorpion_models: set[str] | None
) -> None:
    scope_note = (
        "The ZD kernel and ap-11n-scorpion AP payload compatibility patches were applied "
        f"for: {', '.join(sorted(scorpion_models))}. R600 is validated; all other "
        "listed models are experimental."
        if scorpion_models is not None
        else
        "The ZD kernel compatibility patches were applied. The ap-11n-scorpion AP payload "
        "was not patched because SquashFS tools were not selected."
    )
    path.write_text(
        f"""# ZD1200 local bundle — {release.version}.{release.build}\n\n
This bundle was built locally from an operator-supplied Ruckus download.\n
No firmware is fetched at runtime. Review `.env.example` and choose the
recommended dedicated-adapter profile, or the advanced existing-host-bridge
profile for a deliberately shared LAN, then run `docker compose build` and
`docker compose up -d`. See `README.md` and `VALIDATION.md`.\n\n
## Important scope note\n\n
{scope_note} Do not claim mesh validation from this bundle until the report
says the AP payload was transformed and the physical mesh test in
`VALIDATION.md` passes.\n""",
        encoding="utf-8",
    )


def build_bundle(
    input_path: Path,
    output_path: Path,
    *,
    release: ReleaseManifest,
    repo_root: Path,
    unsquashfs: Path | None = None,
    mksquashfs: Path | None = None,
    r600_bl7: Path | None = None,
) -> dict[str, object]:
    if r600_bl7 is not None and (unsquashfs is not None or mksquashfs is not None):
        raise ValueError("--r600-bl7 cannot be combined with SquashFS tool paths")
    if (unsquashfs is None) != (mksquashfs is None):
        raise ValueError("--unsquashfs and --mksquashfs must be supplied together")
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
        make_boot_initrd(bundle)
        if r600_bl7 is not None or (unsquashfs is not None and mksquashfs is not None):
            scorpion_messages, scorpion_models = patch_scorpion_payload(
                source_dir, unsquashfs=unsquashfs, mksquashfs=mksquashfs, override=r600_bl7
            )
            scorpion_status: dict[str, object] = {
                "status": "patched",
                "models": sorted(scorpion_models),
                "validated_models": sorted(scorpion_models & SCORPION_VALIDATED_MODELS),
                "experimental_models": sorted(scorpion_models & SCORPION_EXPERIMENTAL_MODELS),
                "messages": scorpion_messages,
            }
            scorpion_warnings = [
                f"{model} uses the shared ap-11n-scorpion payload but is experimental; "
                "complete model-specific firmware-delivery and mesh validation before production use."
                for model in sorted(scorpion_models & SCORPION_EXPERIMENTAL_MODELS)
            ]
        else:
            scorpion_models = None
            scorpion_status = {
                "status": "not_applied",
                "reason": "BL7 AP payload repacker not selected; provide both SquashFS tool paths",
            }
            scorpion_warnings = [
                "ap-11n-scorpion AP mesh receive repair is not included in this bundle.",
                "Run the physical AP validation matrix before describing this release as supported.",
            ]
        make_payload(source_dir, image / "zd-payload.tar.gz", release)

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
            "ap_11n_scorpion_wlan_ko": scorpion_status,
            },
            "warnings": scorpion_warnings,
        }
        write_first_readme(
            bundle / "README-FIRST.md", release,
            scorpion_models=scorpion_models,
        )
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
    parser.add_argument(
        "--release",
        help="exact release id; normally omitted so the input SHA-256 selects it",
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--unsquashfs", type=Path, help="Ruckus-compatible unsquashfs for R600 payload patching")
    parser.add_argument("--mksquashfs", type=Path, help="Ruckus-compatible mksquashfs for R600 payload patching")
    parser.add_argument("--r600-bl7", type=Path, help="local unsigned, model-matching R600 BL7 override")
    args = parser.parse_args()
    if not args.input.is_file():
        parser.error(f"input not found: {args.input}")
    try:
        release = (
            release_by_id(args.release, RELEASES)
            if args.release
            else release_by_input_sha256(sha256_file(args.input), RELEASES)
        )
        report = build_bundle(
            args.input,
            args.output,
            release=release,
            repo_root=args.repo_root,
            unsquashfs=args.unsquashfs,
        mksquashfs=args.mksquashfs,
        r600_bl7=args.r600_bl7,
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
