# Physical-AP validation runbook

This runbook validates a generated ZD1200 lab bundle on an isolated Ethernet
segment. It is intentionally a controller-and-AP test, not merely a test that
the web UI loads. Do not attach the segment to a production network.

## Network profiles, recovery, and safety

The recommended topology is one dedicated Ethernet adapter on the Docker host,
a small isolated switch, and one management station. In this profile, the
adapter, bridge, and TAP device have no IPv4 or IPv6 address; the ZoneDirector
guest owns the management address.

Before starting the bridge service, verify that the proposed physical adapter
does not carry the host's default route:

```sh
ip route show default
ip -br addr
```

Configure the bridge helper with the physical adapter's MAC as well as its
name. The helper refuses an adapter that carries the host default route, and
also refuses the dedicated profile if its `br-zd` already carries that route.
Run its non-mutating preflight before enabling the services:

```sh
sudo /usr/local/sbin/zd1200-bridge check
```

For an intentionally shared management LAN, use
`ZD_NETWORK_PROFILE=existing-bridge` and point `ZD_BRIDGE_IF` at a Linux bridge
which was created and is owned by the host's network configuration. The helper
only creates and attaches `tap-zd`; it must not be given the physical member
interface and it does not alter the bridge's addresses, routes, members, or
STP configuration. This is an advanced profile: do not put an unconfigured
factory ZD on a production or untrusted LAN, and assign a non-conflicting
static guest address immediately in the setup wizard.

### Removal and recovery

For the dedicated profile, stop the controller and helper services. This
removes `tap-zd` and `br-zd`, and detaches the dedicated adapter without
changing the host's primary network interface:

```sh
docker compose down
sudo systemctl disable --now zd1200-bridge-watch.service zd1200-bridge.service
ip -br link show br-zd tap-zd  # both should be absent
```

To recover it, reconnect the configured adapter, run
`sudo /usr/local/sbin/zd1200-bridge check`, then re-enable both services. If
the adapter is to be reused as an ordinary host NIC, restore its addressing
through the host's network manager after it has been detached.

For the existing-bridge profile, stop Compose and only
`zd1200-bridge.service`. The helper deletes `tap-zd` but deliberately leaves
the existing bridge, its physical members, and its host IP/default route in
place. Do not enable the dedicated-adapter watcher for this profile.

## Initial controller setup

1. Start the bridge and controller. With no DHCP server on the isolated LAN,
   the factory image's setup wizard is available at `https://192.168.0.2/`.
2. Complete the setup wizard. Use the intended regulatory domain (for example,
   `DE Germany`), a unique controller serial/base MAC, and **Manual** IPv4
   settings for the intended lab address.
3. From the management station, log in at
   `https://<controller-ip>/admin10/login.jsp`.
4. Restart the controller. Confirm the login page and saved configuration
   remain available at the manual address. A DHCP reservation is only a
   fallback if a different exact release does not present the factory address.

Record the controller release, guest MAC, generated-image SHA-256, and the
state-volume backup location. Do not record passwords, session cookies, or
private keys in an issue or test log.

### Optional ECDSA SSH host key

With the default `ZD_ENABLE_ECDSA_SSH=0`, verify that the normal administrative
SSH service remains reachable with its RSA host key. To test the optional
compatibility setting, set `ZD_ENABLE_ECDSA_SSH=1`, recreate the container,
and restart the guest after the wizard is complete. Verify that SSH offers both
RSA and ECDSA host keys, and that a legacy RSA-only client still connects. Set
the option back to `0`, restart once more, and verify that the ECDSA key is no
longer advertised. This setting does not create a root-login path.

### Optional root CLI, root SSH, and support entitlement

With both `ZD_ENABLE_ROOT_CLI=0` and `ZD_SUPPORT_ENTITLEMENT_END` unset, verify
that `.root.sh` is unavailable from `debug` → `script` and that the controller's
existing support state is unchanged. For root-CLI testing, set
`ZD_ENABLE_ROOT_CLI=1`, recreate the container, and use `exec .root.sh` from an
authenticated administrative CLI session. Confirm `id` reports UID 0, then set
the option back to `0`, recreate, and confirm the hook is gone. No network port
should be added in either state.

