"""Load the versioned, language-neutral binary patch catalog.

The JSON file is the source of truth so the future browser worker and the
Python reference patcher consume identical artifact IDs and patch tuples.
This module keeps the small dataclass API used by the current patch engine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    description: str
    handler: str


@dataclass(frozen=True)
class PatchRule:
    artifact_id: str
    name: str
    signature_hex: str
    patch_offset: int
    expected_hex: str
    replacement_hex: str | None
    description: str
    rel32_exit: int = 0


CATALOG_PATH = Path(__file__).with_suffix(".json")
SUPPORTED_HANDLERS = frozenset({"raw", "zd1200_bzimage_gzip_elf"})
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
TOP_LEVEL_FIELDS = frozenset({"catalog_version", "artifacts", "patches"})
ARTIFACT_FIELDS = frozenset({"artifact_id", "description", "handler"})
PATCH_FIELDS = frozenset(
    {
        "artifact_id",
        "name",
        "signature_hex",
        "patch_offset",
        "expected_hex",
        "replacement_hex",
        "description",
        "rel32_exit",
    }
)


def _required_string(item: dict[str, Any], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"catalog {field!r} must be a non-empty string")
    return value


def _validate_masked_hex(value: str, field: str) -> int:
    """Validate the catalog's compact/spaced ``00 ?? ff`` byte syntax.

    This intentionally duplicates only the grammar from the patch engine so
    malformed catalogs fail during startup, before a vendor artifact is read.
    """
    compact = "".join(value.split())
    if not compact or len(compact) % 2:
        raise ValueError(f"catalog {field!r} is not a byte pattern")
    for offset in range(0, len(compact), 2):
        token = compact[offset : offset + 2]
        if token != "??" and not re.fullmatch(r"[0-9a-fA-F]{2}", token):
            raise ValueError(
                f"catalog {field!r} has invalid byte token {token!r}"
            )
    return len(compact) // 2


def _check_fields(item: dict[str, Any], allowed: frozenset[str], kind: str) -> None:
    unexpected = sorted(set(item) - allowed)
    if unexpected:
        raise ValueError(f"catalog {kind} has unknown fields: {', '.join(unexpected)}")


def load_catalog(path: Path = CATALOG_PATH) -> tuple[dict[str, Artifact], tuple[PatchRule, ...]]:
    """Load and minimally validate a catalog without third-party dependencies."""
    try:
        source = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load patch catalog {path}: {exc}") from exc
    if not isinstance(source, dict):
        raise ValueError("catalog root must be an object")
    _check_fields(source, TOP_LEVEL_FIELDS, "root")
    if type(source.get("catalog_version")) is not int or source["catalog_version"] != 1:
        raise ValueError("unsupported or missing catalog_version")

    artifact_items = source.get("artifacts")
    patch_items = source.get("patches")
    if not isinstance(artifact_items, list) or not isinstance(patch_items, list):
        raise ValueError("catalog artifacts and patches must be arrays")

    artifacts: dict[str, Artifact] = {}
    for item in artifact_items:
        if not isinstance(item, dict):
            raise ValueError("catalog artifact must be an object")
        _check_fields(item, ARTIFACT_FIELDS, "artifact")
        artifact = Artifact(
            _required_string(item, "artifact_id"),
            _required_string(item, "description"),
            _required_string(item, "handler"),
        )
        if not IDENTIFIER_RE.fullmatch(artifact.artifact_id):
            raise ValueError(f"invalid artifact id {artifact.artifact_id!r}")
        if artifact.handler not in SUPPORTED_HANDLERS:
            raise ValueError(f"{artifact.artifact_id}: unknown handler {artifact.handler!r}")
        if artifact.artifact_id in artifacts:
            raise ValueError(f"duplicate artifact id {artifact.artifact_id!r}")
        artifacts[artifact.artifact_id] = artifact

    patches: list[PatchRule] = []
    names: set[str] = set()
    for item in patch_items:
        if not isinstance(item, dict):
            raise ValueError("catalog patch must be an object")
        _check_fields(item, PATCH_FIELDS, "patch")
        artifact_id = _required_string(item, "artifact_id")
        if artifact_id not in artifacts:
            raise ValueError(f"patch references unknown artifact {artifact_id!r}")
        patch_offset = item.get("patch_offset")
        rel32_exit = item.get("rel32_exit", 0)
        replacement = item.get("replacement_hex")
        if not isinstance(patch_offset, int) or patch_offset < 0:
            raise ValueError("catalog patch_offset must be a non-negative integer")
        if not isinstance(rel32_exit, int) or rel32_exit < 0:
            raise ValueError("catalog rel32_exit must be a non-negative integer")
        if replacement is not None and not isinstance(replacement, str):
            raise ValueError("catalog replacement_hex must be a string or null")
        rule = PatchRule(
            artifact_id,
            _required_string(item, "name"),
            _required_string(item, "signature_hex"),
            patch_offset,
            _required_string(item, "expected_hex"),
            replacement,
            _required_string(item, "description"),
            rel32_exit,
        )
        if not IDENTIFIER_RE.fullmatch(rule.name):
            raise ValueError(f"invalid patch name {rule.name!r}")
        signature_length = _validate_masked_hex(rule.signature_hex, "signature_hex")
        expected_length = _validate_masked_hex(rule.expected_hex, "expected_hex")
        if rule.patch_offset + expected_length > signature_length:
            raise ValueError("catalog expected_hex lies outside signature_hex")
        if rule.replacement_hex is not None:
            replacement_length = _validate_masked_hex(
                rule.replacement_hex, "replacement_hex"
            )
            if replacement_length != expected_length:
                raise ValueError(
                    "catalog replacement_hex length differs from expected_hex"
                )
        if rule.name in names:
            raise ValueError(f"duplicate patch name {rule.name!r}")
        names.add(rule.name)
        patches.append(rule)
    if not artifacts or not patches:
        raise ValueError("catalog must contain artifacts and patches")
    return artifacts, tuple(patches)


ARTIFACTS, PATCHES = load_catalog()
