#!/bin/sh
# Bridge ZD's persistent setpref journal into a small root-owned settings file.
# The browser remains the only writer of the native preference. This helper
# accepts only the five validated attributes from that exact preference node.
set -eu

bb=/usr/local/sbin/busybox
state_dir=/writable/zd1200-ping-monitor
journal=/writable/etc/airespider/ajax_config.log
cache=$state_dir/settings-cache.conf

attribute() {
    printf '%s\n' "$1" \
        | "$bb" sed -n "s/.* $2=\"\([^\"]*\)\".*/\1/p" \
        | "$bb" head -n 1
}

emit_if_valid() {
    ping_enabled=$1
    ping_interval=$2
    snapshot_enabled=$3
    snapshot_interval=$4
    updated=$5
    valid=1
    case "$ping_enabled" in 0|1) ;; *) valid=0;; esac
    case "$snapshot_enabled" in 0|1) ;; *) valid=0;; esac
    case "$updated" in ''|*[!0-9]*) valid=0;; esac
    for interval in "$ping_interval" "$snapshot_interval"; do
        case "$interval" in ''|*[!0-9]*) valid=0;; esac
        [ "$valid" = 1 ] && [ "$interval" -ge 30 ] \
            && [ "$interval" -le 3600 ] || valid=0
    done
    [ "$valid" = 1 ] || return 1
    printf 'HAS_NATIVE_SETTINGS=1\n'
    printf 'PING_ENABLED=%s\n' "$ping_enabled"
    printf 'PING_INTERVAL_SECONDS=%s\n' "$ping_interval"
    printf 'SNAPSHOT_ENABLED=%s\n' "$snapshot_enabled"
    printf 'SNAPSHOT_INTERVAL_SECONDS=%s\n' "$snapshot_interval"
    printf 'PREFERENCE_UPDATED_AT=%s\n' "$updated"
}

mkdir -p "$state_dir"
umask 077
if [ -r "$journal" ]; then
    record=$("$bb" grep 'updater="zd1200-ping-monitor"' "$journal" 2>/dev/null \
        | "$bb" grep 'action="setpref"' \
        | "$bb" tail -n 1 || true)
    element=$(printf '%s\n' "$record" \
        | "$bb" sed -n 's/.*\(<zd1200-ping-monitor [^>]*\/>\).*/\1/p')
    if [ -n "$element" ]; then
        candidate=$(emit_if_valid \
            "$(attribute "$element" ping-enabled)" \
            "$(attribute "$element" ping-interval)" \
            "$(attribute "$element" snapshot-enabled)" \
            "$(attribute "$element" snapshot-interval)" \
            "$(attribute "$element" updated-at)" || true)
        if [ -n "$candidate" ]; then
            temporary=$cache.tmp.$$
            printf '%s\n' "$candidate" > "$temporary"
            chmod 600 "$temporary"
            mv -f "$temporary" "$cache"
        fi
    fi
fi

[ -r "$cache" ] || exit 0
ping_enabled=$(sed -n 's/^PING_ENABLED=//p' "$cache" | head -n 1)
ping_interval=$(sed -n 's/^PING_INTERVAL_SECONDS=//p' "$cache" | head -n 1)
snapshot_enabled=$(sed -n 's/^SNAPSHOT_ENABLED=//p' "$cache" | head -n 1)
snapshot_interval=$(sed -n 's/^SNAPSHOT_INTERVAL_SECONDS=//p' "$cache" | head -n 1)
updated=$(sed -n 's/^PREFERENCE_UPDATED_AT=//p' "$cache" | head -n 1)
emit_if_valid "$ping_enabled" "$ping_interval" \
    "$snapshot_enabled" "$snapshot_interval" "$updated"
