# Third-party notices

## aioruckus legacy TAC archive decryption

`ruckus_tac_decrypt.py` adapts the legacy TAC decryption branch from
[`ms264556/aioruckus`](https://github.com/ms264556/aioruckus), upstream commit
`9bc44024601ed1798e096d99d192903fb5d16355`.

Upstream license: BSD Zero Clause License (BSD-0-Clause). Attribution is not
required by that license; this notice records the source and revision used by
this project.

Copyright Contributors to the aioruckus project.

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.

## Ruckus-compatible historical SquashFS tools

The generic local Docker image builds `mksquashfs` and `unsquashfs` from the
`src/squashfs4.0-ruckus-lzma` subtree of
[`ms264556/ruckus_ap_firmware_mod`](https://github.com/ms264556/ruckus_ap_firmware_mod),
upstream commit `3d9e4add414228eac4091f301e813d14130c3d61`. This is the exact
source currently compiled by the Dockerfile. It is a historical LZMA-enabled
SquashFS variant; its upstream lineage is Phillip Lougher's
[Squashfs Tools](https://github.com/plougher/squashfs-tools). The tools are
used only to convert and repack the locally supplied 10.5.1 R600 AP payload
into its patched unsigned form.

Upstream license: GNU General Public License, version 2 or later. The Docker
build fetches the pinned public source and compiles the tools locally; this
repository does not commit the source or resulting binaries. See the upstream
[`LICENSE`](https://github.com/ms264556/ruckus_ap_firmware_mod/blob/3d9e4add414228eac4091f301e813d14130c3d61/LICENSE).
