# Virtual ZoneDirector 1200

This project runs Ruckus ZoneDirector 1200 software in a local QEMU virtual
machine so it can manage real Ruckus access points without a
physical ZD1200 appliance. It is intended for owners of compatible APs who
have legitimately obtained a ZoneDirector software download and want a
repeatable lab, replacement-controller, compatibility, or recovery setup.

This is an experimental community project, not a Ruckus product. It does not
include Ruckus software, firmware, keys, or licenses; you must provide those
yourself and remain responsible for complying with the Ruckus terms.

## Recommended hardware

- A Linux x86-64 Docker host.
- A dedicated USB Ethernet adapter.
- **10.5.1.0.282**, the recommended controller release. Its vendor target list
  includes C110, H320, H510, R310, R320, R500, R510, R550, R600, R610, R650,
  R710, R720, R730, R750, R850, R350, H550, H350, E510, T300, T300e, T301n,
  T301s, T310c, T310d, T310n, T310s, T610, T610s, T710, T710s, T750, T750SE,
  T350c, T350d, and T350se. Check the Ruckus release documentation for your
  AP's exact support status.
- An older ZD release if you need legacy models such as the R700 or H500; see
  [Choose a ZoneDirector release](#choose-a-zonedirector-release).

### 1. Check the host requirements

- Linux x86-64 with Docker Engine, Docker Compose, and KVM available at
  `/dev/kvm`.
- One Ethernet adapter that can be dedicated to the controller/AP network. Do
  not use the adapter that carries the host's normal Internet/default route.
- A management computer on the same isolated switch, plus the APs to manage.

Linux ARM64 is experimental. macOS and Windows Docker Desktop are not
supported for physical AP management because this project has no validated
equivalent to the required Linux TAP/bridge networking.

### 2. Download the controller software and create `.env`

ZoneDirector software is available for download on the Ruckus website, and
requires account registration.

Clone this repository, create a `vendor/` directory, and copy your original
ZD1200 download into it. Do not commit that directory.

```sh
git clone https://github.com/dbro/zd1200.git
cd zd1200
git switch public-release-candidate
mkdir vendor
# Copy your Ruckus ZD1200 software download into ./vendor/
cp .env.example .env
```

Now edit `.env`. The only required change is `ZD_VENDOR_ARCHIVE`: set it to
the exact filename you placed in `vendor/`. Other options include enabling
a root shell and other conveniences.

```ini
# Example for the recommended release. Use the actual filename you downloaded.
ZD_VENDOR_ARCHIVE=zd1200_10.5.1.0.282.ap_10.5.1.0.282.img

# Keep this as tap-zd unless you deliberately changed the bridge-helper config.
ZD_TAP_IF=tap-zd

# Optional: leave both commented out to generate a persistent local identity.
# If you set one, set both before the first start.
# ZD_SERIAL=123456000789
# ZD_MAC1=02:52:54:12:00:01
```

### 3. Attach the dedicated adapter

Install and configure the bridge helper. Substitute the Ethernet interface
name and MAC address of the adapter that is connected to the isolated switch.

```sh
sudo install -m 0755 host/zd1200-bridge /usr/local/sbin/zd1200-bridge
sudo install -m 0644 host/zd1200-bridge.service /etc/systemd/system/
sudo install -m 0644 host/zd1200-bridge-watch.service /etc/systemd/system/
sudo install -m 0600 host/zd1200-bridge.env.example /etc/default/zd1200-bridge
sudoedit /etc/default/zd1200-bridge
```

Set these values in `/etc/default/zd1200-bridge`:

```ini
ZD_NETWORK_PROFILE=dedicated
ZD_USB_IF=enx0123456789ab
ZD_USB_MAC=01:23:45:67:89:ab
ZD_TAP_IF=tap-zd
```

The helper refuses an adapter or bridge with the host's default route. Check
the proposed setup, then enable it:

```sh
sudo systemctl daemon-reload
sudo /usr/local/sbin/zd1200-bridge check
sudo systemctl enable --now zd1200-bridge.service
sudo systemctl enable --now zd1200-bridge-watch.service
```

### 4. Build and start the controller

```sh
docker compose up -d --build
docker compose logs -f
```

On its first build, Docker locally decrypts and validates your download,
prepares the controller runtime, and starts QEMU. It also builds the patched
unsigned R600 AP image only for 10.5.1. Your original download stays read-only
in `vendor/` and is never copied into a Docker image layer.

Without DHCP on the isolated network, browse to
[`https://192.168.0.2/`](https://192.168.0.2/) from the management computer.
First give that computer a temporary static address such as `192.168.0.3/24`
on its Ethernet adapter; no gateway is needed for this step.
Complete the ZoneDirector wizard and assign the controller a permanent static
address suitable for the isolated network. Then restart the controller once:

```sh
docker compose restart zd1200
```

Log in at `https://<your-controller-address>/admin10/login.jsp` and adopt an
AP. The full physical-AP acceptance procedure is in
[VALIDATION.md](VALIDATION.md).

## Choose a ZoneDirector release

Only the exact builds below are recognized. Set `ZD_VENDOR_ARCHIVE` to the
filename of the matching download; the preparation step identifies the build
by SHA-256, not by filename alone.

| Choose this build | Choose it when | Important note |
| --- | --- | --- |
| **10.5.1.0.282** | You want the recommended, newest supported controller path. | Automatically creates the repaired R600 mesh image. R600 is validated; related shared-payload models remain experimental. |
| **10.3.1.0.42** | You need the final ZD release family that supports the R700. | |
| **10.2.1.0.232** | You need its historical/vendor unsigned-image compatibility behavior. | This is distinct from the project-generated unsigned R600 image used only by the 10.5.1 mesh repair. |
| **10.1.2.0.318** | You need H500 support. | |

The three older builds are compatibility paths, not upgrades over 10.5.1.
Their AP firmware is delivered as signed FSI images; they do not need or
receive the 10.5.1 mesh repair.


## Fixing mesh operation for R600 and experimental shared-payload APs

ZoneDirector version 10.5.1.0.276 introduced a mesh receive-path bug for some
AP models. A wired "Root" AP (RAP) can appear normal while a wireless "Mesh"
AP (MAP) shows as connected but does not pass ordinary Layer-2 traffic between
the wired and mesh sides. Typical signs are unresolved ARP entries and failed
bidirectional management pings; a static management address does not prevent
the fault.

This bug exists in the software version recommended here (10.5.1.0.282) that
is downloadable from the Ruckus website. This project applies a fix for this
bug during the installation process.

**R600 is the validated repaired model.** R500, R310, T300, T300e, T301n, and
T301s are experimental targets only when the selected ZD payload resolves to
the exact same shared firmware image. Validate a specific model before relying
on the repair in a production-like deployment.

**IMPORTANT! You must manually prepare these APs to receive UNSIGNED firmware
when running ZD version 10.5.1.0.282 !**

An AP currently running a fully signed (**FSI**) image will reject the patched
unsigned (**UI**) image used by the 10.5.1 path. Before adopting one of these
APs to the patched 10.5.1 controller, install a compatible intermediate-signed
(**ISI**) image for that exact AP model through its standalone upgrade
interface. For example, an R600 can use the standalone/ISI release
`110.0.0.0.675`; confirm the exact filename and model compatibility from the
legitimate Ruckus download.
An AP already running an ISI or UI image does not need this preparation.
Never install firmware for a different model.

Steps to flash ISI firmware on an AP:
* download the ISI image from Ruckus website and save it on your laptop
* isolate the AP from the network, and connect it to your laptop with a USB
ethernet adapter set up with a static IP address 192.168.0.xxx
* factory reset the AP and let it reboot
* visit the AP's admin webpage at 192.168.0.1 and login with the default
username "super" and password "sp-admin"
* update the firmware using the local method and select the ISI image
saved on your laptop
* after the AP restarts, visit 192.168.0.1 and check that it runs the
ISI version. Assign its IP as needed (DHCP or static) to connect with
the ZoneDirector.
* Let the ZD discover, adopt, accept, and upgrade the firmware to the
unsigned+patched version.

## Everyday operation

```sh
# Start or update after changing Compose/project code.
docker compose up -d --build

# Inspect startup and guest messages.
docker compose logs -f

# Stop the controller without deleting its configuration.
docker compose down

# Check whether Docker considers the guest ready.
docker compose ps
```

The named state volume retains the controller configuration, AP database,
generated serial number, and generated MAC address. Back it up before making
large configuration changes. Container stop can take up to two minutes while
the guest performs its normal repository and filesystem flush; do not force
QEMU or Docker to exit during that interval.

To factory-reset the virtual controller, stop the stack and remove only its
state volume:

```sh
docker compose down
state_volume="$(sed -n 's/^ZD_STATE_VOLUME=//p' .env | head -n 1)"
state_volume="${state_volume:-zd1200-state}"
docker volume rm "$state_volume"
docker compose up -d --build
```

To deliberately use a different controller download, stop the stack and
remove only the runtime volume named by `ZD_RUNTIME_VOLUME`; leave the state
volume intact unless you also want a factory reset.

```sh
docker compose down
runtime_volume="$(sed -n 's/^ZD_RUNTIME_VOLUME=//p' .env | head -n 1)"
runtime_volume="${runtime_volume:-zd1200-runtime}"
docker volume rm "$runtime_volume"
docker compose up -d --build
```

### Optional `.env` settings

| Setting | Purpose |
| --- | --- |
| `ZD_SERIAL` and `ZD_MAC1` | Set a chosen, stable controller identity. Set both before the first start, or leave both unset for a generated persistent identity. |
| `ZD_WEB_PROBE` | Controls launcher readiness only. Leave it at `auto`: it disables in-container HTTP probing for the normal TAP or macvlan network and enables a local probe for user-mode networking. `on` is not supported with TAP or macvlan. |
| `ZD_ENABLE_ECDSA_SSH=1` | Adds an ECDSA host key to the ordinary ZoneDirector administrative SSH service while retaining RSA. |
| `ZD_ENABLE_ROOT_CLI=1` | Enables a local root shell through the authenticated ZD CLI script hook. It does not add a network listener. |
| `ZD_ROOT_SSH_PUBLIC_KEY` | Enables public-key-only root SSH on TCP 2222 for 10.5.1.0.282. RSA and ECDSA keys are accepted; Ed25519 is not. |
| `ZD_SUPPORT_ENTITLEMENT_END` | Creates a finite support-entitlement record ending on the supplied `YYYY-MM-DD` date. |
| `ZD_PING_INTERVAL_SECONDS` and `ZD_SNAPSHOT_INTERVAL_SECONDS` | Set the initial 30–3600 second intervals. Both collectors remain disabled until enabled from the Ping Monitor page. |
| `ZD_VIRTUAL_BUILD_ID` | Optional override for the seven-character revision shown after the ZoneDirector version. Git checkouts detect this automatically; Portainer deployments may leave it unset (they show `virtual 0000000`). |

The regular admin console appends `virtual <revision>` to the stock
ZoneDirector version. The revision is resolved from the checked-out Git commit
when the Compose runtime is prepared, so it identifies the source used to
build that running virtual controller. If the project was copied without its
`.git` metadata, set `ZD_VIRTUAL_BUILD_ID` to the source commit and
`ZD_GIT_DIR=/dev/null` before running Compose.

### Ping Monitor

After the setup wizard and first restart, **Ping Monitor** appears as the last
item under **Troubleshooting**. It records individual ICMP observations and
retains raw ZoneDirector AP, client, and mesh snapshots for comparison.

Both ping polling and configuration snapshots are disabled on a fresh
controller. Enable either collector and choose its interval on the Ping
Monitor page. The settings are stored through ZoneDirector's normal
authenticated preference mechanism. Snapshot collection uses the controller's
root-local vendor statistics socket, so no additional ZoneDirector role, user,
or password is required.

## Networking choices and recovery

The dedicated-adapter profile above is recommended because it keeps the
Docker host unnumbered on the controller/AP network. The bridge helper can
also attach `tap-zd` to an existing Linux bridge for an intentionally shared
management LAN:

```ini
ZD_NETWORK_PROFILE=existing-bridge
ZD_BRIDGE_IF=br0
ZD_TAP_IF=tap-zd
```

This advanced profile never changes the existing bridge's members, addresses,
routes, or default route. Do not enable `zd1200-bridge-watch.service` for it.
Do not put an unconfigured factory controller on a production LAN.

### Docker macvlan (advanced, experimental)

Use macvlan when the Docker host has an existing wired management VLAN and you
cannot dedicate a physical adapter. The QEMU guest receives Layer-2 access by
bridging the container's macvlan interface to its private TAP; the guest, not
the Docker container, owns the management address.

Add these settings to `.env`, using the host interface and subnet of the VLAN
where the controller is intentionally allowed to appear:

```ini
ZD_MACVLAN_PARENT=eno1
ZD_MACVLAN_SUBNET=192.168.222.0/24
ZD_MACVLAN_GATEWAY=192.168.222.1
```

Then start the alternate Compose profile:

```sh
docker compose -f docker-compose.macvlan.yml up -d --build
```

Do not run the dedicated-adapter bridge helper for this profile. Docker hosts
normally cannot communicate directly with their own macvlan containers, so use
another management station on the selected VLAN for the setup wizard and ZD
administration. This profile has not yet completed physical AP validation; use
it only on an intentionally isolated or managed VLAN.

To remove the dedicated network path, stop Compose and both helper services.
For the existing-bridge profile, stop Compose and only
`zd1200-bridge.service`; the pre-existing bridge is left untouched. Detailed
recovery and AP-adoption instructions are in [VALIDATION.md](VALIDATION.md).

## Troubleshooting

- **`Missing image/bootinitramfs.gz`** — the preparation service did not
  complete. Run `docker compose logs zd1200-prepare` and correct the archive
  filename or unsupported-build error before starting again.
- **The setup wizard does not appear at `192.168.0.2`** — make sure the
  management computer is connected to the isolated switch and has an address
  in the same temporary subnet. Check `docker compose logs -f` and the bridge
  helper with `sudo /usr/local/sbin/zd1200-bridge check`.
- **An AP loops during a 10.5.1 update** — confirm that it was first moved from
  FSI to a compatible ISI image. See “Fixing mesh operation for R600 and
  experimental shared-payload APs” above.
- **The dedicated adapter does not recover after reconnecting** — verify its
  MAC still matches `ZD_USB_MAC`, then review
  `systemctl status zd1200-bridge-watch.service`.

## Advanced and developer reference

The Docker workflow is the normal installation method. The repository also
contains standalone local tools for inspecting or building a ZIP from a
recognized vendor download:

```sh
python3 verify_release_archive.py /path/to/decrypted-zd1200.img.tgz
python3 build_zd1200_bundle.py /path/to/zd1200.img /path/to/bundle.zip
```

`build_zd1200_bundle.py` accepts either the original encrypted download or a
recognized decrypted archive. The generated ZIP includes transformed runtime
files but not the original encrypted input. Its overall hash changes between
builds because it receives a fresh bootstrap TLS identity.

The artifact-specific patch definitions are in `binary_patch_catalog.json` and
the exact supported-download manifests are in `release_manifest.json`. The
full validation matrix, known limitations, test evidence, and roadmap are in
[VALIDATION.md](VALIDATION.md), [PROVENANCE.md](PROVENANCE.md), and
[ROADMAP.md](ROADMAP.md). Run `python3 check_repository_hygiene.py` before
contributing or publishing changes.

The NAR5520 LED and watchdog probes can call the global kernel halt path while
probing hardware that QEMU does not provide. The narrowly documented
`kernel_halt` return keeps those boot-time probes from stopping the VM; it does
not implement restart. Normal ZD admin restart still runs the stock flush and
PID-1 shutdown sequence, then the separate `machine_restart_qemu` patch asks
QEMU's emulated i8042 controller to reset the guest. Exercise both pieces with:

```sh
python3 tests/qemu_restart_smoke.py
```

## Security and licensing

This is not a hardened appliance. Keep the controller, its web UI, SSH, FTP,
and the Docker host API off untrusted networks. Use a dedicated management VLAN
and firewall rules. Do not commit `.env`, `vendor/`, generated runtime files,
state volumes, captures, passwords, or private keys.

The repository's [MIT License](LICENSE) applies only to this project's glue
code and documentation. It grants no rights to Ruckus material. The local TAC
decryption implementation is derived from the permissively licensed
[aioruckus](https://github.com/ms264556/aioruckus) project; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Known limitation

Do not use the ZoneDirector web-upgrade workflow inside this VM. QEMU boots an
external kernel and initramfs, so an in-guest upgrade creates a mixed version
unless this project is updated and rebuilt for that exact release.
