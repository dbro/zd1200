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
original encrypted input. The report explicitly marks the shared
`ap-11n-scorpion` AP payload as unpatched until a BL7 repacker is selected.

The current catalog defines:

```text
zd1200_kernel_elf  ELF32 x86 kernel inside the vendor bzImage gzip member
ap_11n_scorpion_wlan_ko  raw ELF32 big-endian MIPS shared platform wlan.ko
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

The `ap-11n-scorpion` rule operates on an already-extracted module; BL7 filesystem
extraction and rebuilding remain a separate packaging step. The unsigned BL7
container can now be parsed and safely round-tripped with `ruckus_bl7.py`; it
rejects signed ISI/FSI images rather than silently stripping their signatures.
SquashFS extraction/rebuild and integration of the module rule are still
available as a standalone operation when the matching GPL tools are installed:

```sh
python3 patch_r600_bl7.py \
  /path/to/r600-input.bl7 /path/to/r600-patched.bl7 \
  --unsquashfs /path/to/ruckus_ap_firmware_mod/bin/unsquashfs \
  --mksquashfs /path/to/ruckus_ap_firmware_mod/bin/mksquashfs
```

The command patches exactly one `lib/modules/*/net/wlan.ko`, writes a new
unsigned image, and leaves the input untouched. It does not process signed
ISI/FSI images; signed ZD-delivered AP payloads therefore require an ISI/signing
bypass workflow before this operation. The bundle builder accepts the same two
tool paths to patch the shared `ap-11n-scorpion` payload in its nested AP
firmware. R600 is the validated model; R500, R310, T300, T300e, T301n, and
T301s are patched only when they resolve to the exact same vendor BL7 and are
explicitly reported as **experimental**.

```sh
python3 patch_binary_artifact.py \
  --artifact ap_11n_scorpion_wlan_ko \
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
sudo install -m 0644 host/zd1200-bridge-watch.service /etc/systemd/system/
sudo install -m 0600 host/zd1200-bridge.env.example /etc/default/zd1200-bridge
sudoedit /etc/default/zd1200-bridge
sudo systemctl daemon-reload
sudo systemctl enable --now zd1200-bridge.service
sudo systemctl enable --now zd1200-bridge-watch.service
```

Before adopting an ap-11n-scorpion model, read the [one-time AP firmware
prerequisite](VALIDATION.md#one-time-ap-firmware-prerequisite-ap-11n-scorpion-models).
An AP still running FSI firmware must first be manually upgraded to a
compatible ISI image for that exact model; otherwise it will reject the
unsigned patched UI image delivered by this lab ZD. APs already running UI or
ISI firmware do not need this preparation. The automated AP-payload patcher is
currently validated for R600 only.

The bridge service refuses to repurpose an interface carrying the host default
route. Set `ZD_USB_MAC` in its configuration to the dedicated adapter's MAC as
an additional guard.

The USB adapter, bridge, and TAP remain unnumbered on the Docker host. A small
systemd watcher reattaches only the configured USB adapter after a physical
unplug/replug without restarting the container; all other adapters are ignored.
A management station on the attached LAN must perform the web-readiness check.
Compose therefore checks that QEMU is alive rather than trying to reach the
guest through the host network stack.

On this factory ZD1200 build, use `https://192.168.0.2/` for the initial setup
wizard when no DHCP server is present. Set the desired permanent address in the
wizard (for example, `192.168.222.10/24` for the isolated lab). A temporary,
isolated DHCP reservation remains an optional fallback, not a standard
requirement. Do not place the factory guest on an untrusted or production LAN.
If desired, set `ZD_GUEST_IP` in `.env` after the wizard; it only makes the
startup status line show the known address and does not configure the guest.

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
