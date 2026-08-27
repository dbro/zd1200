# ZD1200 browser builder and portable runtime roadmap

Status: planning baseline, 2026-08-27

## Product goal

Turn `dbro/zd1200` into a source-only project that lets a user select an
original encrypted or already-decrypted ZoneDirector download in a static web
page, configure a small set of options, and receive a self-contained ZIP that
can be started with a local Docker build and `docker compose up`.

The transformation must occur entirely in the browser. Vendor firmware must
not be uploaded to a server, committed to this repository, embedded in a
container image, or redistributed by the project. The generated ZIP may contain
the user's locally transformed vendor files.

Managing physical APs is fundamental. A build that exposes only the ZD web UI
does not satisfy the project goal.

## Initial support policy

### Host platforms

| Tier | Host | Acceleration | Physical-AP networking | Initial status |
| --- | --- | --- | --- | --- |
| 1 | Linux x86-64 with Docker Engine | KVM preferred; TCG fallback | Required and validated | Supported |
| 2 | Linux ARM64 with Docker Engine | QEMU TCG | Required by design | Experimental until hardware validation |
| Out of initial scope | macOS Docker Desktop, including Apple Silicon | QEMU TCG | No validated equivalent to the Linux TAP/bridge design | Unsupported |
| Out of initial scope | Windows Docker Desktop | QEMU TCG | No validated equivalent to the Linux TAP/bridge design | Unsupported |

Linux x86-64 is the first release target because it is common, it can expose
KVM and TAP devices directly to Docker, and physical R600 tests are available.
Linux ARM64 remains an explicit design target: builds and non-hardware tests
must run in CI, but the documentation must label it experimental until it is
tested on an ARM64 Linux host with physical APs.

The project may revisit macOS when suitable hardware and a repeatable Layer-2
networking method are available. Merely making the x86 guest boot on Apple
Silicon is not enough.

### ZoneDirector releases

| Release family | Reason | Exact build status |
| --- | --- | --- |
| 10.5.1.0.282 | Primary known-working release and R600 mesh-repair target | Known |
| 10.3.1.0.45 | Last 10.3.1 refresh listed by Ruckus; local fixture is older `.42` | Obtain/decrypt `.45`; `.42` is experimental only |
| 10.2.1.0.232 | Latest 10.2.1 refresh currently listed by Ruckus | Local fixture available; unsigned-image behavior must be verified |
| 10.1.2.0.318 | Latest 10.1.2 refresh currently listed by Ruckus | Local fixture available; H500 behavior must be verified |

Support is per exact build, not merely per marketing version. A build becomes
supported only after its archive layout, hashes, patch signatures, boot path,
web UI, controller persistence, AP join, and AP firmware delivery have passed.
Other structurally recognized builds may be offered as experimental with a
clear disclaimer and fail-closed patch behavior.

### User-selectable options

The first browser UI should support:

- automatic release/build detection;
- support-agreement expiration date, with an empty value meaning no expiry;
- R600 mesh receive repair when its affected signature is present;
- FTP-based AP firmware delivery for releases that require it;
- ECDSA SSH compatibility, after the exact algorithm/configuration change is
  defined (ECDSA is a key/signature algorithm, not an encryption cipher);
- optional diagnostic root SSH, enabled only when the user supplies a valid
  public key;
- serial number and locally administered unicast base MAC address;
- displayed SHA-256 hashes for input, decrypted content, important transformed
  artifacts, and final ZIP.

Required platform patches are automatic. Experimental or security-sensitive
enhancements belong in an Advanced section with an explanation of their effect.

## Intended user experience

1. Open the static page hosted from the existing VuePress site.
2. Select an encrypted Ruckus download or an already-decrypted archive.
3. The page detects format, product, release, build, and known archive layout.
4. Review detected values, hashes, compatibility status, and warnings.
5. Configure identity, expiration date, networking notes, and optional
   enhancements.
6. Select **Build ZIP**. A Web Worker decrypts, extracts, validates, patches,
   and packages locally.
7. Download and extract the generated ZIP.
8. Attach a dedicated Ethernet adapter as documented, adjust `.env` or
   `compose.yaml` if necessary, then run:

   ```sh
   docker compose build
   docker compose up -d
   ```

9. Follow a short validation checklist to complete the wizard and join an AP.

The ZIP should contain only the transformed vendor artifacts needed at runtime,
not a duplicate of the original download. Its size should therefore remain in
the same general range as the input.

