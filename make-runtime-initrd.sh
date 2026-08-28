#!/usr/bin/env bash
set -euo pipefail

work_dir="$(cd "$(dirname "$0")" && pwd)"
state_dir="${STATE_DIR:-$work_dir}"
base_initrd="${BASE_INITRD:-$work_dir/image/bootinitramfs.gz}"
output="${RUNTIME_INITRD:-$state_dir/bootinitramfs.runtime.gz}"
payload="${ZD_PAYLOAD:-${ZD1051_PAYLOAD:-$work_dir/image/zd-payload.tar.gz}}"
stamp="$output.sha256"
enable_ecdsa_ssh="${ZD_ENABLE_ECDSA_SSH:-0}"
enable_root_cli="${ZD_ENABLE_ROOT_CLI:-0}"
support_entitlement_end="${ZD_SUPPORT_ENTITLEMENT_END:-}"
root_ssh_public_key="${ZD_ROOT_SSH_PUBLIC_KEY:-}"
root_ssh_public_key_sha256=""

case "$enable_ecdsa_ssh" in
    0|1) ;;
    *)
        echo "ZD_ENABLE_ECDSA_SSH must be 0 or 1 (got: $enable_ecdsa_ssh)" >&2
        exit 2
        ;;
esac

case "$enable_root_cli" in
    0|1) ;;
    *)
        echo "ZD_ENABLE_ROOT_CLI must be 0 or 1 (got: $enable_root_cli)" >&2
        exit 2
        ;;
esac

support_entitlement_end_epoch=0
if [ -n "$support_entitlement_end" ]; then
    if ! parsed_end="$(date -u -d "$support_entitlement_end" +%F 2>/dev/null)" \
        || [ "$parsed_end" != "$support_entitlement_end" ]; then
        echo "ZD_SUPPORT_ENTITLEMENT_END must be a valid YYYY-MM-DD date (got: $support_entitlement_end)" >&2
        exit 2
    fi
    support_entitlement_end_epoch="$(date -u -d "$support_entitlement_end 00:00:00" +%s)"
    if [ "$support_entitlement_end_epoch" -le 1262304000 ]; then
        echo "ZD_SUPPORT_ENTITLEMENT_END must be later than 2010-01-01" >&2
        exit 2
    fi
fi

if [ -n "$root_ssh_public_key" ]; then
    if ! root_ssh_public_key="$(python3 "$work_dir/zd_root_ssh.py" "$root_ssh_public_key")"; then
        exit 2
    fi
    root_ssh_public_key_sha256="$(printf '%s' "$root_ssh_public_key" | sha256sum | awk '{print $1}')"
fi

if [ ! -f "$base_initrd" ]; then
    echo "Missing base initramfs: $base_initrd" >&2
    exit 1
fi

sources=(
    "$base_initrd"
    "$work_dir/make-runtime-initrd.sh"
    "$work_dir/boot-initrd-handoff"
    "$work_dir/zd-controller-wrapper.sh"
    "$work_dir/zd-memory-snapshot.sh"
    "$work_dir/zd_root_ssh.py"
)
if [ -f "$payload" ]; then
    sources+=("$payload")
fi

signature="$({
    sha256sum "${sources[@]}"
    printf 'ZD_ENABLE_ECDSA_SSH=%s\n' "$enable_ecdsa_ssh"
    printf 'ZD_ENABLE_ROOT_CLI=%s\n' "$enable_root_cli"
    printf 'ZD_SUPPORT_ENTITLEMENT_END=%s\n' "$support_entitlement_end"
    printf 'ZD_ROOT_SSH_PUBLIC_KEY_SHA256=%s\n' "$root_ssh_public_key_sha256"
    printf 'ZD_RUNTIME_OPTIONS_FORMAT=1\n'
} | sha256sum | awk '{print $1}')"
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
cp "$work_dir/zd-controller-wrapper.sh" "$staging/zd-controller-wrapper.sh"
cp "$work_dir/zd-memory-snapshot.sh" "$staging/zd-memory-snapshot.sh"
if [ -f "$payload" ]; then
    mkdir -p "$staging/zd-payload"
    # The container deliberately drops CAP_CHOWN. GNU tar otherwise notices
    # effective UID 0 and tries to restore the archive's uid/gid 1000, turning
    # an otherwise successful extraction into a fatal error.
    tar --no-same-owner -xzf "$payload" -C "$staging/zd-payload"
fi
if [ -n "$root_ssh_public_key" ]; then
    printf '%s\n' "$root_ssh_public_key" > "$staging/zd-root-authorized_keys"
    chmod 600 "$staging/zd-root-authorized_keys"
fi

# The boot handoff validates this small, data-only file before using it.
{
    printf 'ENABLE_ECDSA_SSH=%s\n' "$enable_ecdsa_ssh"
    printf 'ENABLE_ROOT_CLI=%s\n' "$enable_root_cli"
    printf 'SUPPORT_ENTITLEMENT_END_EPOCH=%s\n' "$support_entitlement_end_epoch"
} > "$staging/zd-runtime-options"

chmod 755 "$staging/bin/boot-handoff"
chmod 755 "$staging/zd-controller-wrapper.sh" "$staging/zd-memory-snapshot.sh"

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
