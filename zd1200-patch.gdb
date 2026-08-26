set pagination off
set confirm off
hbreak *0xc10416f0
commands 1
printf "ZD-GDB: 10.5.1 kernel breakpoint reached\n"
# kernel_halt(): the VM has no appliance power controller.
set {unsigned char}0xc10416f0 = 0xc3
# rks_pkt_trace_init(): avoid creating the hardware-oriented tif0 path.
set {unsigned char}0xc145090f = 0x31
set {unsigned char}0xc1450910 = 0xc0
set {unsigned char}0xc1450911 = 0xc3
# machine_restart(): QEMU supervises restart/termination.
set {unsigned char}0xc1015db0 = 0xc3
# Physical COB7402 reset/watchdog function.
set {unsigned char}0xc123c600 = 0x31
set {unsigned char}0xc123c601 = 0xc0
set {unsigned char}0xc123c602 = 0xc3
# Skip the physical board-data retry/recovery path.
set {unsigned char}0xc123d06d = 0xe9
set {unsigned char}0xc123d06e = 0x14
set {unsigned char}0xc123d06f = 0xff
set {unsigned char}0xc123d070 = 0xff
set {unsigned char}0xc123d071 = 0xff
# NOTE: serial number and MACs are NOT patched here.  The v54bsp driver
# reads them from the board-data records on the CompactFlash image (written
# by write-boarddata.py: magic "SKCR" at region2_start+0x8000), so the
# stock board-data query code returns the CF-provided values as-is.
disable 1
continue
end
continue