## Architecture direction

### One declarative catalog

The current Python patch catalog is a useful reference implementation, but the
browser requirement makes a language-neutral catalog preferable. Move artifact
and patch data to a versioned JSON document with a checked JSON Schema. Both the
Python reference tools and TypeScript browser/Node tools must consume that same
catalog.

Each patch references a reusable `artifact_id`. Each artifact definition names:

- a format detector;
- an extraction handler;
- a rebuild handler;
- optional architecture/format assertions;
- one or more supported exact release manifests;
- its ordered patch rules.

Handlers remain reviewed code rather than strings evaluated from the catalog.
Unknown handlers, duplicate matches, missing required matches, overlapping
writes, size changes that violate a container layout, and failed post-patch
verification are hard errors.

### Shared transformation core

Develop the browser transformation core as TypeScript that can run in both Node
and a Web Worker. Use the Node entry point for deterministic fixtures and CI;
use the browser adapter only for file selection, progress, and download.

The existing Python tools remain the reference oracle during migration and are
useful inside the generated Docker bundle. Golden tests must prove that the
TypeScript and Python implementations produce identical patched payloads before
the browser implementation becomes authoritative.

### Browser pipeline

```text
selected file
  -> format detection
  -> optional local decryption
  -> safe archive parsing
  -> exact-build manifest selection
  -> artifact extraction
  -> patch/enhancement application
  -> post-patch verification and hashes
  -> Docker/runtime template assembly
  -> streaming ZIP download
```

All expensive work should run in a Web Worker. Archive paths must be normalized
and traversal rejected. The implementation should prefer streaming or bounded
copies so that browser memory does not grow by many multiples of the firmware
size.

### Docker/runtime bundle

The project publishes source, not prebuilt container images. The generated
bundle contains a Dockerfile, Compose configuration, scripts, documentation,
and the user's transformed firmware. Docker builds the runtime locally for the
host architecture.

Use explicit runtime profiles or generated variants only where their behavior
is genuinely different. The first supported profile is Linux physical-AP
networking. A simple web-only profile is not a release target.

## Milestones

Milestone status values are `pending`, `in progress`, `blocked`, and `complete`.
A milestone is complete only when all exit criteria pass and evidence is
recorded without committing vendor firmware.

### M0 — Repository, licensing, and security baseline

Status: in progress

Work:

- establish `dbro/zd1200` as the canonical source repository;
- preserve attribution for contributor work and document contribution flow;
- merge/generalize the artifact-aware patch engine and catalog;
- inventory every committed binary and its license/source;
- remove committed private test keys and replace them with generated fixtures;
- ensure diagnostic root SSH is disabled unless a public key is supplied;
- document the vendor-firmware boundary and generated-file exclusions;
- choose the licensed decryption implementation.

