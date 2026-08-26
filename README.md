# Virtual ZoneDirector 1200 — 10.5.1.0.282 proof of concept

This project boots the x86 ZoneDirector 1200 software in QEMU/KVM and exposes
it through a TAP-backed Ethernet interface. It is an experimental, unsupported
lab port; it is not affiliated with or endorsed by Ruckus.

## Firmware and licensing boundary

This repository intentionally contains **no Ruckus binaries, firmware, root
filesystems, keys, or AP images**. Obtain the matching ZD1200 10.5.1.0.282
package yourself from [Ruckus Support](https://support.ruckuswireless.com/software/4537-zd1200-10-5-1-ga-refresh-9-software-release), and ensure that your download, decryption and use comply with the applicable terms.

The included [MIT License](LICENSE) applies only to this repository's original
glue code and documentation. It grants no rights to Ruckus materials.

`prepare-vendor-image.sh` accepts a user-supplied *decrypted* archive and
creates the ignored `image/` directory locally. An online decryption tool
is [here](https://ms264556.net/ruckus/DecryptRuckusBackups). It expects
this exact archive SHA-256:

```
64dfbf4d67cc65cafa0e258e426c664c7387b1219209ec893b9b1e41ab202cb8
```

The script verifies the archive identity, vendor metadata and vendor kernel/
rootfs MD5 values before extracting `bzImage`, `vmlinux`, `rootfs.ext2`, the
base initramfs, and the complete AP/aidfs payload. Generated output is ignored
by Git and must never be committed.

## Prerequisites

- x86_64 Linux host with KVM (`/dev/kvm`) and Docker Compose.
- A dedicated Layer-2 path for the guest if it will manage real APs. The host
  must not have an IP address on that adapter, bridge, or TAP interface.
- Host tools for preparation: Bash, Python 3, `tar`, `gzip`, `cpio`,
  `openssl`, GNU binutils (`as`, `ld`), `md5sum`, and `sha256sum`.

## Build the local image

```sh
cp .env.example .env
./prepare-vendor-image.sh /absolute/path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img.tgz
docker compose up -d --build
```

`run-zd1200-lab.sh` invokes the generalized binary patcher when it builds
`image/bzImage.patched`. The default invocation preserves the former
`patch-kernel.py` behavior:

```sh
python3 patch_binary_artifact.py
```

## Generalized binary patcher

Binary matching and container handling live in `patch_binary_artifact.py`.
The reusable artifact IDs and their patch definitions live separately in
`binary_patch_catalog.py`. Every patch names an artifact ID, so all patches for
one payload share its extraction and rebuild handler and cannot accidentally be
mixed with patches for another payload.

The current catalog defines:

```text
zd1200_kernel_elf  ELF32 x86 kernel inside the vendor bzImage gzip member
r600_wlan_ko       raw ELF32 big-endian MIPS R600 wlan.ko
```

The ZD1200 handler locates and decompresses the kernel ELF, applies all five
registered patches, recompresses it, pads the gzip member to its original
length, and splices it back without moving the vendor loader tail. Defaults are
compatible with the lab launcher:

```sh
python3 patch_binary_artifact.py \
  --artifact zd1200_kernel_elf \
  --in image/bzImage \
  --out image/bzImage.patched
```

The R600 rule operates on an already-extracted module; BL7 filesystem
extraction and rebuilding remain a separate packaging step:

```sh
python3 patch_binary_artifact.py \
  --artifact r600_wlan_ko \
  --in /path/to/wlan.ko \
  --out /path/to/wlan.ko.patched
```

Signatures support `??` wildcard bytes. The patcher requires each original or
already-patched signature to occur exactly once, rejects mixed-artifact rule
sets, checks for overlapping writes, and verifies all patched signatures before
writing the output. Reapplying a patch is idempotent.

`ZD_IMAGE_DIR` in `.env` defaults to `./image`. Set it to an external absolute
path if the large, generated files should live elsewhere. `.env`, `image/`, VM
disks, logs and state are excluded by `.gitignore`.

For a physical Ethernet attachment, configure the dedicated adapter in
`host/zd1200-bridge.env.example`, then install the files as follows:

```sh
sudo install -m 0755 host/zd1200-bridge /usr/local/sbin/zd1200-bridge
sudo install -m 0644 host/zd1200-bridge.service /etc/systemd/system/
sudo install -m 0600 host/zd1200-bridge.env.example /etc/default/zd1200-bridge
sudoedit /etc/default/zd1200-bridge
sudo systemctl daemon-reload
sudo systemctl enable --now zd1200-bridge.service
```

The bridge service refuses to repurpose an interface carrying the host default
route. Set `ZD_USB_MAC` in its configuration to the dedicated adapter's MAC as
an additional guard.

After the first factory-wizard completion, restart the container once. That
allows the configured system to generate its persistent Dropbear host key and
start administrative SSH.

## Runtime notes

- Keep `KERNEL_EXTRA: nohz=off`. The 2.6.32 guest's tickless-idle path spins a
  host CPU while idle; this option reduced observed KVM QEMU CPU use from about
  25% to about 2% of one host CPU.
- `CPU_LIMIT` is intentionally absent for KVM. The old duty-cycle limiter only
  added SIGSTOP/SIGCONT pauses and delayed useful work. `nice -n 10` remains
  and only lowers scheduling priority under contention.
- The synthetic platform identity and guest MAC are fixed by the runtime patch.
  Do not run two instances on the same Layer-2 network without changing that
  implementation and validating the board-data checksum behavior.
- The generated state volume contains controller configuration and AP state.
  Back it up before experiments; deleting it returns the VM to factory setup.

## Security warning

This is a lab proof of concept, not a hardened appliance. The boot handoff
seeds legacy Unix account hashes so the factory setup and recovery paths work.
Treat those credentials as known to anyone who can read this repository.
Do not expose the VM's HTTPS, SSH, FTP, management network, or host Docker API
to untrusted networks. Use a dedicated management VLAN and firewall rules.

## Repository contents

The source-only public repository should contain these files:

```text
Dockerfile                    docker-compose.yml             .env.example
boot-initrd-handoff           boot-initrd-init               boot-initrd-inittab
make-boot-initrd.sh           make-runtime-initrd.sh         prepare-vendor-image.sh
make-synthetic-cf.py          pivot-exec.S                   run-zd1200-qemu.sh
run-zd1200-web.sh             zd-controller-wrapper.sh       zd-memory-snapshot.sh
zd1200-patch.gdb              limit-process-cpu.py            patch_binary_artifact.py
binary_patch_catalog.py
host/zd1200-bridge            host/zd1200-bridge.service     host/zd1200-bridge.env.example
README.md                     LICENSE                         .gitignore                     .dockerignore
```

`limit-process-cpu.py` is retained only for the automatic TCG fallback, not
normal KVM operation.

## Optional future improvements

1. Add a stable synthetic serial number if a blank value creates a practical
   problem. [See these instructions for assigning serial number, MAC, etc](https://ms264556.net/ruckus/MigrateDeadZoneDirector#restore-your-old-serial-number-and-mac) (thank you, @ms264556!)
2. Replace the fixed seeded Unix password hashes with per-deployment secrets,
   but only after confirming the factory wizard, recovery, and Dropbear flows
   remain recoverable.
3. Make the synthetic MAC configurable only after validating the corresponding
   board-data behavior, so two lab instances can safely coexist.

## Known limitation

Do not use the ZoneDirector web-upgrade workflow inside this VM. QEMU boots an
external kernel and initramfs, so an in-guest upgrade would create a mixed
version unless this port is updated and rebuilt for that release.