For entitlement testing, set `ZD_SUPPORT_ENTITLEMENT_END=2100-01-01`, recreate,
and confirm the support-entitlement warning is absent in the web UI after the
controller reaches `RUN`. Verify the generated record uses the controller's
actual serial and finite UTC end date, then test a malformed and a
`2010-01-01` value: both must fail before QEMU starts.

The root-SSH option is supported only by the `10.5.1.0.282` runtime profile.
With `ZD_ROOT_SSH_PUBLIC_KEY` unset, verify that TCP 2222 is closed and that
the normal administrative SSH listener is unchanged. Supply one disposable RSA
or ECDSA public key in `.env`, recreate the container, and then test from the
management station:

```sh
ssh -p 2222 -i /path/to/disposable_private_key root@<controller-ip> id
```

The result must identify UID 0; a password-only attempt and a connection to
port 22 as `root` must fail. Remove the variable, recreate once more, and
verify TCP 2222 is closed. The option must refuse an older release, an
Ed25519 key, a malformed key, or an existing non-project-owned
`/root/.ssh/authorized_keys` instead of replacing it.

## One-time AP firmware prerequisite (ap-11n-scorpion models)

R600, R500, R310, T300, T300e, T301n, and T301s APs running FSI firmware
(commonly delivered by newer ZD, SmartZone, or Unleashed releases) reject an
unsigned UI image. Before adopting such an AP to a ZD bundle that delivers an
unsigned patched UI image, perform this one-time preparation manually:

1. Factory-reset the AP and connect it only to the isolated test segment.
2. Use the AP's standalone upgrade interface to install a compatible ISI image
   for that exact model, obtained from a legitimate Ruckus download.
3. Reboot and verify that the AP is actually running the ISI image.
4. Only then connect/adopt it to the patched ZD1200 and allow AP firmware
   delivery.

An AP already running UI or ISI firmware does not need this step. Never use an
image for a different model, and do not treat an FSI image as interchangeable
with ISI. For the exact 10.5.1 R600 payload, the generated bundle deliberately
validates the signed header/payload, removes its signature trailer, and creates
an unsigned UI image before applying the repair. The current automated
AP-payload patcher is validated for R600 only. The exact shared-payload aliases R500, R310, T300,
T300e, T301n, and T301s are patched as experimental targets and still require
model-specific validation.

## AP adoption and static-address persistence

1. Factory-reset one AP and attach it to the isolated switch. If it needs an
   address to discover the controller, give its MAC a temporary reservation on
   the same isolated DHCP service.
2. In the ZoneDirector UI, approve the AP. Confirm its model, serial/MAC, and
   reported software release.
3. Set the AP's management address to **Static** in the controller, using the
   selected IP/netmask/gateway. Apply the change and issue the controller's AP
   reset command.
4. Stop the temporary DHCP service. Confirm that the AP returns at its static
   address and reaches `Connected`/`RUN` state.
5. Restart the controller container once more. Confirm the AP automatically
   rejoins and the static setting is still shown by the controller.

Pass criteria: controller and AP survive both restarts without any DHCP server
on the segment. A successful ping by itself is not enough; verify controller
state after the AP has rebooted.

## AP firmware delivery

Test firmware delivery with an AP whose image differs from the controller's
advertised target. An already-current AP is not evidence that transfer works.

1. Capture the controller/AP Ethernet segment while approving or resetting the
   AP, for example:

   ```sh
   sudo tcpdump -ni <management-station-interface> -s 0 -w ap-upgrade.pcap
   ```

2. Record controller/AP versions before and after, plus the controller event
   log and AP join state.
3. For releases that use FTP delivery, confirm that the FTP service is
   listening in the guest and that the capture contains the expected transfer
   connection. For other releases, identify and capture the actual delivery
   mechanism instead of assuming FTP.
4. Confirm that the AP reboots, reports the target version, and rejoins.

Pass criteria: a version-changing transfer completes without repeating update
errors, and the AP rejoins after reboot. This is a per-exact-release test.

