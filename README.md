# Virtual ZoneDirector 1200 — 10.5.1.0.282 proof of concept

This project boots the x86 ZoneDirector 1200 software in QEMU/KVM and exposes
it through a TAP-backed Ethernet interface. It is an experimental, unsupported
lab port; it is not affiliated with or endorsed by Ruckus.

The supported-host policy, browser-builder design, release matrix, validation
gates, and public-beta milestones are tracked in [ROADMAP.md](ROADMAP.md).
The source/binary boundary and release-gated diagnostic payload inventory are
tracked in [PROVENANCE.md](PROVENANCE.md).
The physical-AP acceptance and regression procedure is in
[VALIDATION.md](VALIDATION.md).

## Firmware and licensing boundary

This repository intentionally contains **no Ruckus binaries, firmware, root
filesystems, keys, or AP images**. Obtain the matching ZD1200 10.5.1.0.282
package yourself from [Ruckus Support](https://support.ruckuswireless.com/software/4537-zd1200-10-5-1-ga-refresh-9-software-release), and ensure that your download, decryption and use comply with the applicable terms.

The included [MIT License](LICENSE) applies only to this repository's original
glue code and documentation. It grants no rights to Ruckus materials.

`prepare-vendor-image.sh` accepts either the original opaque encrypted download
or its already-decrypted gzip-TAR form and creates the ignored `image/`
directory locally. Legacy TAC decryption is performed locally with an adapted,
attributed [aioruckus](https://github.com/ms264556/aioruckus) BSD-0-Clause
implementation; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). It
expects this exact decrypted archive SHA-256:

```
64dfbf4d67cc65cafa0e258e426c664c7387b1219209ec893b9b1e41ab202cb8
```

The script verifies the encrypted input hash when applicable, then verifies the
decrypted archive identity, safe TAR layout/links, vendor metadata, and vendor
kernel/rootfs MD5 values before extracting `bzImage`, `vmlinux`, `rootfs.ext2`,
the base initramfs, and the complete AP/aidfs payload. Generated output is
ignored by Git and must never be committed.

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
The reusable artifact IDs and patch definitions live in the versioned,
language-neutral `binary_patch_catalog.json`; `binary_patch_catalog.py` is its
small Python loader. Every patch names an artifact ID, so all patches for one
payload share its extraction and rebuild handler and cannot accidentally be
mixed with patches for another payload. The accompanying JSON Schema is kept
in `binary_patch_catalog.schema.json` for the future browser/TypeScript tool.

Exact vendor-download identification is deliberately separate in
`release_manifest.json` (with `release_manifest.schema.json` and a Python
loader). It records only hashes, signed metadata expectations, archive layout,
feature state, and applicable artifact IDs—not vendor bytes. A release must be
recognized by this manifest before an eventual bundle builder can call it
supported.

The current read-only preflight for an already-decrypted archive is:

```sh
python3 verify_release_archive.py /path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img.tgz
```

It verifies the exact archive SHA-256, TAR path/link safety, required layout,
and expected vendor metadata without extracting or modifying the archive.

For the current 10.5.1 release, the deterministic local bundle builder is:

```sh
python3 build_zd1200_bundle.py \
  /path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img \
  /path/to/zd1200-10.5.1.0.282-bundle.zip
```

The ZIP contains the locally transformed controller image, Docker/runtime
source, `README-FIRST.md`, and `build-report.json`. It does not include the
original encrypted input. The report explicitly marks the R600 AP payload as
unpatched until a BL7 repacker is available.

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
extraction and rebuilding remain a separate packaging step. The unsigned BL7
container can now be parsed and safely round-tripped with `ruckus_bl7.py`; it
rejects signed ISI/FSI images rather than silently stripping their signatures.
SquashFS extraction/rebuild and integration of the module rule are still
pending:

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

The USB adapter, bridge, and TAP remain unnumbered on the Docker host. The
ZoneDirector guest owns `ZD_GUEST_IP`; a management station on the attached LAN
must perform the web-readiness check. Compose therefore checks that QEMU is
alive rather than trying to reach the guest through the host network stack.

A factory-state ZD requests DHCP and has no usable static management address.
For first setup, provide a temporary isolated DHCP reservation for the ZD MAC
and `ZD_GUEST_IP`, complete the wizard with **Manual** addressing, and then
remove the DHCP service. Do not place the factory guest on an untrusted or
production LAN. A low-step reservation-only bootstrap helper is tracked in
the roadmap; it is not yet part of the supported runtime.

After the first factory-wizard completion, restart the container once. The
vendor administrative SSH service can then generate its persistent host key.
The separate diagnostic root-SSH listener is disabled by default; it must
never be enabled with a repository-provided key.

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
No diagnostic SSH key is included; keep any operator-provided public key out
of Git and use a dedicated, isolated lab network.
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
release_manifest.py             verify_release_archive.py
host/zd1200-bridge            host/zd1200-bridge.service     host/zd1200-bridge.env.example
README.md                     LICENSE                         .gitignore                     .dockerignore
PROVENANCE.md                 ROADMAP.md                      VALIDATION.md
THIRD_PARTY_NOTICES.md        ruckus_tac_decrypt.py
build_zd1200_bundle.py
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
