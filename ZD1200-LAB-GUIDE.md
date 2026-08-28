# ZD1200 Lab Guide — patched kernel + optional diagnostic Dropbear

> Historical developer notes. The source-only project no longer ships the
> prebuilt Dropbear/OpenSSH payload described below, because its provenance was
> not suitable for release. The supported recovery mechanism is the optional
> local root CLI documented in `README.md`; do not treat port-2222 SSH or this
> guide's external payload build as a supported generated-bundle feature.

This guide covers the QEMU lab for the Ruckus ZoneDirector 1200 (10.5.1.0.282)
that was brought up in this directory, and specifically how to run **your own
build** of dropbear 2026.94 + OpenSSH sftp-server (from `../src/out/`) on
guest port **2222 with public-key-only auth**, reachable from the host over
loopback (`127.0.0.1:2222`).

Everything below is the *outcome* of debugging this setup — the "Development
hints" section at the end collects the non-obvious traps that cost the most
time.  Read it before changing anything.

---

## 1. What was added/changed in this repo

| file | role |
|---|---|
| `patch-kernel.py` | applies the six `zd1200-patch.gdb` byte patches to the kernel and splices it back into a bootable `bzImage` (TCG-compatible replacement for the KVM-only gdb flow) |
| `run-zd1200-lab.sh` | one-shot launcher: validates/builds `image/`, patched kernel, runtime initramfs, VM disk; then execs QEMU |
| `zd-dropbear2222/` | optional local deployment payload: `dropbear`, `dropbearkey`, `dropbearconvert`, `sftp-server` (from `../src/out/`) plus an operator-supplied, Git-ignored `authorized_keys` |
| `make-runtime-initrd.sh` *(modified)* | now bundles `zd-dropbear2222/` into the runtime initramfs at `/zd-dropbear2222` |
| `boot-initrd-handoff` *(modified)* | copies the payload into the data partition each boot, writes a real `/etc/passwd` + `/etc/shells`, installs `S99zd_dropbear2222` which generates host keys and starts dropbear on 2222 |
| `run-zd1200-qemu.sh` *(modified)* | added `KERNEL=` override and `EXTRA_HOSTFWD=` (used for `tcp:127.0.0.1:2222-:2222`) |

All QEMU networking uses `NETWORK_MODE=user` (SLIRP) with host forwards bound
to `127.0.0.1` — a loopback-only adapter.  No Docker is required.

## 2. Prerequisites

- x86_64 (or aarch64 — see hints) Linux host, `qemu-system-i386` + `qemu-img`
  (Debian/Ubuntu: `apt install qemu-system-x86`), `python3`, `curl`,
  `ssh` client, ~6 GB free disk.
- `/dev/kvm` **optional**: with KVM the stock repo flow works as-is
  (its gdb patch uses `hbreak`).  Without KVM (TCG) you need
  `patch-kernel.py` — which is what this repo's `run-zd1200-lab.sh` uses
  either way.
- `prepare-vendor-image.sh` must have been run once (see below) to populate
  `image/` from your decrypted `zd1200_10.5.1.0.282.ap_10.5.1.0.282.img.tgz`
  (SHA-256 `64dfbf4d…`).

## 3. One-time preparation

```sh
# aarch64 hosts only: the repo calls bare `as --32` / `ld -m elf_i386`, which
# the aarch64 binutils reject.  Point at the i686 binutils via PATH:
mkdir -p /tmp/zd-i686-bin
ln -sf /usr/bin/i686-linux-gnu-as /tmp/zd-i686-bin/as
ln -sf /usr/bin/i686-linux-gnu-ld /tmp/zd-i686-bin/ld
export PATH="/tmp/zd-i686-bin:$PATH"   # only needed for prepare-vendor-image.sh

./prepare-vendor-image.sh /abs/path/to/zd1200_10.5.1.0.282.ap_10.5.1.0.282.img.tgz
```

