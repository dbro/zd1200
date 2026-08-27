#!/usr/bin/env python3
"""Apply masked binary patches selected by a reusable artifact identifier.

The patcher intentionally uses masked byte patterns rather than a complete-file
hash or a fixed file offset. This permits the same patch to be applied when a
firmware rebuild moves the affected code while leaving its instruction sequence
substantially unchanged.

Pattern syntax is a sequence of hexadecimal bytes. ``??`` matches any byte.
The same wildcard in replacement_hex preserves the corresponding input byte.
Every rule references an entry in ``ARTIFACTS``. This lets several patches
share one artifact definition while ensuring that rules for unrelated binaries
are never mixed. Every selected rule must match exactly once. A rule whose
replacement is ``None`` is a required validation pattern and does not modify
the file.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from binary_patch_catalog import ARTIFACTS, PATCHES, PatchRule


@dataclass(frozen=True)
class MaskedBytes:
    values: bytes
    exact: bytes

    def __len__(self) -> int:
        return len(self.values)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_elf_member(data: bytes) -> tuple[int, int, bytes]:
    """Find the gzip member inside a vendor bzImage containing an ELF32 file."""
    magic = b"\x1f\x8b\x08"
    search_from = 0
    while True:
        member_start = data.find(magic, search_from)
        if member_start < 0:
            break
        try:
            payload = zlib.decompress(
                data[member_start:], 16 + zlib.MAX_WBITS
            )
        except zlib.error:
            search_from = member_start + 1
            continue
        if not (payload.startswith(b"\x7fELF") and payload[4:5] == b"\x01"):
            search_from = member_start + 1
            continue

        trailer = struct.pack(
            "<II",
            zlib.crc32(payload) & 0xFFFFFFFF,
            len(payload) & 0xFFFFFFFF,
        )
        trailer_start = data.find(trailer, member_start + 10)
        if trailer_start < 0:
            raise ValueError(
                f"gzip member at 0x{member_start:x} has no matching trailer"
            )
        member_end = trailer_start + len(trailer)
        try:
            confirmed = zlib.decompress(
                data[member_start:member_end], 16 + zlib.MAX_WBITS
            )
        except zlib.error as exc:
            raise ValueError("candidate gzip trailer does not delimit the member") from exc
        if confirmed != payload:
            raise ValueError("gzip member changed while locating its trailer")
        return member_start, member_end, payload
    raise ValueError("no gzip member decompresses to a 32-bit ELF")


def extract_artifact(
    source: bytes, artifact_id: str, *, report=print
) -> tuple[bytes, object]:
    """Return the patchable payload and opaque rebuild context."""
    artifact = ARTIFACTS[artifact_id]
    if artifact.handler == "raw":
        return source, None
    if artifact.handler == "zd1200_bzimage_gzip_elf":
        start, end, payload = find_elf_member(source)
        report(
            f"kernel ELF gzip member: file bytes {start}..{end} "
            f"(stream {end - start} bytes, decompressed {len(payload)} bytes)"
        )
        return payload, (start, end)
    raise ValueError(
        f"artifact {artifact_id!r} uses unknown handler {artifact.handler!r}"
    )


def rebuild_artifact(
    source: bytes,
    payload: bytes,
    artifact_id: str,
    context: object,
) -> bytes:
    """Reinsert a patched payload without moving fixed container data."""
    artifact = ARTIFACTS[artifact_id]
    if artifact.handler == "raw":
        return payload
    if artifact.handler == "zd1200_bzimage_gzip_elf":
        if not isinstance(context, tuple) or len(context) != 2:
            raise ValueError("missing bzImage member context")
        start, end = context
        member_len = end - start
        member = gzip.compress(payload, compresslevel=9, mtime=0)
        if len(member) > member_len:
            raise ValueError(
                f"patched payload recompresses to {len(member)} bytes, "
                f"larger than original member ({member_len})"
            )
        member += b"\x00" * (member_len - len(member))
        return source[:start] + member + source[end:]
    raise ValueError(
        f"artifact {artifact_id!r} uses unknown handler {artifact.handler!r}"
    )


def parse_masked_hex(text: str) -> MaskedBytes:
    compact = "".join(text.split())
    if not compact:
        raise ValueError("empty byte pattern")
    if len(compact) % 2:
        raise ValueError("byte pattern has an odd number of characters")
    tokens = (compact[index : index + 2] for index in range(0, len(compact), 2))

    values = bytearray()
    exact = bytearray()
    for token in tokens:
        if token == "??":
            values.append(0)
            exact.append(0)
            continue
        if len(token) != 2:
            raise ValueError(f"invalid byte token {token!r}")
        try:
            value = int(token, 16)
        except ValueError as exc:
            raise ValueError(f"invalid byte token {token!r}") from exc
        values.append(value)
        exact.append(1)
    return MaskedBytes(bytes(values), bytes(exact))


def rules_for_artifact(artifact_id: str) -> tuple[PatchRule, ...]:
    if artifact_id not in ARTIFACTS:
        raise ValueError(f"unknown artifact id {artifact_id!r}")
    rules = tuple(rule for rule in PATCHES if rule.artifact_id == artifact_id)
    if not rules:
        raise ValueError(f"artifact {artifact_id!r} has no patches")
    return rules


def matches_at(data: bytes, offset: int, pattern: MaskedBytes) -> bool:
    if offset < 0 or offset + len(pattern) > len(data):
        return False
    candidate = data[offset : offset + len(pattern)]
    return all(
        not required or got == want
        for got, want, required in zip(candidate, pattern.values, pattern.exact)
    )


def _longest_exact_anchor(pattern: MaskedBytes) -> tuple[int, bytes]:
    best_start = 0
    best = b""
    start = 0
    while start < len(pattern):
        while start < len(pattern) and not pattern.exact[start]:
            start += 1
        end = start
        while end < len(pattern) and pattern.exact[end]:
            end += 1
        if end - start > len(best):
            best_start = start
            best = pattern.values[start:end]
        start = end + 1
    return best_start, best


def find_masked(data: bytes, pattern: MaskedBytes) -> list[int]:
    """Return every match, including matches which overlap."""
    anchor_offset, anchor = _longest_exact_anchor(pattern)
    if not anchor:
        return list(range(0, len(data) - len(pattern) + 1))

    matches = []
    search_from = 0
    while True:
        anchor_hit = data.find(anchor, search_from)
        if anchor_hit < 0:
            return matches
        candidate = anchor_hit - anchor_offset
        if matches_at(data, candidate, pattern):
            matches.append(candidate)
        search_from = anchor_hit + 1


def overlay_pattern(
    signature: MaskedBytes,
    patch_offset: int,
    replacement: MaskedBytes,
    *,
    preserve_wildcards: bool = True,
) -> MaskedBytes:
    if patch_offset < 0 or patch_offset + len(replacement) > len(signature):
        raise ValueError("replacement lies outside signature")
    values = bytearray(signature.values)
    exact = bytearray(signature.exact)
    for index, (value, required) in enumerate(
        zip(replacement.values, replacement.exact)
    ):
        if required:
            values[patch_offset + index] = value
            exact[patch_offset + index] = 1
        elif not preserve_wildcards:
            exact[patch_offset + index] = 0
    return MaskedBytes(bytes(values), bytes(exact))


def render_replacement(original: bytes, replacement: MaskedBytes) -> bytes:
    if len(original) != len(replacement):
        raise ValueError("replacement length mismatch")
    return bytes(
        value if required else old
        for old, value, required in zip(
            original, replacement.values, replacement.exact
        )
    )


def apply_rules(
    source: bytes,
    rules: Iterable[PatchRule] | None = None,
    *,
    report=print,
) -> tuple[bytes, bool]:
    """Return ``(result, changed)`` after applying all rules."""
    if rules is None:
        # Preserve the historical no-argument behavior for the shared AP platform.
        rules = rules_for_artifact("ap_11n_scorpion_wlan_ko")
    rules = tuple(rules)
    artifact_ids = {rule.artifact_id for rule in rules}
    if len(artifact_ids) > 1:
        raise ValueError(
            "apply_rules received patches for multiple artifacts: "
            + ", ".join(sorted(artifact_ids))
        )
    original_source = bytes(source)
    resolved = []

    # Resolve every rule against the pristine input before writing anything.
    for rule in rules:
        signature = parse_masked_hex(rule.signature_hex)
        expected = parse_masked_hex(rule.expected_hex)
        if rule.patch_offset + len(expected) > len(signature):
            raise ValueError(f"{rule.name}: expected bytes lie outside signature")

        original_hits = find_masked(original_source, signature)
        if rule.replacement_hex is None:
            if len(original_hits) != 1:
                raise ValueError(
                    f"{rule.name}: validation pattern matched "
                    f"{len(original_hits)} places (expected exactly one)"
                )
            match_offset = original_hits[0]
            site = match_offset + rule.patch_offset
            if not matches_at(original_source, site, expected):
                raise ValueError(f"{rule.name}: validation bytes mismatch")
            resolved.append((rule, match_offset, expected, None, "validated"))
            continue

        replacement = parse_masked_hex(rule.replacement_hex)
        if len(expected) != len(replacement):
            raise ValueError(f"{rule.name}: expected/replacement length mismatch")
        patched_signature = overlay_pattern(
            signature,
            rule.patch_offset,
            replacement,
            preserve_wildcards=not bool(rule.rel32_exit),
        )
        patched_hits = find_masked(original_source, patched_signature)

        if len(original_hits) == 1 and not patched_hits:
            state = "patch"
            match_offset = original_hits[0]
        elif not original_hits and len(patched_hits) == 1:
            state = "already"
            match_offset = patched_hits[0]
        else:
            raise ValueError(
                f"{rule.name}: original signature matched {len(original_hits)} "
                f"places and patched signature matched {len(patched_hits)} places; "
                "refusing ambiguous or unknown input"
            )

        site = match_offset + rule.patch_offset
        if state == "patch" and not matches_at(original_source, site, expected):
            raise ValueError(f"{rule.name}: expected instruction mismatch")

        if state == "patch" and rule.rel32_exit:
            if len(replacement) != 5 or replacement.values[0] != 0xE9:
                raise ValueError(
                    f"{rule.name}: rel32 patch must be a five-byte e9 jump"
                )
            exit_site = match_offset + rule.rel32_exit
            signature_end = match_offset + len(signature)
            if exit_site < match_offset or exit_site + 5 > signature_end:
                raise ValueError(f"{rule.name}: rel32 exit lies outside signature")
            if original_source[exit_site] != 0xE9:
                raise ValueError(f"{rule.name}: rel32 exit is not an e9 jump")
            exit_disp = struct.unpack_from("<i", original_source, exit_site + 1)[0]
            target = exit_site + 5 + exit_disp
            new_disp = (target - (site + 5)) & 0xFFFFFFFF
            replacement = MaskedBytes(
                b"\xe9" + struct.pack("<I", new_disp),
                b"\x01" * 5,
            )
        resolved.append((rule, match_offset, expected, replacement, state))

    ranges = []
    for rule, match_offset, _expected, replacement, state in resolved:
        if replacement is None or state != "patch":
            continue
        start = match_offset + rule.patch_offset
        end = start + len(replacement)
        if any(
            start < other_end and other_start < end
            for other_start, other_end in ranges
        ):
            raise ValueError(f"{rule.name}: patch overlaps another patch")
        ranges.append((start, end))

    result = bytearray(original_source)
    changed = False
    for rule, match_offset, _expected, replacement, state in resolved:
        site = match_offset + rule.patch_offset
        if replacement is None:
            report(
                f"{rule.name}: validated at file offset 0x{match_offset:x}: "
                f"{rule.description}"
            )
            continue
        if state == "already":
            report(f"{rule.name}: already patched at file offset 0x{site:x}")
            continue
        old = bytes(result[site : site + len(replacement)])
        new = render_replacement(old, replacement)
        result[site : site + len(new)] = new
        changed |= old != new
        report(f"{rule.name}: 0x{site:x}: {old.hex(' ')} -> {new.hex(' ')}")
        report(f"  {rule.description}")

    final = bytes(result)

    # Verify each write produced exactly one patched signature and no original
    # signature. Validation-only rules must still match once.
    for rule in rules:
        signature = parse_masked_hex(rule.signature_hex)
        if rule.replacement_hex is None:
            if len(find_masked(final, signature)) != 1:
                raise AssertionError(f"{rule.name}: validation signature lost")
            continue
        replacement = parse_masked_hex(rule.replacement_hex)
        patched_signature = overlay_pattern(
            signature,
            rule.patch_offset,
            replacement,
            preserve_wildcards=not bool(rule.rel32_exit),
        )
        if find_masked(final, signature):
            raise AssertionError(f"{rule.name}: original signature remains")
        if len(find_masked(final, patched_signature)) != 1:
            raise AssertionError(f"{rule.name}: patched signature not unique")

    return final, changed


def apply_artifact(
    source: bytes, artifact_id: str, *, report=print
) -> tuple[bytes, bool]:
    """Apply only the patches registered for ``artifact_id``."""
    return apply_rules(source, rules_for_artifact(artifact_id), report=report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact",
        choices=tuple(ARTIFACTS),
        default="zd1200_kernel_elf",
        help="artifact whose registered patches should be applied",
    )
    parser.add_argument(
        "--in",
        dest="input",
        type=Path,
        default=Path("image/bzImage"),
        help="input artifact or container (default: image/bzImage)",
    )
    parser.add_argument(
        "--out",
        dest="output",
        type=Path,
        default=Path("image/bzImage.patched"),
        help="output artifact or container (default: image/bzImage.patched)",
    )
    parser.add_argument(
        "--vmlinux",
        type=Path,
        default=Path("image/vmlinux"),
        help="optional pristine extracted payload cross-check",
    )
    args = parser.parse_args()

    source = args.input.read_bytes()
    print(f"input:  {args.input}")
    print(f"artifact: {args.artifact} ({ARTIFACTS[args.artifact].description})")
    print(f"sha256: {sha256(source)}")
    try:
        payload, context = extract_artifact(source, args.artifact)
        if args.vmlinux.exists() and args.artifact == "zd1200_kernel_elf":
            if args.vmlinux.read_bytes() == payload:
                print(f"payload matches {args.vmlinux}")
            else:
                print(
                    f"note: payload differs from {args.vmlinux} "
                    "(different release? continuing with signatures)"
                )
        patched_payload, changed = apply_artifact(payload, args.artifact)
        patched = rebuild_artifact(
            source, patched_payload, args.artifact, context
        )
    except ValueError as exc:
        raise SystemExit(f"refusing to patch: {exc}") from exc

    args.output.write_bytes(patched)
    print(f"output: {args.output}")
    print(f"sha256: {sha256(patched)}")
    print("result: " + ("patched" if changed else "already patched"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
