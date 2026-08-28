# Historical 10.5.1 GDB notes; not an executable patch path.
#
# This file formerly replaced global kernel_halt() and machine_restart() with
# returns after an early hardware breakpoint. That prevents normal controller
# restarts and cannot intercept the NAR5520 watchdog before it runs. The
# maintained patch path is patch_binary_artifact.py with the
# zd1200_kernel_elf catalog rules, applied before QEMU starts.
#
# Kept only so links to the original reverse-engineering work remain valid.
