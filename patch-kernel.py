#!/usr/bin/env python3
"""Patch the ZD1200 2.6.32 kernel for QEMU lab use and rebuild the bzImage.

Why this exists
---------------
The repo boots the ZoneDirector 1200 under QEMU with a gdb stub patch
(`zd1200-patch.gdb`) that fixes hardware-specific kernel paths (watchdog,
board-data queries, halt/restart).  That flow relies on KVM hardware
breakpoints (`hbreak`), which QEMU's TCG backend does not support.  The
patches are plain static byte writes at fixed kernel virtual addresses, so
the same edits can be applied directly to the kernel image before boot.

How patches are located (multi-release support)
-----------------------------------------------
The old version of this script patched fixed virtual addresses, which only
worked for the single 10.5.1.0.282 build it was written against.  The same
five functions exist in every ZD1200 release (10.1.2.0.318 through
10.5.1.0.282) but at different addresses: the kernel is relinked for each
release, so machine_restart() lives at 0xc1015d90 in 10.1.x/10.2.x/10.3.x
and at 0xc1015db0 in 10.5.x, and the other four drift too.

Instead of addresses, each patch now carries a byte signature of the
function it targets (its entry sequence plus a few following instructions).
The instruction stream is identical across releases; only embedded absolute
addresses (string/global references) and relative displacements change, so
those bytes are masked with `??` wildcards.  A patch is applied when its
signature matches the decompressed kernel ELF exactly once; zero matches
means the release genuinely lacks that function (or it changed beyond
recognition) and more than one is ambiguous - both are hard errors.

Board data (serial number, MACs) is NOT patched here: it lives in the
board-data records on the CompactFlash image, written by write-boarddata.py.
The kernel's v54bsp driver reads those records at boot (magic "SKCR"),
populates its rbd struct, and the original board-data query code then
serves the CF-provided values, so no kernel patch for serial/MACs is needed.

Vendor bzImage layout (important!)
----------------------------------
    [setup + video table + head code][gzip member = the kernel ELF][loader code]
The gzip member does NOT span the rest of the file: a second stage boot
loader (which decompresses the kernel and relocates it) is linked *after*
the member, and the head code jumps to a baked offset of that loader.  Any
splice that replaces the whole tail destroys the loader and the guest
sleds through zeros.  This script therefore:

  1. locates the gzip member whose payload is the 32-bit kernel ELF
     (member end is found via the gzip trailer: crc32 + ISIZE of the ELF),
  2. applies the five signature-based byte patches to the ELF,
  3. recompresses it (gzip -9, mtime=0) and splices it back in place of
     exactly that member region, padding with zeros to keep the member
     length identical, so the loader tail stays at its baked offset.

The boot decompressor reads the payload with a fixed input size; the new
stream must not exceed the original member length (zero padding after the
trailer is ignored by inflate).
"""

import argparse
import gzip
import re
import struct
import sys
import zlib
from pathlib import Path

# Signature-based patches, derived from zd1200-patch.gdb.  Each entry is:
#   (function, signature_hex, patch_offset, patch_hex, description,
#    rel32_exit=0)
#   - signature_hex: byte pattern found in the ORIGINAL (unpatched) kernel
#     ELF; "??" masks a byte (typically an absolute address or a relative
#     displacement that changes between releases).
#   - patch_offset:  byte offset inside the matched signature at which the
#     patch bytes are written (0 = at the start of the match).
#   - patch_hex:     replacement bytes.  When the optional rel32_exit is
#     non-zero this is just the 0xe9 opcode: the 4 displacement bytes are
#     computed at runtime from rel32_exit, which is the offset inside the
#     matched signature of the "e9 ?? ?? ?? ??" exit jump of the code being
#     skipped; the patched jump re-targets that same exit.  This keeps the
#     redirect correct even if the skipped block's size changes in a future
#     release.
#   - rel32_exit:    see above (0 = patch bytes are taken verbatim).
PATCHES = [
    ("kernel_halt",
     "b80200000083ec04e8????????e8????????c70424????????e8????????83c404e9????????",
     0, bytes.fromhex("c3"),
     "kernel_halt(): no appliance power controller", 0),
    ("rks_pkt_trace_init",
     "83ec08e8????????85c0741fc7442404????????c70424????????e8????????e8????????31c083c408c3",
     0, bytes.fromhex("31c0c3"),
     "rks_pkt_trace_init(): skip tif0 path", 0),
    ("machine_restart",
     "83ec04c70424????????e8????????8b0d????????85c97506ff15????????c705????????00000000ff15????????83c404c3",
     0, bytes.fromhex("c3"),
     "machine_restart(): QEMU supervises restart", 0),
    ("cob7402_reset_watchdog",
     "5383ec08e8????????83f801741283f803",
     0, bytes.fromhex("31c0c3"),
     "COB7402 reset/watchdog function -> no-op", 0),
    ("board_data_retry",
     "31c083c4185b5e5fc3c70424????????e8????????b801000000e8????????b8????????e8????????e9????????",
     9, bytes.fromhex("e9"),
     "skip physical board-data retry/recovery path", 41),
]


