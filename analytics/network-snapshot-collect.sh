#!/bin/sh
# Collect three stock read-only Stamgr views as opaque XML evidence.
# The destination is deliberately fixed to the local controller. Do not make
# this configurable: the runtime credential must never be sent off-controller.
set -eu

state_dir=/writable/zd1200-ping-monitor
snapshot_dir="$state_dir/snapshots"
credentials="$state_dir/credentials.env"
cookie="$state_dir/.session.cookie"
base_url=https://127.0.0.1

[ -r "$credentials" ] || exit 0
username=$(sed -n 's/^ZD_SNAPSHOT_USERNAME=//p' "$credentials" | head -n 1)
password=$(sed -n 's/^ZD_SNAPSHOT_PASSWORD=//p' "$credentials" | head -n 1)
[ -n "$username" ] && [ -n "$password" ] || exit 0

mkdir -p "$snapshot_dir"
umask 077
rm -f "$cookie"

# The appliance presents its own self-signed administrative certificate. This
# command talks only to 127.0.0.1 inside the guest; no routed URL is accepted.
curl -k -sS --connect-timeout 5 --max-time 15 -c "$cookie" \
    --data-urlencode "username=$username" --data-urlencode "password=$password" \
    --data 'ok=Login' \
    "$base_url/admin10/login.jsp" >/dev/null
app=$(curl -k -sS --connect-timeout 5 --max-time 15 -b "$cookie" "$base_url/admin10/app.jsp")
csrf=$(printf '%s\n' "$app" | sed -n "s/.*var csfrToken = '\([^']*\)'.*/\1/p" | head -n 1)
[ -n "$csrf" ] || exit 0

collect() {
    kind=$1
    body=$2
    temporary="$snapshot_dir/.${now}-${kind}.xml.$$"
    destination="$snapshot_dir/${now}-${kind}.xml"
    curl -k -sS --connect-timeout 5 --max-time 20 -b "$cookie" \
        -H "X-CSRF-Token: $csrf" -H 'X-Requested-With: XMLHttpRequest' \
        -H 'Content-Type: application/x-www-form-urlencoded; charset=UTF-8' \
        --data "$body" "$base_url/admin10/_cmdstat.jsp" >"$temporary"
    if grep -q '<ajax-response>' "$temporary"; then mv -f "$temporary" "$destination"; else rm -f "$temporary"; return 1; fi
}

now=$(date +%s)
collect ap '<ajax-request action="getstat" updater="zd1200.snapshot" comp="stamgr"><ap LEVEL="1" caller="ap-summary"/></ajax-request>'
collect client '<ajax-request action="getstat" updater="zd1200.snapshot" comp="stamgr"><client LEVEL="1"/><pieceStat start="0" number="300" pid="1" requestId="zd1200.snapshot"/></ajax-request>'
collect mesh '<ajax-request action="getstat" updater="zd1200.snapshot" comp="stamgr"><meshview/></ajax-request>'
rm -f "$cookie"
