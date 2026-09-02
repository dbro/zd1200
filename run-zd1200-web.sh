#!/usr/bin/env bash
set -euo pipefail

work_dir="$(cd "$(dirname "$0")" && pwd)"
log_file="${LOG_FILE:-/tmp/zd1200-web.log}"
qemu_pid=""
limiter_pid=""
started_at=$SECONDS
high_cpu_samples=0
ready=0
http_status=""
http_port="${HTTP_PORT:-38080}"
https_port="${HTTPS_PORT-38443}"
network_mode="${NETWORK_MODE:-user}"
web_probe="${WEB_PROBE:-auto}"
state_dir="${STATE_DIR:-$work_dir}"
synthetic_disk="${SYNTHETIC_DISK:-$state_dir/synthetic-cf.img}"
persistent_disk="${PERSISTENT_DISK:-$state_dir/zd1200-vm.qcow2}"
patched_kernel="${PATCHED_KERNEL:-$state_dir/bzImage.patched}"
vm_snapshot="${VM_SNAPSHOT:-0}"
cpu_limit="${CPU_LIMIT:-}"
control_socket="${CONTROL_SOCKET:-/tmp/zd1200-control.sock}"
if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
    vm_accel=kvm
else
    vm_accel=tcg
fi

cleanup() {
    trap - EXIT INT TERM
    if [[ "$qemu_pid" =~ ^[0-9]+$ ]] && (( qemu_pid > 1 )); then
        if [[ "$limiter_pid" =~ ^[0-9]+$ ]] && (( limiter_pid > 1 )); then
            kill "$limiter_pid" 2>/dev/null || true
            wait "$limiter_pid" 2>/dev/null || true
        fi
        # Ask the guest's stock PID 1 to perform its normal repository flush
        # and shutdown sequence. The patched restart path resets QEMU after
        # that sequence, so stop QEMU as soon as the serial restart marker is
        # visible rather than allowing the next boot to proceed.
        if [ -S "$control_socket" ]; then
            marker_start=$(wc -l < "$log_file" 2>/dev/null || echo 0)
            if python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(3); s.connect(sys.argv[1]); s.sendall(b"reboot\n"); s.close()' \
                "$control_socket" 2>/dev/null; then
                echo "Requested orderly ZoneDirector guest shutdown."
                for _ in {1..90}; do
                    if tail -n "+$((marker_start + 1))" "$log_file" 2>/dev/null \
                        | grep -q 'Restarting system\.'; then
                        echo "ZoneDirector guest completed its shutdown sequence."
                        break
                    fi
                    kill -0 "$qemu_pid" 2>/dev/null || break
                    sleep 1
                done
            fi
        fi
        kill -CONT -- "-$qemu_pid" 2>/dev/null || true
        kill -TERM -- "-$qemu_pid" 2>/dev/null || true
        for _ in {1..100}; do
            kill -0 "$qemu_pid" 2>/dev/null || break
            sleep 0.1
        done
        kill -KILL -- "-$qemu_pid" 2>/dev/null || true
        wait "$qemu_pid" 2>/dev/null || true
    fi
    rm -f -- "$control_socket"
}
on_signal() {
    cleanup
    exit 0
}
trap cleanup EXIT
trap on_signal INT TERM

cd "$work_dir" || exit 1
mkdir -p "$state_dir"

if [ ! -f image/bootinitramfs.gz ]; then
    echo "Missing image/bootinitramfs.gz" >&2
    exit 1
fi
runtime_initrd="${RUNTIME_INITRD:-$state_dir/bootinitramfs.runtime.gz}"
RUNTIME_INITRD="$runtime_initrd" "$work_dir/make-runtime-initrd.sh"
if ! command -v qemu-img >/dev/null 2>&1; then
    echo "qemu-img is required" >&2
    exit 1
fi
echo "Preparing signature-patched kernel ..."
python3 "$work_dir/patch_binary_artifact.py" \
    --artifact zd1200_kernel_elf \
    --in "$work_dir/image/bzImage" \
    --out "$patched_kernel" \
    --vmlinux "$work_dir/image/vmlinux"
if [ ! -f "$synthetic_disk" ]; then
    echo "Creating persistent synthetic CF base image in $state_dir ..."
    SYNTHETIC_DISK="$synthetic_disk" python3 "$work_dir/make-synthetic-cf.py"
