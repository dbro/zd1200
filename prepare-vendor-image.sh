#!/usr/bin/env bash
# Build the locally ignored runtime image/ directory from a user-supplied,
# decrypted ZD1200 10.5.1.0.282 archive. No vendor material is redistributed.
set -euo pipefail

work_dir="$(cd "$(dirname "$0")" && pwd)"
archive_path="${1:-}"
release_id="${RELEASE_ID:-zd1200_10_5_1_0_282}"

fail() {
    echo "prepare-vendor-image: $*" >&2
    exit 1
}

[ -n "$archive_path" ] || fail "usage: $0 /path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img.tgz"
[ -f "$archive_path" ] || fail "archive not found: $archive_path"
for command in tar gzip python3 md5sum sha256sum; do
    command -v "$command" >/dev/null || fail "$command is required"
done

# This launcher currently knows how to construct the 10.5.1 runtime payload.
# The release manifest/verifier supplies the exact archive identity and safe
# TAR preflight; do not weaken that check with an environment hash override.
[ "$release_id" = "zd1200_10_5_1_0_282" ] || fail "unsupported release_id: $release_id"

staging="$(mktemp -d "${TMPDIR:-/tmp}/zd1051-vendor.XXXXXX")"
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
for required in bzImage restoreinitramfs.gz rootfs.i386.ext2.director1200.img metadata aidfs/file_list.txt file_list.txt ap-models; do
    require_file "$required"
done
[ -d "$source_dir/firmwares" ] || fail "vendor archive lacks firmwares/"

metadata_value() {
    awk -F= -v key="$1" '$1 == key { print $2; exit }' "$metadata"
}
[ "$(metadata_value VERSION)" = "10.5.1.0" ] || fail "unexpected vendor version"
[ "$(metadata_value BUILD)" = "282" ] || fail "unexpected vendor build"
[ "$(metadata_value REQUIRE_PLATFORM)" = "nar5520" ] || fail "unexpected platform"
[ "$(metadata_value REQUIRE_SUBPLATFORM)" = "cob7402" ] || fail "unexpected subplatform"

kernel_md5="$(md5sum "$source_dir/bzImage" | awk '{print $1}')"
rootfs_md5="$(md5sum "$source_dir/rootfs.i386.ext2.director1200.img" | awk '{print $1}')"
[ "$kernel_md5" = "$(metadata_value KERNEL_MD5SUM)" ] || fail "bzImage MD5 mismatch"
[ "$rootfs_md5" = "$(metadata_value ROOTFS_MD5SUM)" ] || fail "rootfs MD5 mismatch"

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

tar -C "$source_dir" -czf "$output_dir/zd1051-payload.tar.gz" \
    firmwares aidfs ap-models file_list.txt
"$work_dir/make-boot-initrd.sh"

echo "Prepared local vendor-derived artifacts in $output_dir"
sha256sum "$output_dir/bzImage" "$output_dir/vmlinux" "$output_dir/rootfs.ext2" \
    "$output_dir/bootinitramfs.gz" "$output_dir/zd1051-payload.tar.gz"
