# Physical-AP validation runbook

This runbook validates a generated ZD1200 lab bundle on an isolated Ethernet
segment. It is intentionally a controller-and-AP test, not merely a test that
the web UI loads. Do not attach the segment to a production network.

## Topology and safety checks

Use one dedicated Ethernet adapter on the Docker host, a small isolated switch,
and one management station. The adapter, bridge, and TAP device must have no
IPv4 or IPv6 address; the ZoneDirector guest owns the management address.

Before starting the bridge service, verify that the proposed physical adapter
does not carry the host's default route:

```sh
ip route show default
ip -br addr
```

Configure the bridge helper with the physical adapter's MAC as well as its
name. The helper refuses an adapter that carries the host default route. This
does not make a production network safe; it prevents a common destructive lab
mistake.

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
with ISI. The generated bundle refuses signed ISI/FSI BL7 payloads rather than
discarding their signatures. The current automated AP-payload patcher is
validated for R600 only. The exact shared-payload aliases R500, R310, T300,
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

| Test | 10.5.1.0.282 R600 isolated bench | Other exact builds/models |
| --- | --- | --- |
| Factory wizard at `192.168.0.2` without DHCP | passed | required per build |
| Manual controller address survives restart without DHCP | passed | required per build |
| AP adoption | passed | required per model |
| AP static address survives AP/controller restart without DHCP | passed | required per build/model |
| Version-changing AP firmware delivery | passed — R600 ISI `110.0.0.0.675` → patched UI `10.5.1.0.282` | required per build/model |
| R600 patched mesh bidirectional traffic | pending repeat from the generated bundle; passed in the prior two-R600 lab | not applicable or required by signature/model |
| Host reboot / bridge recovery | pending operator-performed test | required per host profile |
| USB-adapter unplug/replug recovery | pending operator-performed test | required per host profile |

The current physical delivery evidence is an R600 deliberately installed with
ISI `110.0.0.0.675`, then adopted by the factory-configured virtual ZD. The
controller delivered `R600_10.5.1.0.282-rx-vlan-fix-UNSIGNED.bl7` as Image2.
`fw show info` reported its expected 16,666,624-byte size and image-header MD5
`2ACDA0866E032DA153B7C709329DDE41`; after an AP reboot, `get director` again
reported `RUN` and `get version` reported `10.5.1.0.282`.

Do not mark a release/profile supported until every applicable row passes with
the transformed bundle built from that exact vendor download.
