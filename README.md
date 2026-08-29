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

Legacy TAC decryption is performed locally with an adapted, attributed
[aioruckus](https://github.com/ms264556/aioruckus) BSD-0-Clause
implementation; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The Compose preparation service verifies the encrypted input hash when
applicable, then verifies the decrypted archive identity, safe TAR layout and
links, vendor metadata, and vendor kernel/rootfs MD5 values before extracting
and transforming it. Generated output is ignored by Git and must never be
committed.

## Prerequisites

- x86_64 Linux host with KVM (`/dev/kvm`) and Docker Compose. This is the
  validated host target; ARM64 is experimental because physical-AP validation
  has not yet been performed on that architecture.
- A Layer-2 path for the guest if it will manage real APs. The recommended
  dedicated-adapter profile keeps the host unnumbered on its adapter, bridge,
  and TAP interface. An existing-host-bridge profile is available for an
  intentionally shared LAN.
- Docker handles local decryption, validation, extraction, patching, and the
  required preparation tools. The host needs only Docker Compose plus the
  separate Linux bridge helper prerequisites when physical AP networking is
  wanted.

## One-command local installation

```sh
mkdir -p vendor
# Copy the Ruckus ZD download into vendor/.
cp .env.example .env
docker compose up -d --build
```

Set `ZD_VENDOR_ARCHIVE` in `.env` to the basename of the downloaded file in
`vendor/` (or set `ZD_VENDOR_DIR` to an existing directory). For a 10.5.1
R600 mesh-repair deployment, no additional AP image is required: the
preparation service builds the patched unsigned R600 payload directly from the
selected ZD download. The 10.1–10.3 scoped releases are left unmodified
because this receive-path bug is not present there.

The one-shot `zd1200-prepare` service reads `vendor/` read-only. It detects the
exact release from the full input SHA-256, decrypts and validates locally,
creates the controller/AP runtime files in the `zd1200-runtime` named volume,
then exits. Compose starts `zd1200` only after this succeeds. Neither the
download nor derived vendor bytes are stored in Docker image layers.
During the first Docker build, Docker fetches and compiles a pinned public GPL
source revision of the historical LZMA SquashFS tools needed only for that
10.5.1 R600 repack; see `THIRD_PARTY_NOTICES.md`.

Subsequent `docker compose up -d` calls verify the same input fingerprint and
return immediately. To deliberately replace the controller download, first
stop the stack and remove only its runtime volume, then start again:

```sh
docker compose down
docker volume rm "$(grep '^ZD_RUNTIME_VOLUME=' .env | cut -d= -f2)"
docker compose up -d --build
```

If `ZD_RUNTIME_VOLUME` is not set, remove `zd1200-runtime` instead. This does
not remove the separately named `zd1200-state` volume; remove that too only
when a factory-reset controller state is intended.

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
recognized by this manifest before the bundle builder can process it.

The current read-only preflight for an already-decrypted archive is:

```sh
python3 verify_release_archive.py /path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img.tgz
```

It verifies the exact archive SHA-256, TAR path/link safety, required layout,
and expected vendor metadata without extracting or modifying the archive.

## Advanced: standalone bundle builder

For any recognized release, the local bundle builder is:

```sh
python3 build_zd1200_bundle.py \
  /path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img \
  /path/to/zd1200-10.5.1.0.282-bundle.zip
```

The input hash selects its exact manifest automatically. You may pass the
exact manifest ID only to assert the release you expect, for example:

```sh
python3 build_zd1200_bundle.py \
  /path/to/zd1200_10.2.1.0.232.ap_10.2.1.0.232.img \
  /path/to/zd1200-10.2.1.0.232-bundle.zip \
  --release zd1200_10_2_1_0_232
```

The ZIP contains the locally transformed controller image, Docker/runtime
source, `README-FIRST.md`, and `build-report.json`. It does not include the
original encrypted input. A standalone build without AP-repack options reports
the shared `ap-11n-scorpion` payload as unpatched. Compose automatically
repackages and repairs that payload only for the exact 10.5.1 R600 release;
older scoped releases report that the repair is not required. The transformed
firmware artifacts are reproducible for a fixed input and options. The final
ZIP deliberately receives a fresh self-signed bootstrap TLS identity, so its
overall ZIP hash is not expected to repeat across separate builds.

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
extraction and rebuilding remain a separate packaging step. For the exact
10.5.1 R600 payload, the automatic builder validates the signed header and
payload, removes its signature trailer, converts it to unsigned UI, then
applies the module repair. This is an explicit local conversion, not signature
validation or preservation. Standalone SquashFS extraction/rebuild and module
integration are also available when the matching GPL tools are installed:

```sh
python3 patch_r600_bl7.py \
  /path/to/r600-input.bl7 /path/to/r600-patched.bl7 \
  --unsquashfs /path/to/ruckus_ap_firmware_mod/bin/unsquashfs \
  --mksquashfs /path/to/ruckus_ap_firmware_mod/bin/mksquashfs
```

The command patches exactly one `lib/modules/*/net/wlan.ko`, writes a new
unsigned image, and leaves the input untouched. It accepts a signed input only
for the explicit UI conversion above; it never writes a modified signed image.
The bundle builder enables that path only for the exact 10.5.1 release needing
the repair. R600 is the validated model; R500, R310, T300, T300e, T301n, and
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

The prepared vendor-derived runtime lives in the named
`ZD_RUNTIME_VOLUME` (`zd1200-runtime` by default). The separate named
`ZD_STATE_VOLUME` holds controller configuration and persists across normal
container rebuilds. `.env`, `vendor/`, generated images, VM disks, logs and
state are excluded by `.gitignore`.

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

Before using the 10.5.1 unsigned mesh-repair path with an ap-11n-scorpion
model, read the [one-time AP firmware prerequisite](VALIDATION.md#one-time-ap-firmware-prerequisite-ap-11n-scorpion-models).
An AP still running FSI firmware must first be manually upgraded to a
compatible ISI image for that exact model; otherwise it will reject the
unsigned patched UI image. APs already running UI or ISI firmware do not need
this preparation. The 10.1–10.3 scoped releases deliver signed FSI firmware
and do not use this unsigned-image prerequisite. The automated AP-payload
patcher is currently validated for R600 only.

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

`ZD_WEB_PROBE` controls only launcher readiness behavior; it never assigns a
guest address. `off` declares startup without an in-container HTTP probe,
which is appropriate for the normal TAP/physical-LAN configuration. `on`
probes the configured `ZD_GUEST_IP`. `auto` selects `off` for TAP networking
and `on` for user-mode networking. The shipped TAP profile uses `off`.

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

For an opt-in root SSH shell on TCP 2222, set `ZD_ROOT_SSH_PUBLIC_KEY` to one
RSA or ECDSA OpenSSH public-key line before starting the container. This uses
the vendor Dropbear binary with password authentication disabled and leaves the
ordinary administrative SSH service untouched. It is currently enabled only
for the 10.5.1.0.282 manifest; Ed25519 keys are rejected because that vendor
Dropbear build does not support them.

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
No third-party diagnostic SSH payload is included in source bundles. The only
supported opt-in root SSH path is the vendor-Dropbear key option described
above; it requires an operator-provided public key.

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

The public repository contains source and documentation only. Its principal
components are:

- Compose/runtime launchers: `Dockerfile`, `docker-compose.yml`,
  `prepare-compose-runtime.sh`, `run-zd1200-web.sh`, and the initrd/QEMU
  helpers.
- Local transformation tools: `build_zd1200_bundle.py`,
  `patch_binary_artifact.py`, `patch_r600_bl7.py`, `ruckus_bl7.py`, and the
  release/archive verification tools.
- Versioned metadata: `binary_patch_catalog.json`, `release_manifest.json`,
  and their schemas/loaders.
- Host networking: `host/zd1200-bridge` and its dedicated/existing-bridge
  systemd units and configuration template.
- Runtime identity and optional payload helpers: `zd_identity.py`,
  `write-boarddata.py`, and `zd_root_ssh.py`.
- Verification and policy: `tests/`, `check_repository_hygiene.py`,
  `PROVENANCE.md`, `VALIDATION.md`, `ROADMAP.md`, and
  `THIRD_PARTY_NOTICES.md`.

`limit-process-cpu.py` is retained only for the automatic TCG fallback, not
normal KVM operation.

## Optional future improvements

1. Replace the fixed seeded Unix password hashes with per-deployment secrets,
   but only after confirming that factory setup, recovery, and Dropbear flows
   remain recoverable.
2. Validate physical-AP operation on ARM64 Docker hosts.
3. Validate the shared ap-11n-scorpion repair on each experimental model:
   R500, R310, T300, T300e, T301n, and T301s.

## Known limitation

Do not use the ZoneDirector web-upgrade workflow inside this VM. QEMU boots an
external kernel and initramfs, so an in-guest upgrade would create a mixed
version unless this port is updated and rebuilt for that release.