`fw show info` may report that `/writable/fw/main.cntl` is not in flash even
when the AP selected the newly delivered image and reached `RUN`. Treat that
line as diagnostic context, not a failure criterion; record the active image,
reported firmware version, and controller state instead.

## ap-11n-scorpion mesh receive-repair regression test

This applies only to a release/model combination recognized by the
`ap_11n_scorpion_mesh_vlan_rx` catalog rule. R600 is validated. R500, R310,
T300, T300e, T301n, and T301s use the patch only in experimental state, even
where their payload is an exact alias of the R600 payload. Patch both APs with
the generated image; do not treat a one-sided patch as proof of interoperability.

1. Attach the intended root AP by Ethernet. Configure it as `ROOT AP`.
2. Remove Ethernet from the mesh AP. Configure it as `MESH AP` and, if needed,
   force its uplink to the root AP's management MAC.
3. Use a fixed channel and disable automatic channel changes for the test.
4. On the mesh AP, check `get mesh`; the root must be shown as `U` (uplink) and
   the local state as `M` (MAP), rather than repeated `IDLE`, `DISCOVERY`, or
   `SULKING` transitions.
5. Issue ping tests in both directions between the AP management addresses.
   Confirm ARP entries resolve rather than remain `incomplete`.
6. Repeat after a mesh-AP reboot and after a root-AP reboot. If possible,
   collect a management-side capture and verify ordinary ARP/IPv4 Ethernet-II
   frames cross the mesh link without the erroneous inserted LLC/SNAP bytes.
7. Re-enable the normal WLAN configuration and test a client association plus
   ordinary unicast, broadcast/ARP, and DHCP relay behavior appropriate to the
   lab.

Pass criteria: bidirectional management traffic and client traffic survive
reboots, no ARP neighbour remains incomplete, and the mesh remains associated
for a sustained observation period.

## Regression matrix

| Test | 10.5.1.0.282 R600 isolated bench | 10.1.2.0.318 / 10.2.1.0.232 / 10.3.1.0.42 isolated bench | Other exact builds/models |
| --- | --- | --- | --- |
| Factory wizard at `192.168.0.2` without DHCP | passed | passed — each reached `READY`, returned the HTTPS wizard redirect, and installed its own AP payload |
| Manual controller address survives restart without DHCP | passed | required per build |
| AP adoption | passed | 10.1.2.0.318, 10.2.1.0.232, and 10.3.1.0.42 passed — R600 reached `RUN` |
| AP static address survives AP/controller restart without DHCP | passed | required per build/model |
| Version-changing AP firmware delivery | passed — R600 ISI `110.0.0.0.675` → patched UI `10.5.1.0.282`, using both secured HTTPS and legacy FTP delivery | 10.1.2.0.318 passed — R600 ISI `110.0.0.0.675` → signed FSI `10.1.2.0.318` using legacy FTP; 10.2.1.0.232 passed — R600 ISI `110.0.0.0.675` → signed FSI `10.2.1.0.232`, header MD5 `695FAFD1411902882A81364804796ACF`, then `RUN`; 10.3.1.0.42 passed — signed FSI delivery over FTP, 16,714,840-byte payload and `226 Transfer complete`, then `RUN` |
| R600 patched mesh bidirectional traffic | pending repeat from the generated bundle; passed in the prior two-R600 lab | not applicable or required by signature/model |
| Host reboot / bridge recovery | passed — bridge, TAP, ZD, and R600 recovered | required per host profile |
| USB-adapter unplug/replug recovery | passed — watcher reattached the USB NIC; ZD container stayed at restart count 0 | required per host profile |

The current physical delivery evidence is an R600 deliberately installed with
ISI `110.0.0.0.675`, then adopted by the factory-configured virtual ZD. The
controller delivered `R600_10.5.1.0.282-rx-vlan-fix-UNSIGNED.bl7` as Image2.
`fw show info` reported its expected 16,666,624-byte size and image-header MD5
`2ACDA0866E032DA153B7C709329DDE41`; after an AP reboot, `get director` again
reported `RUN` and `get version` reported `10.5.1.0.282`.

Do not mark a release/profile supported until every applicable row passes with
the transformed bundle built from that exact vendor download.
