#!/bin/sh

# One low-cost snapshot used to identify the file/device mapping implicated by
# the post-wizard emfd page-table corruption.  It runs once and exits.
(
    /bin/busybox sleep 35
    for status in /proc/[0-9]*/status; do
        /bin/busybox grep '^Name:[[:space:]]*emfd$' "$status" >/dev/null 2>&1 || continue
        pid="${status#/proc/}"
        pid="${pid%/status}"
        {
            echo "ZD-MEMORY-SNAPSHOT: emfd pid $pid"
            echo "ZD-MEMORY-SNAPSHOT: process table"
            /bin/busybox ps
            echo "ZD-MEMORY-SNAPSHOT: top"
            /bin/busybox top -n 1 2>/dev/null || true
            echo "ZD-MEMORY-SNAPSHOT: maps"
            /bin/busybox cat "/proc/$pid/maps"
            echo "ZD-MEMORY-SNAPSHOT: file descriptors"
            /bin/busybox ls -l "/proc/$pid/fd"
            echo "ZD-MEMORY-SNAPSHOT: dump directory"
            /bin/busybox ls -la /etc/airespider/dump
            echo "ZD-MEMORY-SNAPSHOT: complete"
        } >/dev/console 2>&1
        break
    done
) &

exit 0