def find_elf_member(data: bytes):
    """Return (member_start, member_end, payload) for the gzip member inside
    `data` that decompresses to the 32-bit kernel ELF.

    member_end is found by locating the gzip trailer (crc32 + ISIZE of the
    decompressed ELF) and confirming that region decompresses cleanly.
    """
    magic = b"\x1f\x8b\x08"
    start = 0
    while True:
        i = data.find(magic, start)
        if i < 0:
            break
        try:
            payload = zlib.decompress(data[i:], 16 + zlib.MAX_WBITS)
        except zlib.error:
            start = i + 1
            continue
        if not (payload.startswith(b"\x7fELF") and payload[4:5] == b"\x01"):
            start = i + 1
            continue
        # Locate this stream's trailer (it may not be at EOF: a second-stage
        # boot loader is linked after the member).
        pat = struct.pack("<II", zlib.crc32(payload) & 0xffffffff,
                          len(payload) & 0xffffffff)
        hit = data.find(pat, i + 10, i + len(data))
        if hit < 0:
            raise SystemExit(f"gzip member at 0x{i:x} decompresses to an ELF "
                             "but its trailer was not found")
        end = hit + 8
        try:
            again = zlib.decompress(data[i:end], 16 + zlib.MAX_WBITS)
        except zlib.error:
            raise SystemExit(f"trailer at 0x{hit:x} does not delimit the member")
        assert again == payload
        return i, end, payload
    raise SystemExit("no gzip member inside the bzImage decompresses to a 32-bit ELF")


def off_to_va(data: bytes, off: int) -> int:
    """Map a file offset in the ELF payload back to a kernel virtual address
    via the ELF32 PT_LOAD segments (for reporting)."""
    e_phoff = struct.unpack_from("<I", data, 28)[0]
    e_phentsize = struct.unpack_from("<H", data, 42)[0]
    e_phnum = struct.unpack_from("<H", data, 44)[0]
    for i in range(e_phnum):
        o = e_phoff + i * e_phentsize
        p_type, p_offset = struct.unpack_from("<II", data, o)
        p_vaddr = struct.unpack_from("<I", data, o + 8)[0]
        p_filesz = struct.unpack_from("<I", data, o + 16)[0]
        if p_type == 1 and p_offset <= off < p_offset + p_filesz:
            return p_vaddr + (off - p_offset)
    raise SystemExit(f"file offset 0x{off:x} is not in any PT_LOAD segment")