fi
# The serial number and MACs live in the board-data records on the CF image
# (read by the kernel's v54bsp driver; NOT patched into the kernel).  When an
# operator does not explicitly set both values, create one unique local
# identity in the persistent state volume. MAC2 = MAC1 + 1.
IFS=$'\t' read -r identity_serial identity_mac identity_source < <(
    python3 "$work_dir/zd_identity.py" --state-dir "$state_dir" \
        --serial "${ZD_SERIAL:-}" --mac "${ZD_MAC1:-}"
)
echo "Board identity: $identity_serial / $identity_mac ($identity_source)"
python3 "$work_dir/write-boarddata.py" \
    --disk "$synthetic_disk" \
    --serial "$identity_serial" \
    --mac "$identity_mac" \
    --model "${ZD_MODEL:-ZD1200}" \
    --customer "${ZD_CUSTOMER:-ruckus}"
if [ ! -f "$persistent_disk" ]; then
    qemu-img create -q -f qcow2 -F raw -b "$synthetic_disk" "$persistent_disk"
    echo "Created persistent VM disk overlay: $persistent_disk"
fi

: > "$log_file"
setsid env KERNEL="$patched_kernel" INITRD="$runtime_initrd" \
    DISK_IMAGE="$persistent_disk" DISK_FORMAT=qcow2 DISK_CACHE=writeback SNAPSHOT="$vm_snapshot" PACE_GUEST=0 \
    ACCEL="$vm_accel" \
    HTTP_PORT="$http_port" \
    HTTPS_PORT="$https_port" \
    NETWORK_MODE="$network_mode" \
    TAP_IF="${TAP_IF:-tap-zd}" \
    CONTROL_SOCKET="$control_socket" \
    QEMU_NIC_MAC="$identity_mac" \
    nice -n 10 ./run-zd1200-qemu.sh \
    >>"$log_file" 2>&1 </dev/null &
qemu_pid=$!

# CPU_LIMIT is opt-in for KVM. TCG retains its historical 60% safety cap.
if [ -z "$cpu_limit" ] && [ "$vm_accel" = tcg ]; then
    cpu_limit=60
fi
if [ -n "$cpu_limit" ]; then
    if ! [[ "$cpu_limit" =~ ^[0-9]+$ ]] || (( cpu_limit < 1 || cpu_limit > 95 )); then
        echo "CPU_LIMIT must be an integer from 1 through 95." >&2
        exit 2
    fi
    python3 "$work_dir/limit-process-cpu.py" "$qemu_pid" "$cpu_limit" &
    limiter_pid=$!
    echo "QEMU CPU duty cycle capped at ${cpu_limit}% while the VM runs."
fi

case "$web_probe" in
    auto)
        if [ "$network_mode" = tap ] || [ "$network_mode" = bridge ]; then
            web_probe=off
        else
            web_probe=on
        fi
        ;;
    on|off) ;;
    *)
        echo "WEB_PROBE must be auto, on, or off." >&2
        exit 2
        ;;
esac

wait_seconds="${WEB_WAIT_SECONDS:-${WEB_WAIT_LOOPS:-180}}"
if ! [[ "$wait_seconds" =~ ^[0-9]+$ ]] || (( wait_seconds < 1 )); then
    echo "WEB_WAIT_SECONDS must be a positive integer." >&2
    exit 2
fi
if [ "$web_probe" = off ]; then
    echo "ZD1200 is starting with external web readiness checks."
    echo "First setup without DHCP: https://192.168.0.2/"
    echo "Set the permanent guest address in the ZoneDirector wizard."
    echo "The TAP bridge is intentionally unnumbered on the Docker host."
    ready=1
elif [ -n "$cpu_limit" ]; then
    echo "ZD1200 is starting; waiting for the web service..."
    echo "Startup is CPU-limited and has a ${wait_seconds}s readiness deadline."
else
    echo "ZD1200 is starting; waiting for the web service..."
    echo "Startup runs at full speed and has a ${wait_seconds}s readiness deadline."
fi
if [ "$web_probe" != off ]; then
    if [ "$network_mode" = tap ] || [ "$network_mode" = bridge ]; then
        echo "WEB_PROBE=on is not supported with NETWORK_MODE=$network_mode; use auto or off." >&2
        exit 2
    else
        probe_base="https://127.0.0.1:$https_port"
    fi
