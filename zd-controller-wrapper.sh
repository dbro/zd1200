#!/bin/sh

# Keep the vendor controller initialization running, but do not let its
# hardware-specific foreground wait prevent the remaining init scripts from
# executing in the VM.
/bin/sh /etc/init.d/controller.vendor "$@" >/tmp/controller-vendor.log 2>&1 &

# Let rcS reach S98 normally.  Starting S98 here as well created a second
# web server and a second diagnostic process.
/bin/busybox sleep 20
exit 0