def find_signature(payload: bytes, sig_hex: str):
    """Return all file offsets in `payload` matching the hex signature, where
    "??" masks a byte."""
    pattern = bytearray()
    i = 0
    while i < len(sig_hex):
        pair = sig_hex[i:i + 2]
        if pair == "??":
            pattern.append(0x00)  # placeholder; replaced below
            i += 2
        else:
            pattern.append(int(pair, 16))
            i += 2
    # Convert to a regex: exact bytes escaped, "??" -> dot.
    rx = re.compile(b"".join(
        (b"." if sig_hex[j:j + 2] == "??"
         else re.escape(bytes([int(sig_hex[j:j + 2], 16)])))
        for j in range(0, len(sig_hex), 2)))
    return [m.start() for m in rx.finditer(payload)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="bzimage", default="image/bzImage",
                    help="stock bzImage (default image/bzImage)")
    ap.add_argument("--out", dest="out", default="image/bzImage.patched",
                    help="output patched bzImage (default image/bzImage.patched)")
    ap.add_argument("--vmlinux", default="image/vmlinux",
                    help="pristine kernel ELF for an optional cross-check "
                         "(default image/vmlinux; informational when patching "
                         "a release other than the one it came from)")
    args = ap.parse_args()

    bz = Path(args.bzimage).read_bytes()
    member_start, member_end, payload = find_elf_member(bz)
    member_len = member_end - member_start
    print(f"kernel ELF gzip member: file bytes {member_start}..{member_end} "
          f"(stream {member_len} bytes, decompressed {len(payload)} bytes)")

    # Optional cross-check against a pristine vmlinux.  With signature-based
    # patching this is informational only: a mismatch simply means the input
    # is a different release than the reference ELF.
    vmlinux = Path(args.vmlinux)
    if vmlinux.exists():
        want = vmlinux.read_bytes()
        if want == payload:
            print(f"payload matches {args.vmlinux}")
        else:
            print(f"note: payload differs from {args.vmlinux} "
                  "(different release? continuing with signatures)")

    # Apply the signature-based byte patches.
    elf = bytearray(payload)
    missing = []
    for name, sig_hex, patch_off, patch, desc, rel32_exit in PATCHES:
        hits = find_signature(bytes(elf), sig_hex)
        if len(hits) == 0:
            print(f"  {name:22s}: NOT FOUND - release lacks this function")
            missing.append(name)
            continue
        if len(hits) > 1:
            raise SystemExit(f"signature for {name} matched {len(hits)} places; "
                             "refusing to patch (ambiguous)")
        sig_start = hits[0]
        fo = sig_start + patch_off
        va = off_to_va(bytes(elf), fo)
        if rel32_exit:
            # Redirect to the exit of the skipped block.  The exit jump lives
            # inside the matched signature at sig_start+rel32_exit; re-target
            # the patch site at the same destination.  All of this lies in one
            # PT_LOAD segment, so file-offset deltas equal VA deltas.
            exit_rel32 = struct.unpack_from("<i", bytes(elf),
                                            sig_start + rel32_exit + 1)[0]
            target = (sig_start + rel32_exit + 5 + exit_rel32) & 0xffffffff
            disp = (target - (fo + 5)) & 0xffffffff
            patch = b"\xe9" + struct.pack("<I", disp)
        original = bytes(elf[fo:fo + len(patch)])
        if original == patch:
            print(f"  {name:22s}: already patched at {va:#x} (offset 0x{fo:x})")
        else:
            print(f"  {name:22s}: {original.hex()} -> {patch.hex()} at {va:#x} "
                  f"(offset 0x{fo:x})")
            elf[fo:fo + len(patch)] = patch

    if missing:
        raise SystemExit(f"missing patches for this release: {', '.join(missing)}")

    # Recompress; keep the member region the same length (inflate stops at the
    # trailer, so zero padding after it is harmless).
    new_member = gzip.compress(bytes(elf), compresslevel=9, mtime=0)
    if len(new_member) > member_len:
        raise SystemExit(f"patched payload recompresses to {len(new_member)} bytes, "
                         f"larger than the original member ({member_len})")
    new_member = new_member + b"\x00" * (member_len - len(new_member))

    # Splice: replace ONLY the member region; keep the loader tail untouched.
    out = bz[:member_start] + new_member + bz[member_end:]
    Path(args.out).write_bytes(out)
    print(f"wrote {args.out} ({len(out)} bytes, member region kept at {member_len} bytes)")

    import hashlib
    print("sha256:", hashlib.sha256(out).hexdigest())


if __name__ == "__main__":
    sys.exit(main())
