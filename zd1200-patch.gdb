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
# Synthetic board-data query: MAC 02:52:54:12:00:01 and success.
set {unsigned char}0xc123bc20 = 0xc7
set {unsigned char}0xc123bc21 = 0x02
set {unsigned char}0xc123bc22 = 0x02
set {unsigned char}0xc123bc23 = 0x52
set {unsigned char}0xc123bc24 = 0x54
set {unsigned char}0xc123bc25 = 0x12
set {unsigned char}0xc123bc26 = 0x66
set {unsigned char}0xc123bc27 = 0xc7
set {unsigned char}0xc123bc28 = 0x42
set {unsigned char}0xc123bc29 = 0x04
set {unsigned char}0xc123bc2a = 0x00
set {unsigned char}0xc123bc2b = 0x01
set {unsigned char}0xc123bc2c = 0x31
set {unsigned char}0xc123bc2d = 0xc0
set {unsigned char}0xc123bc2e = 0xc3
disable 1
continue
end
continue
