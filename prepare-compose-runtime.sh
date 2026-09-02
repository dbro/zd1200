#!/usr/bin/env bash
# Prepare vendor-derived runtime files in a Compose volume.  The vendor
# download is bind-mounted read-only and is never copied into a Docker image.
set -euo pipefail

work_dir=/opt/zd1200
vendor_dir=/vendor
runtime_dir=/runtime
archive_name="${ZD_VENDOR_ARCHIVE:-}"

fail() {
    echo "zd1200-prepare: $*" >&2
    exit 2
}

safe_basename() {
    case "$1" in
        ""|.|..|*/*) return 1 ;;
        *) return 0 ;;
    esac
}

safe_basename "$archive_name" || fail "ZD_VENDOR_ARCHIVE must be a filename, not a path"
archive="$vendor_dir/$archive_name"
[ -f "$archive" ] || fail "vendor archive is not readable: $archive"

mkdir -p "$runtime_dir"
virtual_build_id="$(bash "$work_dir/resolve-source-revision.sh")"
write_virtual_build_id() {
    printf '%s\n' "$virtual_build_id" > "$runtime_dir/virtual-build-id.tmp"
    mv -f "$runtime_dir/virtual-build-id.tmp" "$runtime_dir/virtual-build-id"
}
input_fingerprint="$(sha256sum "$archive")"
if [ -f "$runtime_dir/preparation-input.sha256" ] \
    && [ -f "$runtime_dir/bzImage" ] \
    && [ -f "$runtime_dir/bootinitramfs.gz" ]; then
    if [ "$(cat "$runtime_dir/preparation-input.sha256")" = "$input_fingerprint" ]; then
        write_virtual_build_id
        echo "Prepared runtime already matches this vendor input."
        exit 0
    fi
    fail "prepared runtime belongs to different input; stop Compose and remove the named runtime volume before changing ZD_VENDOR_ARCHIVE"
fi
temporary="$(mktemp -d /tmp/zd1200-compose-prepare.XXXXXX)"
cleanup() { rm -rf "$temporary"; }
trap cleanup EXIT

command=(
    python3 "$work_dir/build_zd1200_bundle.py" "$archive" "$temporary/bundle.zip"
    --auto-r600-mesh
    --unsquashfs /usr/local/lib/zd1200/ruckus-squashfs/unsquashfs
    --mksquashfs /usr/local/lib/zd1200/ruckus-squashfs/mksquashfs
)

echo "Preparing local ZD runtime from $archive_name ..."
"${command[@]}" > "$temporary/build-report.json"
unzip -q "$temporary/bundle.zip" 'image/*' -d "$temporary/extracted"
[ -f "$temporary/extracted/image/bzImage" ] || fail "prepared bundle has no bzImage"
[ -f "$temporary/extracted/image/bootinitramfs.gz" ] || fail "prepared bundle has no boot initramfs"

# Do not expose a partial runtime: generate and validate everything in /tmp,
# then replace the dedicated named-volume contents only after success.
find "$runtime_dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a "$temporary/extracted/image/." "$runtime_dir/"
cp "$temporary/build-report.json" "$runtime_dir/build-report.json"
sha256sum "$archive" > "$runtime_dir/source-archive.sha256"
printf '%s\n' "$archive_name" > "$runtime_dir/source-archive.name"
printf '%s\n' "$input_fingerprint" > "$runtime_dir/preparation-input.sha256"
write_virtual_build_id
echo "Prepared runtime is ready for the ZoneDirector service."
