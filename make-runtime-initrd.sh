#!/usr/bin/env bash
set -euo pipefail

work_dir="$(cd "$(dirname "$0")" && pwd)"
state_dir="${STATE_DIR:-$work_dir}"
base_initrd="${BASE_INITRD:-$work_dir/image/bootinitramfs.gz}"
output="${RUNTIME_INITRD:-$state_dir/bootinitramfs.runtime.gz}"
payload="${ZD_PAYLOAD:-${ZD1051_PAYLOAD:-$work_dir/image/zd-payload.tar.gz}}"
stamp="$output.sha256"

if [ ! -f "$base_initrd" ]; then
    echo "Missing base initramfs: $base_initrd" >&2
    exit 1
fi

sources=(
    "$base_initrd"
    "$work_dir/make-runtime-initrd.sh"
    "$work_dir/boot-initrd-handoff"
)
if [ -f "$payload" ]; then
    sources+=("$payload")
fi
if [ -r "$work_dir/zd-dropbear2222/authorized_keys" ]; then
    sources+=("$work_dir"/zd-dropbear2222/*)
fi

signature="$(sha256sum "${sources[@]}" | sha256sum | awk '{print $1}')"
if [ -s "$output" ] && [ -f "$stamp" ] && [ "$(cat "$stamp")" = "$signature" ]; then
    exit 0
fi

mkdir -p "$state_dir"
staging="$(mktemp -d "${TMPDIR:-/tmp}/zd-runtime-initrd.XXXXXX")"
combined="$(mktemp "${TMPDIR:-/tmp}/zd-runtime-cpio.XXXXXX")"
temporary="$output.tmp.$$"
cleanup() {
    rm -rf "$staging"
    rm -f "$combined" "$temporary"
}
trap cleanup EXIT

mkdir -p "$staging/bin"
cp "$work_dir/boot-initrd-handoff" "$staging/bin/boot-handoff"
if [ -f "$payload" ]; then
    mkdir -p "$staging/zd-payload"
    # The container deliberately drops CAP_CHOWN. GNU tar otherwise notices
    # effective UID 0 and tries to restore the archive's uid/gid 1000, turning
    # an otherwise successful extraction into a fatal error.
    tar --no-same-owner -xzf "$payload" -C "$staging/zd-payload"
fi
if [ -r "$work_dir/zd-dropbear2222/authorized_keys" ]; then
    mkdir -p "$staging/zd-dropbear2222"
    cp -a "$work_dir/zd-dropbear2222/." "$staging/zd-dropbear2222/"
    chmod 755 "$staging/zd-dropbear2222/dropbear" \
        "$staging/zd-dropbear2222/dropbearkey" \
        "$staging/zd-dropbear2222/dropbearconvert" \
        "$staging/zd-dropbear2222/sftp-server"
fi

chmod 755 "$staging/bin/boot-handoff"

gzip -dc "$base_initrd" > "$combined"
(cd "$staging" && find . -print | cpio -o -H newc --quiet) >> "$combined"
gzip -1 < "$combined" > "$temporary"
mv -f "$temporary" "$output"
printf '%s\n' "$signature" > "$stamp"
echo "Prepared runtime initramfs: $output"
if [ -f "$payload" ]; then
    echo "Included release-specific ZoneDirector AP firmware and web payload."
else
    echo "Warning: ZoneDirector AP firmware payload is absent: $payload" >&2
fi
