# Virtual ZoneDirector 1200 lab port

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
filesystems, keys, or AP images**. Obtain a matching ZD1200 package yourself
from Ruckus Support and ensure that your download, decryption and use comply
with the applicable terms. Exact builds are recognized only by their manifest
hash and metadata, never by a filename alone.

| Exact build | Status | Bench evidence |
| --- | --- | --- |
| 10.5.1.0.282 | known | controller, R600 adoption, HTTPS and legacy FTP delivery validated |
| 10.3.1.0.42 | experimental | controller, R600 signed FSI delivery over FTP, and AP `RUN` validated |
| 10.2.1.0.232 | experimental | controller, R600 ISI adoption, signed FSI delivery, and AP `RUN` validated |
| 10.1.2.0.318 | experimental | controller, R600 ISI adoption, legacy FTP delivery, FSI boot, and AP `RUN` validated |

The included [MIT License](LICENSE) applies only to this repository's original
glue code and documentation. It grants no rights to Ruckus materials.

Before a release, run `python3 check_repository_hygiene.py`. It rejects tracked
firmware/archive formats, private-key markers, and the formerly unprovenanced
diagnostic payload paths.

`prepare-vendor-image.sh` accepts either the original opaque encrypted download
or its already-decrypted gzip-TAR form and creates the ignored `image/`
directory locally. Select an older exact build with `RELEASE_ID`; otherwise it
uses the known 10.5.1.0.282 manifest. Legacy TAC decryption is performed
locally with an adapted, attributed
[aioruckus](https://github.com/ms264556/aioruckus) BSD-0-Clause
implementation; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The script verifies the encrypted input hash when applicable, then verifies the
decrypted archive identity, safe TAR layout/links, vendor metadata, and vendor
kernel/rootfs MD5 values before extracting `bzImage`, `vmlinux`, `rootfs.ext2`,
the base initramfs, and the complete AP/aidfs payload. Generated output is
ignored by Git and must never be committed.

## Prerequisites

- x86_64 Linux host with KVM (`/dev/kvm`) and Docker Compose.
- A Layer-2 path for the guest if it will manage real APs. The recommended
  dedicated-adapter profile keeps the host unnumbered on its adapter, bridge,
  and TAP interface. An existing-host-bridge profile is available for an
  intentionally shared LAN.
- Host tools for preparation: Bash, Python 3, `tar`, `gzip`, `cpio`,
  `openssl`, GNU binutils (`as`, `ld`), `md5sum`, and `sha256sum`. On an
  ARM64 Linux host, install `binutils-i686-linux-gnu`; the scripts select it
  automatically for the x86 guest helper. ARM64 remains experimental because
  no physical-AP validation host is available.

## Build the local image

```sh
cp .env.example .env
./prepare-vendor-image.sh /absolute/path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img.tgz
docker compose up -d --build
```

For an older experimental build:

```sh
RELEASE_ID=zd1200_10_2_1_0_232 ./prepare-vendor-image.sh \
  /absolute/path/to/zd1200_10.2.1.0.232.ap_10.2.1.0.232.img
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

For any recognized release, the deterministic local bundle builder is:

```sh
python3 build_zd1200_bundle.py \
  /path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img \
  /path/to/zd1200-10.5.1.0.282-bundle.zip
```

Pass the exact manifest ID for an older build, for example:

```sh
python3 build_zd1200_bundle.py \
  /path/to/zd1200_10.2.1.0.232.ap_10.2.1.0.232.img \
  /path/to/zd1200-10.2.1.0.232-bundle.zip \
  --release zd1200_10_2_1_0_232
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

The ZD1200 handler locates and decompresses the kernel ELF, applies all six
registered patches, recompresses it, pads the gzip member to its original
length, and splices it back without moving the vendor loader tail. Defaults are
compatible with the lab launcher:

```sh
python3 patch_binary_artifact.py \
  --artifact zd1200_kernel_elf \
  --in image/bzImage \
  --out image/bzImage.patched
```

Those rules suppress only the physical NAR5520 watchdog startup/worker paths
and replace the appliance-specific guest reset entry point with QEMU's i8042
system-reset command. They do not disable the kernel's general halt path or
use a host-side log watcher to manufacture a reboot.

The vendor loader reserves a fixed-size gzip member. To keep that boundary
unchanged on especially tight releases, the catalog clears a name in the ELF
section-name table, which is not mapped by any loadable program segment and is
not used by the running kernel.

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

For a physical Ethernet attachment, install the bridge helper and choose one
of the profiles below. The controller is a factory appliance on first boot;
use an isolated lab unless you have deliberately prepared a shared management
LAN.

```sh
sudo install -m 0755 host/zd1200-bridge /usr/local/sbin/zd1200-bridge
sudo install -m 0644 host/zd1200-bridge.service /etc/systemd/system/
sudo install -m 0644 host/zd1200-bridge-watch.service /etc/systemd/system/
sudo install -m 0600 host/zd1200-bridge.env.example /etc/default/zd1200-bridge
sudoedit /etc/default/zd1200-bridge
sudo systemctl daemon-reload
```

### Dedicated adapter (recommended)

In `/etc/default/zd1200-bridge`, keep `ZD_NETWORK_PROFILE=dedicated`, set
`ZD_USB_IF` to the dedicated Ethernet adapter and set `ZD_USB_MAC` to its MAC.
The helper creates the unnumbered `br-zd` and `tap-zd` path, and refuses to
start if either the selected adapter or `br-zd` carries the host's default
route. It also recreates that path after a USB unplug/replug.

```sh
sudo /usr/local/sbin/zd1200-bridge check
sudo systemctl enable --now zd1200-bridge.service
sudo systemctl enable --now zd1200-bridge-watch.service
```

### Existing Linux bridge (advanced shared-LAN profile)

Use this only when the Docker host already has a Linux bridge, such as `br0`,
created and managed by its network configuration. That bridge may retain the
host address and default route. Set the following in
`/etc/default/zd1200-bridge` and leave `ZD_USB_IF` and `ZD_USB_MAC` unset:

```ini
ZD_NETWORK_PROFILE=existing-bridge
ZD_BRIDGE_IF=br0
ZD_TAP_IF=tap-zd
```

The helper only creates/removes and attaches `tap-zd`; it never changes the
existing bridge, its member NICs, addresses, routes, STP settings, or default
route. Do not enable `zd1200-bridge-watch.service` for this profile: there is
no dedicated USB adapter to watch.

```sh
sudo /usr/local/sbin/zd1200-bridge check
sudo systemctl enable --now zd1200-bridge.service
```

Before adopting an ap-11n-scorpion model, read the [one-time AP firmware
prerequisite](VALIDATION.md#one-time-ap-firmware-prerequisite-ap-11n-scorpion-models).
An AP still running FSI firmware must first be manually upgraded to a
compatible ISI image for that exact model; otherwise it will reject the
unsigned patched UI image delivered by this lab ZD. APs already running UI or
ISI firmware do not need this preparation. The automated AP-payload patcher is
currently validated for R600 only.

The dedicated profile refuses to repurpose an interface carrying the host
default route. Set `ZD_USB_MAC` as an additional guard. Its USB adapter,
bridge, and TAP remain unnumbered, and the watcher reattaches only that
configured adapter after an unplug/replug. In the existing-bridge profile,
the operator-owned bridge retains its host networking and only the TAP is
managed by this project.

To remove the dedicated path, stop Compose and both services; the helper
detaches the adapter and removes `br-zd` and `tap-zd`. To remove the
existing-bridge path, stop Compose and `zd1200-bridge.service`; only `tap-zd`
is removed and the pre-existing bridge is left untouched. See
[`VALIDATION.md`](VALIDATION.md#network-profiles-recovery-and-safety) for the
full recovery checks.

On this factory ZD1200 build, use `https://192.168.0.2/` for the initial setup
wizard when no DHCP server is present. Set the desired permanent address in the
wizard (for example, `192.168.222.10/24` for the isolated lab). A temporary,
isolated DHCP reservation remains an optional fallback, not a standard
requirement. Do not place the factory guest on an untrusted or production LAN.
If desired, set `ZD_GUEST_IP` in `.env` after the wizard; it only makes the
startup status line show the known address and does not configure the guest.

After the first factory-wizard completion, restart the container once. The
vendor administrative SSH service can then generate its persistent host key.
By default it offers its vendor RSA host key only. Set
`ZD_ENABLE_ECDSA_SSH=1` in `.env` before starting the container to retain RSA
and offer an additional ECDSA host key on that same administrative SSH
listener. The option is reversible: setting it back to `0` restores the vendor
launcher on the next boot. It changes host-key compatibility only; it does not
enable root access or weaken account authentication.

`ZD_ENABLE_ROOT_CLI=1` is a separate, deliberately opt-in lab/recovery option.
It uses the vendor CLI script hook and adds no listener or SSH account. An
authenticated CLI administrator can then enter `enable`, `debug`, `script`, and
`exec .root.sh` to obtain a local root shell. Disable the setting and restart
to remove only this project's hook.

Set `ZD_SUPPORT_ENTITLEMENT_END=YYYY-MM-DD` to create an enabled, finite support
entitlement record before the controller starts; the date must be later than
`2010-01-01`. Leave it unset to preserve the vendor support-entitlement state.
For a long-lived isolated lab, `2100-01-01` is a practical value.

### Health status

Docker reports the container as healthy only after the emulated guest has a
running `webs` process and an HTTP or HTTPS listening socket. This is stronger
than checking that QEMU exists: a guest which has shut down or is stuck during
a reboot becomes unhealthy. The physical-LAN TAP bridge remains unnumbered;
the check uses a guest-generated serial readiness marker rather than assigning
an otherwise unnecessary management address to the Docker host.
The separate diagnostic root-SSH development hook is not included in source
bundles. It must remain disabled unless a future reproducible, licensed
implementation and an operator-provided public key are available.

## Runtime notes

- Keep `KERNEL_EXTRA: nohz=off`. The 2.6.32 guest's tickless-idle path spins a
  host CPU while idle; this option reduced observed KVM QEMU CPU use from about
  25% to about 2% of one host CPU.
- `CPU_LIMIT` is intentionally absent for KVM. The old duty-cycle limiter only
  added SIGSTOP/SIGCONT pauses and delayed useful work. `nice -n 10` remains
  and only lowers scheduling priority under contention.
- Each new state volume receives a generated 12-digit serial number and a
  locally administered unicast MAC; both are retained in
  `board-identity.env` within that volume. The synthetic board data and QEMU
  NIC use the same base MAC. To use a chosen identity, set both `ZD_SERIAL`
  and `ZD_MAC1` before first start; MAC2 is derived as MAC1 + 1.
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
run-zd1200-web.sh             zd-controller-wrapper.sh       zd-healthcheck.sh
zd-memory-snapshot.sh
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
