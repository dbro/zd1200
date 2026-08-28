#!/usr/bin/env bash
# Build the locally ignored runtime image/ directory from a user-supplied,
# ZD1200 archive selected by an exact release manifest. No vendor material is
# redistributed.
set -euo pipefail

work_dir="$(cd "$(dirname "$0")" && pwd)"
archive_path="${1:-}"
release_id="${RELEASE_ID:-zd1200_10_5_1_0_282}"

fail() {
    echo "prepare-vendor-image: $*" >&2
    exit 1
}

[ -n "$archive_path" ] || fail "usage: RELEASE_ID=zd1200_10_2_1_0_232 $0 /path/to/zd1200.img"
[ -f "$archive_path" ] || fail "archive not found: $archive_path"
for command in tar gzip python3 md5sum sha256sum; do
    command -v "$command" >/dev/null || fail "$command is required"
done

staging="$(mktemp -d "${TMPDIR:-/tmp}/zd-vendor.XXXXXX")"
trap 'rm -rf "$staging"' EXIT
if gzip -t "$archive_path" 2>/dev/null; then
    decrypted_archive="$archive_path"
    echo "Input is a gzip-compressed TAR; verifying decrypted archive."
else
    echo "Input is opaque; verifying exact encrypted archive and decrypting locally."
    python3 "$work_dir/verify_release_archive.py" --release "$release_id" --encrypted "$archive_path"
    decrypted_archive="$staging/decrypted.img.tgz"
    python3 "$work_dir/ruckus_tac_decrypt.py" "$archive_path" "$decrypted_archive"
fi
python3 "$work_dir/verify_release_archive.py" --release "$release_id" "$decrypted_archive"

# Refuse paths that would escape the temporary extraction directory.
if tar -tzf "$decrypted_archive" | awk '/^\// || /(^|\/)\.\.($|\/)/ { bad = 1 } END { exit bad ? 0 : 1 }'; then
    fail "archive contains an unsafe path"
fi

tar -xzf "$decrypted_archive" -C "$staging"
metadata="$(find "$staging" -type f -name metadata -print -quit)"
[ -n "$metadata" ] || fail "vendor metadata file not found"
source_dir="$(dirname "$metadata")"

require_file() {
    [ -f "$source_dir/$1" ] || fail "vendor archive lacks $1"
}
for required in bzImage restoreinitramfs.gz rootfs.i386.ext2.director1200.img metadata file_list.txt ap-models; do
    require_file "$required"
done
[ -d "$source_dir/firmwares" ] || fail "vendor archive lacks firmwares/"

output_dir="$work_dir/image"
mkdir -p "$output_dir"
cp -f "$source_dir/bzImage" "$output_dir/bzImage"
cp -f "$source_dir/restoreinitramfs.gz" "$output_dir/restoreinitramfs.gz"
cp -f "$source_dir/rootfs.i386.ext2.director1200.img" "$output_dir/rootfs.ext2"

# The compressed ELF begins at a variable offset inside the x86 bzImage.
# Search gzip members and keep the one that expands to an i386 ELF file.
python3 - "$output_dir/bzImage" "$output_dir/vmlinux" <<'PY'
import sys
import zlib

source, destination = sys.argv[1:]
data = open(source, "rb").read()
for offset in range(len(data) - 2):
    if data[offset:offset + 3] != b"\x1f\x8b\x08":
        continue
    try:
        # bzImage puts non-gzip bytes immediately after the compressed member.
        # zlib stops cleanly at the member boundary; gzip.GzipFile rejects that
        # normal trailing kernel data.
        candidate = zlib.decompress(data[offset:], 16 + zlib.MAX_WBITS)
    except zlib.error:
        continue
    if candidate.startswith(b"\x7fELF") and candidate[4:5] == b"\x01":
        open(destination, "wb").write(candidate)
        break
else:
    raise SystemExit("could not locate an ELF kernel inside bzImage")
PY

payload_stage="$staging/payload"
mkdir "$payload_stage"
cp -a "$source_dir/firmwares" "$source_dir/ap-models" "$source_dir/file_list.txt" "$payload_stage/"
if [ -d "$source_dir/aidfs" ]; then
    cp -a "$source_dir/aidfs" "$payload_stage/"
    has_aidfs=1
else
    has_aidfs=0
fi
{
    printf 'RELEASE_ID=%s\n' "$release_id"
    awk -F= '$1 == "VERSION" || $1 == "BUILD" { print }' "$metadata"
    printf 'HAS_AIDFS=%s\n' "$has_aidfs"
    # The legacy preparation path is restricted to a manifest-selected exact
    # release. Keep its runtime profile explicit rather than inheriting a
    # 10.5-only assumption; all currently supported releases use this profile.
    printf 'RUNTIME_FTP_BOOTSTRAP=vendor_state\n'
} > "$payload_stage/release-info"
tar -C "$payload_stage" -czf "$output_dir/zd-payload.tar.gz" .
"$work_dir/make-boot-initrd.sh"

echo "Prepared local vendor-derived artifacts in $output_dir"
sha256sum "$output_dir/bzImage" "$output_dir/vmlinux" "$output_dir/rootfs.ext2" \
    "$output_dir/bootinitramfs.gz" "$output_dir/zd-payload.tar.gz"
