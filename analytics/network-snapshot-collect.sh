#!/bin/sh
# Collect three stock read-only Stamgr views as opaque XML evidence through
# ZoneDirector's vendor-supplied, root-local getstatd socket.
set -eu

state_dir=/writable/zd1200-ping-monitor
snapshot_dir="$state_dir/snapshots"

mkdir -p "$snapshot_dir"
umask 077

collect() {
    kind=$1
    temporary="$snapshot_dir/.${now}-${kind}.xml.$$"
    destination="$snapshot_dir/${now}-${kind}.xml"
    /usr/local/sbin/zd1200-local-getstat "$kind" "$temporary"
    if grep -q '<ajax-response>' "$temporary"; then mv -f "$temporary" "$destination"; else rm -f "$temporary"; return 1; fi
}

now=$(date +%s)
collect ap
collect client
collect mesh
