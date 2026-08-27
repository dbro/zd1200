#!/usr/bin/env python3
"""Verify a decrypted ZoneDirector archive against an exact release manifest.

This command is read-only. It does not decrypt, extract, patch, or redistribute
the selected vendor archive. Its path/link checks are deliberately stricter
than the current shell extractor so future builders have one fail-closed
preflight step to reuse.
"""

from __future__ import annotations

import argparse
import hashlib
import posixpath
import tarfile
from pathlib import Path, PurePosixPath

from release_manifest import RELEASES, ReleaseManifest, release_by_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe archive member path {name!r}")
    normalized = posixpath.normpath(name).removeprefix("./")
    if not normalized or normalized == "." or normalized.startswith("../"):
        raise ValueError(f"unsafe archive member path {name!r}")
    return normalized.rstrip("/")


def safe_link_target(member_name: str, target: str) -> None:
    if not target or PurePosixPath(target).is_absolute():
        raise ValueError(f"unsafe archive link target {target!r}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(member_name), target))
    if resolved == ".." or resolved.startswith("../"):
        raise ValueError(f"unsafe archive link target {target!r}")


def parse_metadata(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("metadata is not ASCII") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            raise ValueError("metadata has an invalid line")
        values[key] = value
    return values


def verify_encrypted_input(path: Path, release: ReleaseManifest) -> str:
    """Verify only the exact opaque input hash before local decryption."""
    if release.encrypted_sha256 is None:
        raise ValueError(f"{release.release_id}: no encrypted input hash is recorded")
    actual_hash = sha256_file(path)
    if actual_hash != release.encrypted_sha256:
        raise ValueError(
            f"{release.release_id}: encrypted SHA-256 mismatch: {actual_hash}"
        )
    return actual_hash


def verify_decrypted_archive(path: Path, release: ReleaseManifest) -> dict[str, object]:
    """Check hash, safe TAR structure, required paths, and signed metadata."""
    if release.decrypted_sha256 is None:
        raise ValueError(f"{release.release_id}: no decrypted archive hash is recorded")
    actual_hash = sha256_file(path)
    if actual_hash != release.decrypted_sha256:
        raise ValueError(
            f"{release.release_id}: decrypted SHA-256 mismatch: {actual_hash}"
        )
    names: set[str] = set()
    metadata_raw: bytes | None = None
    try:
        archive = tarfile.open(path, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise ValueError(f"{release.release_id}: not a readable gzip TAR archive") from exc
    with archive:
        for member in archive:
            name = safe_member_name(member.name)
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f"unsafe archive member type for {member.name!r}")
            if member.issym() or member.islnk():
                safe_link_target(name, member.linkname)
            if name in names:
                raise ValueError(f"duplicate archive member path {name!r}")
            names.add(name)
            if name == "metadata":
                if not member.isfile():
                    raise ValueError("metadata is not a regular file")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("cannot read metadata")
                metadata_raw = stream.read()
    missing = [item for item in release.required_paths if item.rstrip("/") not in names]
    if missing:
        raise ValueError(f"{release.release_id}: archive lacks required paths: {', '.join(missing)}")
    if metadata_raw is None:
        raise ValueError(f"{release.release_id}: archive lacks metadata")
    metadata = parse_metadata(metadata_raw)
    mismatch = [
        f"{key}={metadata.get(key)!r}"
        for key, expected in release.metadata.items()
        if metadata.get(key) != expected
    ]
    if mismatch:
        raise ValueError(
            f"{release.release_id}: metadata mismatch: " + ", ".join(mismatch)
        )
    return {
        "release_id": release.release_id,
        "sha256": actual_hash,
        "members": len(names),
        "metadata": metadata,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="encrypted input or decrypted .img.tgz archive")
    parser.add_argument("--release", default="zd1200_10_5_1_0_282")
    parser.add_argument(
        "--encrypted", action="store_true", help="verify opaque encrypted input only"
    )
    args = parser.parse_args()
    release = release_by_id(args.release, RELEASES)
    if args.encrypted:
        digest = verify_encrypted_input(args.archive, release)
        print(f"verified encrypted {release.release_id}: SHA-256 {digest}")
        return
    result = verify_decrypted_archive(args.archive, release)
    print(
        f"verified {result['release_id']}: {result['members']} members, "
        f"SHA-256 {result['sha256']}"
    )


if __name__ == "__main__":
    main()
