"""Declarative artifact and binary-patch definitions.

The matching and writing engine lives in :mod:`patch_binary_artifact`. Keeping
this catalog separate makes it possible to add patches or reuse an artifact ID
without changing the patching algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass


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


ARTIFACTS = {
    artifact.artifact_id: artifact
    for artifact in (
        Artifact(
            "r600_wlan_ko",
            "R600 firmware ELF32 big-endian MIPS wlan.ko",
            "raw",
        ),
        Artifact(
            "zd1200_kernel_elf",
            "decompressed ZD1200 ELF32 little-endian x86 kernel",
            "zd1200_bzimage_gzip_elf",
        ),
    )
}


# The R600 signature covers the VLAN EtherType construction, conditional
# branch, 16-byte memmove, and following two-byte pull. Address material and
# branch displacement are masked. Keeping the last two instruction bytes
# preserves the original MIPS branch target:
#
#     54 62 xx xx   bnel v1,v0,target
#     10 00 xx xx   b    target
PATCHES = (
    PatchRule(
        "r600_wlan_ko",
        "r600_mesh_vlan_rx",
        """
        90 42 00 0c 90 63 00 0d 00 02 12 00 00 62 18 25
        34 02 81 00 54 62 ?? ?? 8e a2 01 08 8e a5 01 08
        3c 02 ?? ?? 24 06 00 10 24 42 ?? ?? 00 40 f8 09
        24 a4 00 02 24 05 00 02 02 00 f8 09 02 a0 20 21
        """,
        20,
        "54 62 ?? ??",
        "10 00 ?? ??",
        "bypass destructive VLAN inner-EtherType removal",
    ),
    PatchRule(
        "zd1200_kernel_elf",
        "kernel_halt",
        "b80200000083ec04e8????????e8????????c70424????????e8????????83c404e9????????",
        0,
        "b8",
        "c3",
        "kernel_halt(): no appliance power controller",
    ),
    PatchRule(
        "zd1200_kernel_elf",
        "rks_pkt_trace_init",
        "83ec08e8????????85c0741fc7442404????????c70424????????e8????????e8????????31c083c408c3",
        0,
        "83ec08",
        "31c0c3",
        "rks_pkt_trace_init(): skip tif0 path",
    ),
    PatchRule(
        "zd1200_kernel_elf",
        "machine_restart",
        "83ec04c70424????????e8????????8b0d????????85c97506ff15????????c705????????00000000ff15????????83c404c3",
        0,
        "83",
        "c3",
        "machine_restart(): QEMU supervises restart",
    ),
    PatchRule(
        "zd1200_kernel_elf",
        "cob7402_reset_watchdog",
        "5383ec08e8????????83f801741283f803",
        0,
        "5383ec",
        "31c0c3",
        "COB7402 reset/watchdog function -> no-op",
    ),
    PatchRule(
        "zd1200_kernel_elf",
        "board_data_retry",
        "31c083c4185b5e5fc3c70424????????e8????????b801000000e8????????b8????????e8????????e9????????",
        9,
        "c70424????",
        "e9????????",
        "skip physical board-data retry/recovery path",
        41,
    ),
)
