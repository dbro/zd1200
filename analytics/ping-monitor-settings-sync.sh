#!/bin/sh
# Read the Ping Monitor preference directly through ZD's stock authenticated
# configuration API. The dedicated Monitoring Admin account can read this
# preference even though ZD correctly denies that account setpref access.
# Validated values are emitted on stdout for the monitor process to consume in
# memory; this helper persists no copy of the setting.
set -eu

state_dir=/writable/zd1200-ping-monitor
credentials="$state_dir/credentials.env"
base_url=https://127.0.0.1
cookie=/tmp/zd1200-ping-settings-cookie.$$

cleanup() {
    rm -f "$cookie"
}
trap cleanup EXIT HUP INT TERM

[ -r "$credentials" ] || exit 0
username=$(sed -n 's/^ZD_SNAPSHOT_USERNAME=//p' "$credentials" | head -n 1)
password=$(sed -n 's/^ZD_SNAPSHOT_PASSWORD=//p' "$credentials" | head -n 1)
[ -n "$username" ] && [ -n "$password" ] || exit 0
umask 077

curl -k -sS --connect-timeout 5 --max-time 15 -c "$cookie" \
    --data-urlencode "username=$username" \
    --data-urlencode "password=$password" --data 'ok=Login' \
    "$base_url/admin10/login.jsp" >/dev/null
app=$(curl -k -sS --connect-timeout 5 --max-time 15 \
    -b "$cookie" "$base_url/admin10/app.jsp")
csrf=$(printf '%s\n' "$app" \
    | sed -n "s/.*var csfrToken = '\([^']*\)'.*/\1/p" | head -n 1)
[ -n "$csrf" ] || exit 0

body='<ajax-request action="getconf" updater="zd1200-ping-monitor" comp="system" GET_PREFERENCE="zd1200-ping-monitor"></ajax-request>'
response=$(curl -k -sS --connect-timeout 5 --max-time 15 -b "$cookie" \
    -H "X-CSRF-Token: $csrf" -H 'X-Requested-With: XMLHttpRequest' \
    -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
    --data-binary "$body" "$base_url/admin10/_conf.jsp")

element=$(printf '%s\n' "$response" \
    | sed 's/<zd1200-ping-monitor /\
<zd1200-ping-monitor /g' \
    | sed -n 's/^\(<zd1200-ping-monitor [^>]*\/>\).*/\1/p' \
    | head -n 1)
[ -n "$element" ] || exit 0
attribute() {
    printf '%s\n' "$element" \
        | sed -n "s/.* $1=\"\([^\"]*\)\".*/\1/p" | head -n 1
}

ping_enabled=$(attribute ping-enabled)
ping_interval=$(attribute ping-interval)
snapshot_enabled=$(attribute snapshot-enabled)
snapshot_interval=$(attribute snapshot-interval)
updated=$(attribute updated-at)
valid=1
case "$ping_enabled" in 0|1) ;; *) valid=0;; esac
case "$snapshot_enabled" in 0|1) ;; *) valid=0;; esac
case "$updated" in ''|*[!0-9]*) valid=0;; esac
for interval in "$ping_interval" "$snapshot_interval"; do
    case "$interval" in ''|*[!0-9]*) valid=0;; esac
    [ "$valid" = 1 ] && [ "$interval" -ge 30 ] \
        && [ "$interval" -le 3600 ] || valid=0
done
[ "$valid" = 1 ] || exit 0

printf 'HAS_NATIVE_SETTINGS=1\n'
printf 'PING_ENABLED=%s\n' "$ping_enabled"
printf 'PING_INTERVAL_SECONDS=%s\n' "$ping_interval"
printf 'SNAPSHOT_ENABLED=%s\n' "$snapshot_enabled"
printf 'SNAPSHOT_INTERVAL_SECONDS=%s\n' "$snapshot_interval"
printf 'PREFERENCE_UPDATED_AT=%s\n' "$updated"
