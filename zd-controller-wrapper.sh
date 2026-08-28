#!/bin/sh

# Keep the vendor controller initialization running, but do not let its
# hardware-specific foreground wait prevent the remaining init scripts from
# executing in the VM.
apply_support_entitlement() {
    support_end_file=/etc/zd1200-support-entitlement-end
    [ -r "$support_end_file" ] || return 0
    support_end=$(sed -n '1p' "$support_end_file")
    case "$support_end" in ''|0|*[!0-9]*) return 0 ;; esac

    mount -o remount,rw / || return 1
    cd /etc/persistent-scripts || return 1
    mkdir -p patch-storage
    cd patch-storage || return 1

    if [ -f sys_wrapper.sh ]; then
        cp -f sys_wrapper.sh /bin/sys_wrapper.sh
    else
        cp -f /bin/sys_wrapper.sh sys_wrapper.sh
    fi
    current_md5=$(md5sum /bin/sys_wrapper.sh | awk '{print $1}')
    serial=$(cat /bin/SERIAL)

    cat > support <<EOF
<support-list>
  <support zd-serial-number="$serial" service-purchased="904" date-start="1262304000" date-end="$support_end" ap-support-number="licensed" DELETABLE="false"></support>
</support-list>
EOF
    sed 's/<support-list>/<support-list status="1">/' support > /writable/etc/airespider/support-list.xml
    tar -czf support.spt support

    # This is the vendor patch method: serve our generated support payload for
    # exactly these two actions, leaving every other sys_wrapper action intact.
    sed -i \
        -e '/verify-upload-support)/a \\
        cd /tmp\\
        cat /etc/persistent-scripts/patch-storage/support > support\\
        echo "OK"\\
        ;;\\
    verify-upload-support-unpatched)' \
        -e '/wget-support-entitlement)/a \\
        cat /etc/persistent-scripts/patch-storage/support.spt > "/tmp/$1"\\
        echo "OK"\\
        ;;\\
    wget-support-entitlement-unpatched)' \
        /bin/sys_wrapper.sh || return 1

    new_md5=$(md5sum /bin/sys_wrapper.sh | awk '{print $1}')
    sed -i "s/$current_md5/$new_md5/" /file_list.txt
    bsp set model ZD3050 >/dev/null 2>&1 || true
    bsp commit >/dev/null 2>&1 || true
    mount -o remount,ro / || true
    echo "ZD-SUPPORT: applied vendor-compatible finite entitlement patch" >/dev/console
}

(
    /bin/sh /etc/init.d/controller.vendor "$@" >/tmp/controller-vendor.log 2>&1
    apply_support_entitlement
) &


# Let rcS reach S98 normally.  Starting S98 here as well created a second
# web server and a second diagnostic process.
/bin/busybox sleep 20
exit 0
