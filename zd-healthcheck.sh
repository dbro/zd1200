#!/bin/sh
# Docker health check for the emulated guest, not merely its QEMU process.
#
# The TAP bridge intentionally has no host IP address, so the container cannot
# make a normal TCP request to the physical-LAN guest without changing the
# deployment's pass-through topology.  boot-initrd-handoff instead arranges
# for the guest to emit a serial readiness marker only after its `webs`
# process owns an HTTP or HTTPS listener.  A later shutdown marker makes this
# check fail until a new guest boot reaches that point.
set -eu

log_file="${ZD_SERIAL_LOG:-/tmp/zd1200-web.log}"

pgrep -f '[q]emu-system-i386' >/dev/null || exit 1
[ -r "$log_file" ] || exit 1

ready_line="$(grep -an 'ZD-HEALTH: guest web service ready' "$log_file" \
    | tail -n 1 | cut -d: -f1 || true)"
stop_line="$(grep -anE 'System Shutdown|Restarting system' "$log_file" \
    | tail -n 1 | cut -d: -f1 || true)"
filesystem_fault_line="$(grep -anE \
    'EXT2-fs error \(device hda4\)|Remounting filesystem read-only|hda4 filesystem repair failed' \
    "$log_file" | tail -n 1 | cut -d: -f1 || true)"

[ -n "$ready_line" ] || exit 1
[ -z "$stop_line" ] || [ "$ready_line" -gt "$stop_line" ] || exit 1
[ -z "$filesystem_fault_line" ] \
    || [ "$ready_line" -gt "$filesystem_fault_line" ] || exit 1
