#!/usr/bin/env bash
# Launch the virtual Ruckus ZoneDirector 1200 lab (patched kernel, loopback
# networking, custom dropbear on guest port 2222).
#
# Builds/checks every prerequisite on the way and then execs
# run-zd1200-qemu.sh with the lab settings.  See ZD1200-LAB-GUIDE.md.
#
# Usage:
#   ./run-zd1200-lab.sh                 # launch (builds what is missing)
#   ./run-zd1200-lab.sh --reset-disk    # wipe VM disk state (factory reset)
#   ./run-zd1200-lab.sh --rebuild-kernel# force the kernel patch step
#   ./run-zd1200-lab.sh --wait [SECS]   # launch, then poll the web login page
#   ACCEL=kvm ./run-zd1200-lab.sh       # override accelerator (default tcg)
#   ZD_SERIAL=... ZD_MAC1=... ./run-zd1200-lab.sh   # board data (MAC2=MAC1+1)
set -eu

work_dir="$(cd "$(dirname "$0")" && pwd)"
cd "$work_dir"

RESET_DISK=0
REBUILD_KERNEL=0
WAIT=0
WAIT_SECS=600
while [ $# -gt 0 ]; do
    case "$1" in
        --reset-disk) RESET_DISK=1; shift ;;
        --rebuild-kernel) REBUILD_KERNEL=1; shift ;;
        --wait) WAIT=1; shift; if [ $# -gt 0 ] && [[ "$1" =~ ^[0-9]+$ ]]; then WAIT_SECS="$1"; shift; fi ;;
        -h|--help) sed -n '1,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

for tool in qemu-system-i386 qemu-img python3; do
    command -v "$tool" >/dev/null 2>&1 || { echo "missing tool: $tool" >&2; exit 1; }
done

echo "== prerequisites =="
[ -f image/bzImage ]        || { echo "missing image/bzImage - run prepare-vendor-image.sh first" >&2; exit 1; }
[ -f image/vmlinux ]        || { echo "missing image/vmlinux - run prepare-vendor-image.sh first" >&2; exit 1; }
[ -f image/restoreinitramfs.gz ] || { echo "missing image/restoreinitramfs.gz - run prepare-vendor-image.sh first" >&2; exit 1; }
[ -d zd-dropbear2222 ]      || { echo "missing zd-dropbear2222/ payload dir" >&2; exit 1; }

# The vendor rootfs file in image/ is gzip-compressed; the synthetic disk and
# the controller need a RAW ext2 filesystem (superblock magic 0xEF53 at byte
# 1080).  Decompress it in place if needed.
if [ -f image/rootfs.ext2 ]; then
    python3 - <<'PY'
from pathlib import Path
p = Path("image/rootfs.ext2")
data = p.read_bytes()
if data[:3] == b"\x1f\x8b\x08":          # gzip
    import gzip as gz
    raw = gz.decompress(data)
    assert raw[1080:1082] == b"\x53\xef", "decompressed rootfs is not ext2"
    p.write_bytes(raw)
    print("image/rootfs.ext2: was gzip, decompressed to raw ext2", len(raw), "bytes")
elif data[1080:1082] == b"\x53\xef":
    print("image/rootfs.ext2: raw ext2 OK")
else:
    print("image/rootfs.ext2: unrecognized format (expected gzip or ext2)"); raise SystemExit(1)
PY
else
    echo "missing image/rootfs.ext2 - run prepare-vendor-image.sh first" >&2
    exit 1
fi

echo "== patched kernel =="
if [ "$REBUILD_KERNEL" = 1 ] || [ ! -f image/bzImage.patched ]; then
    python3 patch_binary_artifact.py
else
    echo "image/bzImage.patched present (--rebuild-kernel to rebuild)"
fi

echo "== runtime initramfs (kernel + dropbear2222 payload) =="
RUNTIME_INITRD="$work_dir/bootinitramfs.runtime.gz" ./make-runtime-initrd.sh

echo "== VM disk =="
if [ "$RESET_DISK" = 1 ]; then
    rm -f synthetic-cf.img zd1200-vm.qcow2
    echo "VM disk state reset"
fi
if [ ! -f synthetic-cf.img ]; then
    python3 make-synthetic-cf.py
fi
# The serial number and MACs live in the board-data records on the CF image
# (read by the kernel's v54bsp driver; NOT patched into the kernel).  Rewrite
# them on every launch so env changes take effect.  MAC2 = MAC1 + 1.
python3 write-boarddata.py \
    --disk synthetic-cf.img \
    --serial "${ZD_SERIAL:-123456000789}" \
    --mac "${ZD_MAC1:-01:01:01:01:01:02}" \
    --model "${ZD_MODEL:-ZD1200}" \
    --customer "${ZD_CUSTOMER:-ruckus}"
if [ ! -f zd1200-vm.qcow2 ]; then
    qemu-img create -q -f qcow2 -F raw -b synthetic-cf.img zd1200-vm.qcow2
    echo "created qcow2 overlay: zd1200-vm.qcow2"
fi

echo "== launching QEMU =="
echo "  accelerator : ${ACCEL:-tcg}"
echo "  console     : attached here (Ctrl-A X to quit QEMU)"
echo "  web         : http://127.0.0.1:38080/  https://127.0.0.1:38443/admin10/login.jsp"
echo "  ssh (lab)   : ssh -i <key> -p 2222 root@127.0.0.1   (pubkey-only, dropbear 2026.94)"

exec env \
    KERNEL=image/bzImage.patched \
    INITRD="$work_dir/bootinitramfs.runtime.gz" \
    DISK_IMAGE="$work_dir/zd1200-vm.qcow2" \
    DISK_FORMAT=qcow2 DISK_CACHE=writeback SNAPSHOT=0 \
    NETWORK_MODE=user \
    EXTRA_HOSTFWD="tcp:127.0.0.1:2222-:2222" \
    ACCEL="${ACCEL:-tcg}" PACE_GUEST=0 \
    HTTP_PORT=38080 HTTPS_PORT=38443 \
    KERNEL_EXTRA="nohz=off" \
    ./run-zd1200-qemu.sh

# (--wait handled by the caller watching the ports; exec above never returns)