This produces `image/bzImage`, `image/vmlinux`, `image/rootfs.ext2`
(**gzip-compressed — see hint #2**), `image/restoreinitramfs.gz` and the
payload tarball.

## 4. Build the dropbear binaries (if you changed them)

```sh
cd ../src
./build-zd1200-dropbear.sh --keep        # or run clean (no --keep)
cp -a out/dropbear out/dropbearkey out/dropbearconvert out/sftp-server \
      ../dbro_zd1200/zd-dropbear2222/
```

> The build **must** run with hardening disabled (`--disable-harden` /
> `--without-stackprotect` / `-fno-stack-protector`) — the build script
> already does this and now refuses to pass if any output contains TLS
> stack-protector canaries.  See hint #3.

## 5. Launch

```sh
./run-zd1200-lab.sh              # builds anything missing, then starts QEMU
```

Optional flags: `--reset-disk` (factory reset), `--rebuild-kernel`,
`--wait [SECS]`.  The script prints the URLs and SSH hint before exec'ing
QEMU; the guest console is attached (Ctrl-A X to quit QEMU).

Under TCG expect **~2–4 minutes** from launch to the login page / dropbear.

Diagnostic root SSH is disabled unless this local (untracked) file exists:

```sh
mkdir -p zd-dropbear2222
ssh-keygen -t ed25519 -N '' -f ~/.ssh/zd1200-diagnostic
cp ~/.ssh/zd1200-diagnostic.pub zd-dropbear2222/authorized_keys
chmod 600 zd-dropbear2222/authorized_keys
```

The public key is not shipped by this project. Remove that file and restart
the VM to disable the diagnostic listener again.

## 6. Verify

```sh
# web (factory wizard until configured):
curl -kIs https://127.0.0.1:38443/admin10/login.jsp     # expect 302 wizard.jsp

# SSH — pubkey-only, port 2222 (generate a key first, see hint #8):
ssh -i /tmp/dbtest/testkey -p 2222 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    root@127.0.0.1 'id; uname -a'
# expect: uid=0(root) ... Linux Ruckus-Unleashed 2.6.32.24 ... i686

# SFTP (the custom sftp-server, via the compiled SFTPSERVER_PATH):
printf 'put /etc/hostname /tmp/x\n' | sftp -P 2222 \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    -b - root@127.0.0.1
```

Password auth is disabled server-side (`-s`): a wrong/absent key fails with
`Permission denied (publickey)` and the server never offers a password path.

## 7. How the dropbear deployment works (boot-time injection)

Nothing is baked into the VM disk for the binaries; the payload is carried in
the runtime initramfs and re-copied on every boot, so updating
`zd-dropbear2222/` only needs an initramfs rebuild (automatic on next launch):

1. `make-runtime-initrd.sh` appends `/zd-dropbear2222` (the payload) to
   `bootinitramfs.runtime.gz`.
2. `boot-initrd-handoff` (runs in the initramfs before pivot) copies it to
   `/data/dropbear2222` **and** `/data/data/dropbear2222` (hda4 = the data
   partition = `/writable` after boot; the second copy satisfies the compiled
   `SFTPSERVER_PATH=/writable/data/dropbear2222/sftp-server`), seeds a real
   `/etc/passwd` (root shell `/bin/ash`) and `/etc/shells`, and installs
   `/etc/init.d/S99zd_dropbear2222` on the root fs.
3. On the controller boot, `S99zd_dropbear2222` generates
   `hostkey_ed25519`/`hostkey_rsa` once (persisted in the qcow2 overlay) and
   starts:
   `dropbear -p 2222 -s -D /writable/dropbear2222 -r …ed25519 -r …rsa -F -E`
   (`-s` = no passwords; `-D` = global `authorized_keys` directory).

The console shows the S99 diagnostics on every boot (`/etc/passwd`, host-key
generation, dropbear banner) — that is the first place to look when something
does not come up.

---

## 8. Development hints (non-obvious traps)

1. **aarch64 host: the repo's `as --32` / `ld -m elf_i386` fail.**  The
   host `as`/`ld` are the aarch64 binutils and reject i386 flags.  Prepending
   a dir with symlinks to `i686-linux-gnu-as`/`i686-linux-gnu-ld` to `PATH`
   fixes `prepare-vendor-image.sh` (see §3).  `patch-kernel.py` and the
   launch script do not need it.

2. **`image/rootfs.ext2` is gzip, not ext2.**  The vendor archive stores
   `rootfs.i386.ext2.director1200.img` compressed; `prepare-vendor-image.sh`
   copies it verbatim, so `make-synthetic-cf.py` seeds the disk partitions
   with gzip bytes and the handoff's `mount -t ext2 /dev/hda2` fails with
   *Invalid argument*.  Decompress it first.  Note the ext2 superblock magic
   `0xEF53` lives at byte **1080** (`s_magic` = superblock + `0x38`), not
   1024 — checking 1024 gives a false "not ext2".  `run-zd1200-lab.sh`
   decompresses automatically.

3. **TLS stack-protector canary = instant segfault on the device.**  The
   ZD's uClibc 0.9.30.1 has no TLS, so `mov %gs:0x14` (canary read emitted by
   `-fstack-protector-strong`) faults with `segfault at 14` in *every*
   function prologue — even `dropbear -V`.  Symptom on the console:
   `dropbear[NNN]: segfault at 14 ip 0804xxxx`.  Fix: build with
   `--disable-harden` (dropbear) / `--without-stackprotect` (OpenSSH) and
   `-fno-stack-protector`; the build script now enforces zero `gs:0x14`
   references.  Verify a suspect binary with:
   `i686-linux-gnu-objdump -d out/dropbear | grep -c 'gs:0x14'`.