Licensing gate: the displayed
[`DecryptBackupSource`](https://ms264556.net/ruckus/DecryptBackupSource) page
states that its shown TypeScript grants no copying rights, even though it links
to the permissively licensed
[`aioruckus`](https://github.com/ms264556/aioruckus) implementation. Use code
only from a repository with an applicable license or with explicit written
permission, and retain attribution and an upstream reference.

Tests:

- repository secret/private-key scan;
- license/source inventory check;
- clean checkout contains no Ruckus firmware;
- generated root SSH configuration is absent when no public key is supplied.

Exit criteria:

- source and licensing boundaries are unambiguous;
- no reusable private credential remains;
- README links this roadmap and states the supported-host policy.

Progress evidence (2026-08-27): tracked Dropbear test private/public key
material and its default `authorized_keys` file were removed. The runtime
injects the optional diagnostic listener only if an operator supplies a local
`zd-dropbear2222/authorized_keys` file, which is ignored by Git. The listener
remains an M3 feature until its payload provenance and an end-to-end
public-key-only test are documented. The four remaining prebuilt diagnostic
payload binaries were introduced by contributor commit `20fc444`; no source
or license record is present in this repository, so they are not eligible for
public-release use until that provenance is supplied or the payload is removed.
`PROVENANCE.md` records this inventory, release gate, and the no-vendor/no-key
boundary for reviewers. A local runtime-initramfs packaging test with no
operator key verified that neither `zd-dropbear2222` nor `authorized_keys` was
included. The decryption licensing gate is now resolved for legacy TAC-format
ZD archives: `ruckus_tac_decrypt.py` adapts the relevant `aioruckus` routine at
commit `9bc44024601ed1798e096d99d192903fb5d16355` under BSD-0-Clause with a
retained third-party notice. Its output matched the known decrypted 10.5.1
fixture exactly; support for another encryption format still requires a
separate identified/licensed/tested implementation.

### M1 — Exact-build inventory and manifest schema

Status: in progress

Work:

- obtain encrypted and/or decrypted local fixtures for each target build;
- determine exact final builds for 10.3.1 and 10.1.2;
- record input hashes, decryption format, metadata, archive layout, kernel/rootfs
  formats, AP payload layout, and expected output hashes;
- define versioned JSON Schema for release, artifact, handler, and patch data;
- migrate the current kernel and R600 patch definitions into the shared catalog;
- classify required, optional, experimental, and inapplicable patches;
- add human-readable provenance for every signature.

Tests:

- schema validation;
- exact fixture detection with local, gitignored files;
- rejection of near-match or modified metadata fixtures;
- original and already-patched signature uniqueness;
- round-trip container invariants and output length checks.

Exit criteria:

- every available target build is either supported with exact metadata or
  explicitly listed as awaiting a fixture;
- both Python and TypeScript can load and validate the same catalog.

Progress evidence (2026-08-27): `binary_patch_catalog.json` version 1 and its
JSON Schema now define the current R600 and ZD kernel artifacts/rules. The
Python loader enforces the same closed field set, identifier grammar, masked
byte grammar, artifact references, and in-place replacement sizes without a
third-party dependency. `release_manifest.json` now separately records the
exact known 10.5.1.0.282 encrypted/decrypted hashes, vendor metadata, required
archive paths, and applicable artifact IDs; a TypeScript consumer remains
pending. `verify_release_archive.py` independently verified the local
decrypted 10.5.1.0.282 archive's hash, 1,246-member gzip-TAR structure, safe
paths/links, required layout, and metadata without extracting it. The existing
10.5.1 preparation command now invokes that verifier and no longer accepts an
environment override for the supported archive hash. Local
proprietary-fixture integration reproduced the known patched R600 module
exactly (SHA-256
`57bc0e93174a4f73fd8b82834ac0b375a2875fc64527c6605d6b84a7e64cdaf4`)
and produced the known patched ZD kernel container SHA-256
`c3014270e817be56b2b3c79223bbb588ac8c28130662f35c42b77ede2c609803`.
The Linux x86-64 Docker image build completed locally and the resulting image
loaded all six catalog rules.

Local, Git-excluded fixture inventory (2026-08-27):

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `zd1200_10.1.2.0.318.ap_10.1.2.0.318.img` | 152506552 | `0e06015d85a004c42a512dee2a3be0aee9a3851999ff7221d9fda6626e592069` |
| `zd1200_10.2.1.0.232.ap_10.2.1.0.232.img` | 113418232 | `110703c3c4492db0b31be0c265fffed9e468fa2b9bd733a518d09577f31eba67` |
| `zd1200_10.3.1.0.42.ap_10.3.1.0.42.img` | 180379736 | `55605ad5baefd237bc7a7018fc01991e60c7390cc20e46959976556e2511a8e1` |
| `zd1200_10.5.1.0.282.ap_10.5.1.0.282.img` | 174356328 | `d18591e40c535f3ac0f3a6c767708c0f40ae4e347fc2fdfb51e28fba11b26c90` |
| `R600_110.0.0.0.675-standalone.bl7` | 10971132 | `86d77347385a544bbd8bd4cb299ed13b3d1f1c7b866825fa811f1175b7d5389e` |
| `R600_200.7.10.202.145-unleashed.bl7` | 24594428 | `736cdb5ae748137553d78d256240720bcc4217566da2b2052089651482a53879` |
| `R600_10.5.1.0.282-rx-vlan-fix-UNSIGNED.bl7` | 16666624 | `92edb7709f098f1f9a928bfe407b1765da0a8d252b9e4c14797178d6ba6cce43` |

The four `.img` inputs share the same opaque encrypted header family. The
similarly named 10.5.1 `.img.tgz` is a valid decrypted gzip-compressed tar
archive, is two bytes shorter than its encrypted `.img`, and has SHA-256
`64dfbf4d67cc65cafa0e258e426c664c7387b1219209ec893b9b1e41ab202cb8`.
This provides paired encrypted/decrypted 10.5.1 fixtures for pipeline tests.

Official-catalog check (2026-08-27): the
[10.3.1.0.45 ZD1200 release](https://support.ruckuswireless.com/products/73-zonedirector-1200?open=software)
appears after local build `.42`, so `.42` cannot be called final. The same
catalog currently lists `.232` as the latest 10.2.1 release; Ruckus's
[10.1.2.0.318 ZD1200 download](https://support.ruckuswireless.com/software/2759-zd1200-10-1-2-0-318-mr2-refresh7-software-release)
identifies it as MR2 Refresh7. These are catalog observations, not substitutes
for per-build boot and hardware validation.

### M2 — Deterministic command-line bundle builder

Status: in progress

Work:

- implement automatic encrypted/decrypted input detection;
- implement licensed local decryption with attribution;
- safely extract the vendor archive;
- apply artifact handlers and patch sets selected by the manifest;
- normalize the root filesystem input without mutating the original;
- generate Docker/runtime templates, `.env`, and `README-FIRST.md`;
- produce a deterministic ZIP and a machine-readable build report;
- never include the original encrypted input in the output.

Tests:

- golden output hashes for locally held fixtures;
- encrypted and decrypted inputs for the same build produce equivalent bundles;
- malicious archive-path fixtures are rejected;
- corrupted, unsupported, ambiguous, and already-patched inputs behave as
  documented;
- repeat builds with the same inputs/options are byte-for-byte reproducible,
  excluding explicitly documented timestamps if unavoidable.

Exit criteria:

- one command generates a runnable 10.5.1.0.282 bundle;
- the build report lists every detected artifact, patch decision, and hash;
- no manual firmware extraction is required.

Progress evidence (2026-08-27): the 0BSD-licensed `aioruckus` legacy TAC
routine was adapted with attribution and no third-party runtime dependency. It
reproduced the locally held 10.5.1.0.282 decrypted gzip-TAR byte-for-byte from
the encrypted vendor input. The same transform produced a valid 10.2.1.0.232
gzip-TAR; its exact input/output hashes, metadata, and layout are now recorded
as `experimental` in the release manifest. The existing 10.5.1 preparation
command accepts either exact encrypted or exact already-decrypted input and
performs hash/structure/metadata verification locally. Generalized extraction,
runtime generation, and per-release patch selection remain pending.

The initial `build_zd1200_bundle.py` now assembles a local Docker bundle,
applies the kernel catalog rules, emits `build-report.json`, and marks the
unimplemented R600 BL7 repacker explicitly. Payload-TAR creation and the known
kernel output have deterministic tests. A local 10.5.1.0.282 decrypted-input
run produced a ZIP that passed `unzip -t`, repeated with identical SHA-256
`f65693b66eac35b1ec6b6ee86e9452b0fe10fc2f240f5a4ba10939b2cc9b8880`, and
contained the complete runnable source/image layout. A full encrypted-input
packaging run remains pending completion outside the terminal sandbox's
process-duration limit; its decryption and archive verification are tested
separately. `ruckus_bl7.py` now validates and round-trips the known unsigned
R600 BL7 container, recalculating offsets, payload MD5, and header checksum;
signed ISI/FSI images fail closed. SquashFS handling and AP-module integration
remain the next packaging task.

### M3 — Version-independent runtime enhancements

Status: pending

Work:

- make initramfs/runtime overlays manifest-driven rather than tied to 10.5.1;
- implement FTP-based AP firmware delivery without permanently weakening
  unrelated services;
- implement support-agreement expiration configuration, including blank/no
  expiry;
- precisely define and implement the requested ECDSA SSH compatibility option;
- make public-key-only diagnostic root SSH optional and version-aware;
- generate unique, valid board identity and preserve it in Docker state;
- remove test-only defaults that could collide on a real LAN.

Tests:

- service/process and listening-port assertions inside each guest;
- FTP control and actual AP firmware transfer test, not merely an open port;
- support-expiration behavior before, on, and after the configured date;
- SSH algorithm negotiation tests with the option on and off;
- root SSH absent by default and public-key-only when enabled;
- factory reset and persistent restart behavior.

Exit criteria:

- enhancements are independently selectable where appropriate;
- required legacy behavior is enabled automatically by the release manifest;
- security-sensitive services have safe defaults and explicit documentation.

### M4 — Linux physical-AP networking

Status: in progress

Work:

- evaluate the safest low-step way to give QEMU Layer-2 access to a dedicated
  Ethernet adapter from Docker;
- compare the existing host systemd TAP helper with a narrowly privileged
  Compose helper and other Linux-native approaches;
- require explicit adapter selection and refuse the host default-route adapter;
- make setup reversible and preserve host connectivity on failure;
- document dedicated USB Ethernet as the recommended topology;
- expose only the controller services required for operation.
- keep the controller/AP acceptance steps and pass criteria in
  `VALIDATION.md`, including the mesh regression test.

Tests on Linux x86-64:

- start from a clean Docker host using only documented commands;
- controller obtains the configured identity and is reachable;
- physical AP discovers or is directed to the controller and reaches `RUN`;
- AP firmware download succeeds, including an FTP-requiring release;
- controller restart, container restart, host reboot, and factory reset;
- unplug/replug dedicated adapter and recover without corrupting state;
- safety test refuses an adapter carrying the host default route.

Exit criteria:

- a Docker-comfortable user can attach physical APs without manually creating
  bridges or TAP devices;
- setup and removal are documented and tested;
- failure does not strand the host network.
- no profile is called supported merely because the controller web UI loads.

Current Linux x86-64 bench evidence (2026-08-27):

- Debian 12 Docker host reached over a separate built-in management NIC;
- dedicated USB NIC attached to an unnumbered `br-zd` and `tap-zd` while the
  default route remained on the management NIC;
- helper rejected/guards against using the default-route interface and checks
  the configured USB MAC;
- QEMU/KVM booted the signature-patched 10.5.1.0.282 kernel from Compose and
  remained healthy with the host bridge unnumbered;
- a reservation-only, temporary DHCP endpoint assigned `192.168.222.10` to the
  factory ZD and `192.168.222.13` to a factory-reset R600; both responded from
  the independent management laptop;
- ZD factory setup completed with Germany (`DE`) as the regulatory domain, DFS
  enabled, IPv4 management fixed at `192.168.222.10/24`, and its generated
  administrator credential retained outside the repository;
- the factory-reset R600 (`94:f6:65:0c:60:b0`) was automatically discovered,
  approved, and reached controller state `1` at `192.168.222.13`;
- ZD's supported AP configuration path changed the R600 from DHCP/keep-setting
  to static `192.168.222.13/24`; an AP restart with the DHCP endpoint stopped
  proved that the address persisted and that the AP rejoined in state `1`;
- a ZD container restart with DHCP still stopped proved that the controller's
  static address, completed wizard state, login, AP record, and AP discovery
  persisted; the temporary DHCP container and network were then removed;
- after validation, the host management NIC remained `192.168.20.41/24` and
  the dedicated USB NIC plus `br-zd` remained unnumbered;
- actual AP firmware transfer, host restart, and USB unplug/replug recovery
  remain outstanding. The AP reports `10.5.1.0.282`, but that alone is not
  evidence that this bench transferred the image. A prior two-R600 lab
  established bidirectional management traffic with the receive repair; repeat
  that mesh test from the generated bundle before calling the bundle validated.

### M5 — Release validation matrix

Status: pending

Validate, in order:

1. 10.5.1.0.282;
2. 10.2.1.0.232;
3. 10.3.1.0.45 (obtain fixture; `.42` can be exercised separately);
4. 10.1.2.0.318.

For each exact build, record:

- input and extracted artifact hashes;
- applied/skipped patch rules;
- boot time and accelerator;
- factory wizard and login;
- persistent restart;
- AP adoption and `RUN` state using available R600 hardware;
- firmware delivery protocol and successful transfer;
- client WLAN forwarding where supported;
- diagnostics after an extended run.

The absence of R700, H500, and non-R600 test hardware must be stated. Static
payload inspection and successful controller boot do not prove model support.
Invite community validation and record external reports separately from locally
reproduced results.

Exit criteria:

- every advertised build has a published evidence row;
- unsupported/unverified AP models are not described as tested.

### M6 — Linux ARM64 experimental support

Status: pending

Work:

- make the Dockerfile build on `linux/arm64` without distributing images;
- install/use the correct cross-binutils inside the build rather than requiring
  users to alter host `PATH`;
- run the x86 guest with QEMU TCG;
- keep networking design architecture-neutral;
- add CI build and synthetic boot-format tests on ARM64 where available.

Tests:

- ARM64 Docker build;
- catalog, transformation, bzImage, initramfs, and disk-image tests;
- QEMU reaches a deterministic guest boot marker under TCG in CI or an ARM64
  Linux test environment;
- physical-AP networking remains explicitly unvalidated until hardware exists.

Exit criteria:

- ARM64 is usable by an informed tester and clearly labeled experimental;
- no ARM64-specific manual binutils symlink workaround is required.

### M7 — Static VuePress browser builder

Status: pending

Work:

- integrate into the existing VuePress repository through a reviewable PR;
- run detection, decryption, extraction, patching, hashing, and ZIP generation
  in a Web Worker;
- reuse the shared catalog and golden fixtures without vendor files;
- show stepwise progress and actionable errors;
- show hashes and a complete build report before download;
- state prominently that no selected data leaves the browser;
- perform no analytics or network requests during transformation;
- handle both encrypted and already-decrypted input;
- expose only options applicable to the detected release.

Tests:

- unit tests shared with the Node CLI;
- browser tests in current Chromium and Firefox;
- network-interception test confirms zero upload/telemetry requests;
- cancel/retry behavior and large-file memory measurements;
- generated ZIP matches the command-line golden output semantically and, where
  practical, byte-for-byte;
- static-site build and link checks.

Exit criteria:

- a user can generate the validated Linux x86-64 bundle without installing a
  firmware-preparation tool;
- browser and CLI reports agree on detection, patches, and hashes.

### M8 — Public beta documentation and release

Status: pending

Work:

- replace development notes with a short install-first guide;
- provide dedicated guides for firmware selection, networking, recovery,
  upgrades, optional SSH, and diagnostics;
- publish the exact support matrix and known limitations;
- add issue templates that request version/build, hashes, host architecture,
  Docker version, AP model, and generated build report without requesting
  vendor firmware;
- add contribution instructions for new manifests and validation evidence;
- tag a public beta only after the Linux x86-64 physical-AP exit criteria pass.

Exit criteria:

- a fresh tester can go from vendor download to an adopted AP using the written
  instructions;
- recovery and removal procedures are tested;
- release artifacts contain source and documentation only.

## Test strategy without redistributing firmware

- Keep all vendor fixtures outside Git and address them by SHA-256 in local
  test configuration.
- Generate small synthetic archives and ELF-like byte fixtures for public CI.
- Test masked matching, ambiguity, rel32 rewriting, container boundaries,
  traversal rejection, catalog validation, option rendering, and ZIP assembly
  with synthetic fixtures.
- Run proprietary-fixture integration tests locally and save only sanitized
  reports containing hashes, offsets, decisions, and pass/fail results.
- Never print decryption keys, credentials, private keys, or firmware content in
  CI logs.
- Treat physical-hardware results as a separate evidence layer from unit and
  boot tests.

## Autonomous work and blocker policy

For each milestone:

1. keep its status current in this file;
2. work in small reviewable commits with tests and documentation together;
3. preserve unrelated user changes and never commit vendor-derived artifacts;
4. run public synthetic tests plus available local proprietary-fixture tests;
5. record evidence and known gaps before marking the milestone complete;
6. proceed to the next unblocked task without waiting for routine approval.

Stop and request input only when progress requires:

- a missing exact firmware fixture or physical device;
- permission or licensing not established by the repository;
- a choice that changes the security or networking contract;
- external publication, repository administration, or another action requiring
  new authority;
- destructive handling of user state or hardware networking beyond the
  documented test adapter.

## Open items requiring definition or evidence

- Obtain a local 10.3.1.0.45 fixture; official release listings show the
  available `.42` fixture is not the final 10.3.1 refresh.
- Confirmation of what 10.2.1.0.232 unsigned-image behavior must be retained.
- Exact implementation and desired compatibility effect of “enable ECDSA SSH.”
- Existing method/signatures for support-agreement expiration changes.
- Which releases require FTP firmware delivery and the required daemon/config,
  ports, and AP-visible addressing.
- Licensed source location or explicit permission for the browser decryption
  implementation.
- A Linux ARM64 host and non-R600 AP models for later validation.

## Definition of the first useful public beta

The first beta may support only Linux x86-64 and 10.5.1.0.282, but it must:

- accept encrypted and decrypted input locally;
- produce a self-contained, locally buildable Docker bundle;
- configure a safe unique board identity;
- attach to a dedicated physical Ethernet adapter;
- adopt and manage a physical R600;
- deliver AP firmware successfully;
- apply and verify the R600 mesh repair when applicable;
- survive controller/container restart with persistent state;
- provide recovery/removal instructions;
- contain no vendor firmware, private keys, or prebuilt container image in the
  public repositories.
