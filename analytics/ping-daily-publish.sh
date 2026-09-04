#!/bin/sh
set -eu

bb=/usr/local/sbin/busybox
[ -x "$bb" ] || bb=/bin/busybox
state_dir=/writable/zd1200-ping-monitor
daily_dir=$state_dir/daily
exporter=/usr/local/sbin/zd1200-ping-export
targets=$state_dir/targets.json
manifest=$state_dir/daily-manifest.json
mode=publish
if [ "${1:-}" = backfill ]; then
    mode=backfill
    shift
fi
now=${1:-$(date +%s)}
day=$((now / 86400 * 86400))
cutoff=$((day - 30 * 86400))
current=$daily_dir/ping-current.bin.gz
marker=$daily_dir/current-day
backfill_marker=$daily_dir/.backfill-v2

mkdir -p "$daily_dir"

if [ "$mode" = backfill ]; then
    # The database remains the full-precision source of truth. On the first
    # daily-format boot, materialize every retained historical UTC day once.
    if [ ! -e "$backfill_marker" ]; then
        historical=$cutoff
        while [ "$historical" -lt "$day" ]; do
            destination="$daily_dir/ping-$historical.bin.gz"
            raw="$daily_dir/.ping-$historical.bin.$$"
            compressed="$daily_dir/.ping-$historical.bin.gz.$$"
            trap 'rm -f "$raw" "$compressed"' EXIT HUP INT TERM
            "$exporter" export-day "$historical" > "$raw"
            # A header-only day contains no observations and need not be
            # advertised or downloaded by the browser.
            size=$(wc -c < "$raw" | tr -d ' ')
            if [ "$size" -gt 576 ]; then
                "$bb" gzip -6 -c "$raw" > "$compressed"
                "$bb" gzip -t "$compressed"
                chmod 644 "$compressed"
                mv -f "$compressed" "$destination"
            else
                rm -f "$destination"
            fi
            rm -f "$raw" "$compressed"
            trap - EXIT HUP INT TERM
            historical=$((historical + 86400))
        done
        : > "$backfill_marker"
        chmod 600 "$backfill_marker"
    fi
    targets_tmp="$state_dir/.targets.json.$$"
    manifest_tmp="$state_dir/.daily-manifest.json.$$"
    trap 'rm -f "$targets_tmp" "$manifest_tmp"' EXIT HUP INT TERM
    "$exporter" targets-json > "$targets_tmp"
    chmod 644 "$targets_tmp"
    mv -f "$targets_tmp" "$targets"
    "$exporter" manifest > "$manifest_tmp"
    chmod 644 "$manifest_tmp"
    mv -f "$manifest_tmp" "$manifest"
    trap - EXIT HUP INT TERM
    exit 0
fi

previous=
if [ -r "$marker" ]; then
    previous=$(sed -n '1p' "$marker")
fi
case "$previous" in
    ''|*[!0-9]*) previous= ;;
esac
if [ -n "$previous" ] && [ "$previous" -ne "$day" ] && [ -r "$current" ]; then
    mv -f "$current" "$daily_dir/ping-$previous.bin.gz"
fi

raw="$daily_dir/.ping-current.bin.$$"
compressed="$daily_dir/.ping-current.bin.gz.$$"
marker_tmp="$daily_dir/.current-day.$$"
targets_tmp="$state_dir/.targets.json.$$"
manifest_tmp="$state_dir/.daily-manifest.json.$$"
trap 'rm -f "$raw" "$compressed" "$marker_tmp" "$targets_tmp" "$manifest_tmp"' EXIT HUP INT TERM

"$exporter" export-day "$day" > "$raw"
"$bb" gzip -6 -c "$raw" > "$compressed"
"$bb" gzip -t "$compressed"
printf '%s\n' "$day" > "$marker_tmp"
chmod 644 "$compressed" "$marker_tmp"
mv -f "$compressed" "$current"
mv -f "$marker_tmp" "$marker"

for file in "$daily_dir"/ping-*.bin.gz; do
    [ -e "$file" ] || continue
    value=${file##*/ping-}
    value=${value%.bin.gz}
    case "$value" in
        ''|*[!0-9]*) continue ;;
    esac
    if [ "$value" -lt "$cutoff" ]; then
        rm -f "$file"
    fi
done

"$exporter" targets-json > "$targets_tmp"
chmod 644 "$targets_tmp"
mv -f "$targets_tmp" "$targets"
"$exporter" manifest > "$manifest_tmp"
chmod 644 "$manifest_tmp"
mv -f "$manifest_tmp" "$manifest"
rm -f "$raw"
trap - EXIT HUP INT TERM