4. **TCG cannot run the repo's gdb patch, so the kernel is patched
   statically.**  `zd1200-patch.gdb` uses `hbreak`, which QEMU's TCG backend
   rejects ("No hardware breakpoint support"), and `-S` pauses before paging
   exists, so software breakpoints can't be inserted either.  The six edits
   are static byte writes, so `patch-kernel.py` applies them to the kernel
   ELF directly.  **Critical layout detail:** in this vendor bzImage the
   gzip member does *not* run to EOF — a second-stage boot loader (the code
   starting at file offset 2529336 with `push $0x2667cc`) is linked *after*
   the member, and the head code jumps to a baked offset of it.  Replacing
   the whole tail destroys the loader and the guest executes a zero-sled
   (EIP wanders through `0x02xxxxxx`, all bytes zero).  Only the member
   region may be replaced; its end is found via the gzip trailer
   (`crc32(payload) + ISIZE` of the decompressed ELF).  The recompressed
   stream must not exceed the original member length (the decompressor is
   fed a fixed input size; inflate stops at the trailer, so zero padding
   after it is fine).

5. **Boot-time, watchdog, and restart.**  The stock COB7402/NAR5520 watchdog
   path reaches `kernel_halt()` during early boot.  The patch catalog disables
   only `nar5520_wdt_init()` and `nar5520_wdt_thread()`.  It leaves the general
   `kernel_halt()` path intact, but replaces the appliance-specific
   `machine_restart()` entry with QEMU's i8042 system-reset command; a ZD web
   restart therefore performs a real guest reboot rather than halting the VM.
   A TCG boot to the login page / dropbear takes ~2–4 minutes; the first kernel
   serial output appears only after decompression, so be patient before
   concluding it hangs.

6. **Auth "Permission denied (publickey)" has three layers.**
   - `/etc/passwd` on the rootfs is a **symlink** (target
     `/writable/etc/config/passwd`, 27 chars) into the data partition, so
     `getpwnam()` only works once `/writable` is mounted.  The handoff
     replaces it with a real file for the lab.
   - Dropbear validates the user's shell against `/etc/shells`; the vendor
     file lists only `/bin/ash` and `/sbin/rkscli`, so a root shell of
     `/bin/sh` is rejected ("invalid shell").  Root's shell is `/bin/ash`
     in the seeded passwd.
   - `authorized_keys` must be owned by root (or the user) and not
     group/world-writable (`600`); dropbear opens it as the authenticating
     user.  The `-D <dir>` option means "global keys at `<dir>/authorized_keys`"
     for all users — no `~` expansion for absolute paths.

7. **`dropbear -v` does not exist in this build** (DEBUG_TRACE off) —
   `Invalid option -v` kills the server.  Use `-E` to log to stderr/console.

8. **Use an operator-owned key.** Generate one with
   `ssh-keygen -t ed25519 -N '' -f <path>` and put the `.pub` line into the
   Git-ignored `zd-dropbear2222/authorized_keys`; the handoff refreshes it
   every boot (initramfs rebuild is automatic). First connect: add
   `-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null` or accept
   the host key (fingerprint is printed on the console at key generation).

9. **Persistence and factory reset.**  All VM writes (host keys, seeded
   passwd, controller state) live in the qcow2 overlay `zd1200-vm.qcow2`
   over `synthetic-cf.img`; deleting both (`--reset-disk`) returns the VM
   to the factory wizard.  In a dockerized deployment, that overlay is what
   should live on a named volume so `/writable` (the controller's account
   DB at `/writable/etc/config/passwd`) persists.

10. **Running QEMU from scripts/CI.**  QEMU keeps the terminal busy, so
    automated launchers hang waiting on the pipe; detach with
    `setsid env … ./run-zd1200-qemu.sh </dev/null >log 2>&1 &`.  The lab
    script itself is meant to run in a normal terminal (console attached).

11. **KVM is fine too.**  With `/dev/kvm` present, `ACCEL=kvm
    ./run-zd1200-lab.sh` boots the same patched kernel (the patches are
    static bytes; the gdb flow becomes unnecessary).

12. **sftp-server path is compiled in.**  `SFTPSERVER_PATH` is baked as
    `/writable/data/dropbear2222/sftp-server` (see `src/patches/dropbear/
    0001-sftpserver-path.patch`); the handoff mirrors the payload to both
    `/data/dropbear2222` and `/data/data/dropbear2222` so the subsystem
    resolves.  If you change that patch, update the mirror list in
    `boot-initrd-handoff` accordingly.

## 9. Troubleshooting cheat sheet

| symptom | likely cause / fix |
|---|---|
| handoff `mount /dev/hda2 … Invalid argument` | rootfs still gzip — see hint #2 |
| `System halted.` right after `nar5520_wdt_init` | unpatched kernel — apply the `zd1200_kernel_elf` catalog rules |
| guest EIP sleds through zeros after boot | loader tail was overwritten — rebuild with `patch-kernel.py` |
| `segfault at 14` from dropbear* | TLS canary build — rebuild with hardening off |
| `Permission denied (publickey)` | passwd symlink / `/etc/shells` / key perms — see hint #6 |
| `Invalid option -v` | DEBUG_TRACE not compiled — use `-E` |
| port 2222 open but no SSH banner | dropbear died at start — check the S99 console output |
| nothing on 2222 at all | handoff copy failed — check S99 `ls` output on the console |