fi
deadline=$((SECONDS + wait_seconds))
next_notice=$((SECONDS + 30))
while (( ready == 0 && SECONDS < deadline )); do
    http_status="$(curl -ksS --max-time 3 -o /tmp/zd1200-login.html \
        -w '%{http_code}' \
        "$probe_base/admin10/login.jsp" \
        2>/dev/null || true)"
    if { [ "$http_status" = 302 ] && rg -q 'wizard\.jsp' /tmp/zd1200-login.html; } \
        || { [ "$http_status" = 200 ] \
            && [ "$(wc -c < /tmp/zd1200-login.html)" -gt 1000 ] \
            && ! rg -q '~(SystemName|Username|GP_Login)~' /tmp/zd1200-login.html; }; then
        if [ "$http_status" = 302 ] || rg -q 'form-wizard|Setup Wizard' /tmp/zd1200-login.html; then
            # Seeing HTML is insufficient: the stock factory session has an
            # empty CID, while its AJAX modules still enforce a CSRF match.
            # Confirm that our factory-only compatibility patch reaches the
            # backend before inviting the user to complete the wizard.
            cookie_jar="/tmp/zd1200-web-cookie.$qemu_pid"
            factory_reply="/tmp/zd1200-factory-probe.$qemu_pid.xml"
            curl -ksS --max-time 5 -c "$cookie_jar" -b "$cookie_jar" \
                -o /dev/null "$probe_base/admin10/wizard.jsp" 2>/dev/null || true
            curl -ksS --max-time 8 -c "$cookie_jar" -b "$cookie_jar" \
                -H 'X-Requested-With: XMLHttpRequest' \
                -H 'X-Rico-Version: 1.1.2' -H 'X-CSRF-Token;' \
                -H 'Content-Type: text/xml' \
                --data-binary '<ajax-request action="getconf" comp="system" updater="readiness-probe"/>' \
                -o "$factory_reply" "$probe_base/admin10/_conf.jsp" 2>/dev/null || true
            if ! rg -q '<ajax-response>.*<system>' "$factory_reply" 2>/dev/null; then
                rm -f "$cookie_jar" "$factory_reply"
                sleep 1
                continue
            fi
            rm -f "$cookie_jar" "$factory_reply"
            ready_url="$probe_base/admin10/wizard.jsp"
            ready_kind="factory setup wizard"
        else
            ready_url="$probe_base/admin10/login.jsp"
            ready_kind="login page"
        fi
        echo "ZD1200 $ready_kind is ready:"
        if [ "$network_mode" = tap ]; then
            echo "HTTP:  http://$guest_ip/"
        else
            echo "HTTP:  http://127.0.0.1:$http_port/"
        fi
        echo "HTTPS: $ready_url"
        if [ "$vm_accel" = kvm ]; then
            echo "Hardware acceleration: KVM"
        fi
        echo "Press Ctrl-C to stop the virtual ZoneDirector."
        ready=1
        break
    fi
    if ! kill -0 "$qemu_pid" 2>/dev/null; then
        echo "QEMU exited before the web service became ready." >&2
        tail -160 "$log_file" >&2
        exit 1
    fi
    if (( SECONDS >= next_notice )); then
        echo "Still initializing ($((SECONDS - started_at))s elapsed since launch)..."
        next_notice=$((next_notice + 30))
    fi
    sleep 1
done

if (( ready == 0 )); then
    echo "Timed out waiting for the web service." >&2
    tail -160 "$log_file" >&2
    exit 1
fi

# Keep supervising the VM instead of blocking in wait(1).  The old embedded
# kernel should idle with HLT; sustained full-core TCG use indicates a guest
# spin loop and is not acceptable on a laptop.
clock_ticks="$(getconf CLK_TCK)"
previous_ticks="$(awk '{print $14 + $15}' "/proc/$qemu_pid/stat")"
previous_sample=$SECONDS
while kill -0 "$qemu_pid" 2>/dev/null; do
    sleep 5
    current_ticks="$(awk '{print $14 + $15}' "/proc/$qemu_pid/stat" 2>/dev/null || echo "$previous_ticks")"
    current_sample=$SECONDS
    sample_seconds=$((current_sample - previous_sample))
    (( sample_seconds > 0 )) || sample_seconds=1
    cpu=$(( (current_ticks - previous_ticks) * 100 / clock_ticks / sample_seconds ))
    previous_ticks="$current_ticks"
    previous_sample="$current_sample"
    if (( cpu >= 95 )); then
        high_cpu_samples=$((high_cpu_samples + 1))
    else
        high_cpu_samples=0
    fi
    if (( high_cpu_samples >= 4 )); then
        echo "QEMU stayed above 95% CPU for 20 seconds; stopping it to protect the host." >&2
        exit 3
    fi
done

echo "QEMU exited." >&2
exit 1
