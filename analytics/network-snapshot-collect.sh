#!/bin/sh
# Collect three stock read-only Stamgr views as opaque XML evidence through
# ZoneDirector's vendor-supplied, root-local getstatd socket.
set -eu

state_dir=/writable/zd1200-ping-monitor
snapshot_dir="$state_dir/snapshots"
current_client="$state_dir/current-client.xml"
bb=/usr/local/sbin/busybox

mkdir -p "$snapshot_dir"
umask 077

collect() {
    kind=$1
    temporary="$snapshot_dir/.${now}-${kind}.xml.$$"
    compressed="$snapshot_dir/.${now}-${kind}.xml.gz.$$"
    destination="$snapshot_dir/${now}-${kind}.xml.gz"
    /usr/local/sbin/zd1200-local-getstat "$kind" "$temporary"
    if ! grep -q '<ajax-response>' "$temporary"; then
        rm -f "$temporary" "$compressed"
        return 1
    fi
    if [ "$kind" = client ]; then
        current_temporary="$current_client.tmp.$$"
        cp -f "$temporary" "$current_temporary"
        chmod 600 "$current_temporary"
        mv -f "$current_temporary" "$current_client"
    fi
    if "$bb" gzip -c "$temporary" > "$compressed"; then
        chmod 600 "$compressed"
        mv -f "$compressed" "$destination"
        rm -f "$temporary"
    else
        rm -f "$temporary" "$compressed"
        return 1
    fi
}

now=${1:-$(date +%s)}
case "$now" in ''|*[!0-9]*) exit 2;; esac
collect ap
collect client
collect mesh
