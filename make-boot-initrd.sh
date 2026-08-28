#!/usr/bin/env bash
set -euo pipefail

work_dir="$(cd "$(dirname "$0")" && pwd)"
source_dir="/tmp/zdinitrd2"
original="$work_dir/image/restoreinitramfs.gz"
staging="$(mktemp -d "${TMPDIR:-/tmp}/zd-boot-initrd.XXXXXX")"
trap 'rm -rf "$staging"' EXIT

# The guest helper is always 32-bit x86, including when this source bundle is
# built in an ARM64 Docker container. Prefer Debian's cross binutils there;
# native x86 binutils remain a useful fallback for ordinary Linux x86 hosts.
if [ -n "${I386_AS:-}" ]; then
    assembler="$I386_AS"
elif command -v i686-linux-gnu-as >/dev/null 2>&1; then
    assembler=i686-linux-gnu-as
elif [[ "$(uname -m)" =~ ^(i[3-6]86|x86_64)$ ]]; then
    assembler=as
else
    echo "i686-linux-gnu-as is required to build this x86 guest on $(uname -m); install binutils-i686-linux-gnu" >&2
    exit 1
fi
if [ -n "${I386_LD:-}" ]; then
    linker="$I386_LD"
elif command -v i686-linux-gnu-ld >/dev/null 2>&1; then
    linker=i686-linux-gnu-ld
elif [[ "$(uname -m)" =~ ^(i[3-6]86|x86_64)$ ]]; then
    linker=ld
else
    echo "i686-linux-gnu-ld is required to build this x86 guest on $(uname -m); install binutils-i686-linux-gnu" >&2
    exit 1
fi

mkdir -p "$staging/newroot" "$staging/etc" "$staging/bin" "$staging/lab-certs"
cp "$work_dir/boot-initrd-init" "$staging/init"
cp "$work_dir/boot-initrd-inittab" "$staging/etc/inittab"
cp "$work_dir/boot-initrd-handoff" "$staging/bin/boot-handoff"
cp "$work_dir/zd-controller-wrapper.sh" "$staging/zd-controller-wrapper.sh"
cp "$work_dir/zd-memory-snapshot.sh" "$staging/zd-memory-snapshot.sh"
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -subj '/CN=zd1200-lab' \
    -keyout "$staging/lab-certs/webackey.pem" \
    -out "$staging/lab-certs/webaccert.pem" >/dev/null 2>&1
"$assembler" --32 "$work_dir/pivot-exec.S" -o "$staging/pivot-exec.o"
"$linker" -m elf_i386 -N -e _start -o "$staging/bin/pivot-exec" "$staging/pivot-exec.o"
rm -f "$staging/pivot-exec.o"
chmod 755 "$staging/init"
chmod 755 "$staging/bin/boot-handoff"
chmod 755 "$staging/zd-controller-wrapper.sh"
chmod 755 "$staging/zd-memory-snapshot.sh"
chmod 755 "$staging/bin/pivot-exec"

combined="$(mktemp "${TMPDIR:-/tmp}/zd-boot-initrd-archive.XXXXXX")"
trap 'rm -rf "$staging" "$combined"' EXIT
gzip -dc "$original" > "$combined"
(cd "$staging" && find . -print | cpio -o -H newc --quiet) >> "$combined"
gzip -9 < "$combined" > "$work_dir/image/bootinitramfs.gz"
echo "created $work_dir/image/bootinitramfs.gz"
