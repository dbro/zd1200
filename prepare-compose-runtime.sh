#!/usr/bin/env bash
# Prepare vendor-derived runtime files in a Compose volume.  The vendor
# download is bind-mounted read-only and is never copied into a Docker image.
set -euo pipefail

work_dir=/opt/zd1200
vendor_dir=/vendor
runtime_dir=/runtime
archive_name="${ZD_VENDOR_ARCHIVE:-}"
r600_name="${ZD_R600_BL7:-}"

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

if [ -n "$r600_name" ]; then
    safe_basename "$r600_name" || fail "ZD_R600_BL7 must be a filename, not a path"
    r600_bl7="$vendor_dir/$r600_name"
    [ -f "$r600_bl7" ] || fail "R600 BL7 override is not readable: $r600_bl7"
fi

mkdir -p "$runtime_dir"
input_fingerprint="$(
    sha256sum "$archive"
    if [ -n "$r600_name" ]; then
        sha256sum "$r600_bl7"
    fi
)"
if [ -f "$runtime_dir/preparation-input.sha256" ] \
    && [ -f "$runtime_dir/bzImage" ] \
    && [ -f "$runtime_dir/bootinitramfs.gz" ]; then
    if [ "$(cat "$runtime_dir/preparation-input.sha256")" = "$input_fingerprint" ]; then
        echo "Prepared runtime already matches this vendor input."
        exit 0
    fi
    fail "prepared runtime belongs to different input; stop Compose and remove the named runtime volume before changing ZD_VENDOR_ARCHIVE or ZD_R600_BL7"
fi
temporary="$(mktemp -d /tmp/zd1200-compose-prepare.XXXXXX)"
cleanup() { rm -rf "$temporary"; }
trap cleanup EXIT

command=(python3 "$work_dir/build_zd1200_bundle.py" "$archive" "$temporary/bundle.zip")
if [ -n "$r600_name" ]; then
    command+=(--r600-bl7 "$r600_bl7")
fi

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
echo "Prepared runtime is ready for the ZoneDirector service."
