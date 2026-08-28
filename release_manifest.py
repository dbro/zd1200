"""Load exact-build metadata without including vendor materials.

The manifest is deliberately separate from binary patch tuples: release
selection identifies archive layout and required runtime behavior, while the
patch catalog identifies how a selected artifact may be changed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from binary_patch_catalog import ARTIFACTS


MANIFEST_PATH = Path(__file__).with_suffix(".json")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){3}$")
ROOT_FIELDS = frozenset({"manifest_version", "releases"})
RELEASE_FIELDS = frozenset(
    {
        "release_id", "product", "version", "build", "support_status",
        "encrypted_sha256", "decrypted_sha256", "archive_format", "metadata",
        "required_paths", "artifact_ids", "features",
    }
)
RUNTIME_FTP_BOOTSTRAP_VALUES = frozenset({"vendor_state", "not_required"})


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    product: str
    version: str
    build: int
    support_status: str
    encrypted_sha256: str | None
    decrypted_sha256: str | None
    archive_format: str
    metadata: dict[str, str]
    required_paths: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    features: dict[str, str]


def _fields(value: dict[str, Any], allowed: frozenset[str], kind: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"release manifest {kind} has unknown fields: {', '.join(unknown)}")


def _string(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"release manifest {field!r} must be a non-empty string")
    return result


def _optional_hash(value: dict[str, Any], field: str) -> str | None:
    result = value.get(field)
    if result is None:
        return None
    if not isinstance(result, str) or not SHA256_RE.fullmatch(result):
        raise ValueError(f"release manifest {field!r} must be a lowercase SHA-256")
    return result


def load_release_manifest(path: Path = MANIFEST_PATH) -> tuple[ReleaseManifest, ...]:
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load release manifest {path}: {exc}") from exc
    if not isinstance(source, dict):
        raise ValueError("release manifest root must be an object")
    _fields(source, ROOT_FIELDS, "root")
    if type(source.get("manifest_version")) is not int or source["manifest_version"] != 1:
        raise ValueError("unsupported or missing manifest_version")
    releases = source.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError("release manifest releases must be a non-empty array")

    parsed: list[ReleaseManifest] = []
    ids: set[str] = set()
    for item in releases:
        if not isinstance(item, dict):
            raise ValueError("release manifest release must be an object")
        _fields(item, RELEASE_FIELDS, "release")
        release_id = _string(item, "release_id")
        if not IDENTIFIER_RE.fullmatch(release_id) or release_id in ids:
            raise ValueError(f"invalid or duplicate release_id {release_id!r}")
        ids.add(release_id)
        if item.get("product") != "zd1200":
            raise ValueError(f"{release_id}: unsupported product")
        version = _string(item, "version")
        if not VERSION_RE.fullmatch(version):
            raise ValueError(f"{release_id}: invalid version")
        build = item.get("build")
        if type(build) is not int or build < 0:
            raise ValueError(f"{release_id}: build must be a non-negative integer")
        support_status = _string(item, "support_status")
        if support_status not in {"known", "experimental", "awaiting_fixture"}:
            raise ValueError(f"{release_id}: invalid support_status")
        if item.get("archive_format") != "gzip_tar":
            raise ValueError(f"{release_id}: unsupported archive_format")
        metadata = item.get("metadata")
        features = item.get("features")
        required_paths = item.get("required_paths")
        artifact_ids = item.get("artifact_ids")
        if not isinstance(metadata, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()
        ):
            raise ValueError(f"{release_id}: metadata must be a string map")
        if not isinstance(features, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in features.items()
        ):
            raise ValueError(f"{release_id}: features must be a string map")
        ftp_bootstrap = features.get("runtime_ftp_bootstrap")
        if ftp_bootstrap not in RUNTIME_FTP_BOOTSTRAP_VALUES:
            raise ValueError(
                f"{release_id}: runtime_ftp_bootstrap must be one of "
                f"{', '.join(sorted(RUNTIME_FTP_BOOTSTRAP_VALUES))}"
            )
        if not isinstance(required_paths, list) or not required_paths or not all(
            isinstance(value, str) and value for value in required_paths
        ):
            raise ValueError(f"{release_id}: required_paths must be a non-empty string array")
        if not isinstance(artifact_ids, list) or not all(
            isinstance(value, str) and IDENTIFIER_RE.fullmatch(value) for value in artifact_ids
        ):
            raise ValueError(f"{release_id}: artifact_ids must be an identifier array")
        missing = sorted(set(artifact_ids) - set(ARTIFACTS))
        if missing:
            raise ValueError(f"{release_id}: unknown artifact ids: {', '.join(missing)}")
        parsed.append(
            ReleaseManifest(
                release_id, "zd1200", version, build, support_status,
                _optional_hash(item, "encrypted_sha256"),
                _optional_hash(item, "decrypted_sha256"), "gzip_tar",
                dict(metadata), tuple(required_paths), tuple(artifact_ids), dict(features),
            )
        )
    return tuple(parsed)


RELEASES = load_release_manifest()


def release_by_id(
    release_id: str, releases: tuple[ReleaseManifest, ...] = RELEASES
) -> ReleaseManifest:
    """Return one exact release or fail rather than guessing from a filename."""
    for release in releases:
        if release.release_id == release_id:
            return release
    raise ValueError(f"unknown release_id {release_id!r}")
