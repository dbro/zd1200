# Source and binary provenance

This repository publishes glue code and documentation for building a local
ZoneDirector lab. It does not grant rights to Ruckus software, firmware,
archives, decrypted payloads, diagnostic bundles, or controller state.

## Intentionally excluded vendor materials

`.gitignore` excludes `image/`, original downloads, archives, VM disks, logs,
and generated runtime initramfs files. A user supplies the Ruckus download and
creates all derived artifacts locally. Do not add hashes, extracts, keys, or
sample payload bytes to a public issue unless their redistribution is allowed.

## Tracked executable inventory

All tracked executable shell/Python/assembly files are source files covered by
the repository license unless a file says otherwise. The following prebuilt
diagnostic binaries are exceptions:

| Path | SHA-256 | Claimed origin | Public-release status |
| --- | --- | --- | --- |
| `zd-dropbear2222/dropbear` | `cd57285acbca9d14f65d8774e3bc07414721a364707bbc61fd273f66c90bee21` | custom Dropbear build, contributor commit `20fc444` | blocked: source and license record absent |
| `zd-dropbear2222/dropbearconvert` | `9a5c0e6eb289821ed35152d0f2507868f88de738d4d277a7701e76ad6be088f0` | custom Dropbear build, contributor commit `20fc444` | blocked: source and license record absent |
| `zd-dropbear2222/dropbearkey` | `671c833f481fe96850725d7a38f75d836a8b357f5b5eb1f4561dd57f70db79cf` | custom Dropbear build, contributor commit `20fc444` | blocked: source and license record absent |
| `zd-dropbear2222/sftp-server` | `bc644f7425e08e32b4a304d35e1fcf64eec9a032931a21989d34b4d07d74ccf3` | custom OpenSSH SFTP server build, contributor commit `20fc444` | blocked: source and license record absent |

These files are not included by the Docker image and the diagnostic listener is
disabled unless an operator supplies a local public-key file. Before a public
release, either add reproducible source/build instructions and the applicable
licenses, or remove the payload and its injection path.

## Credentials

No private key, password, or default `authorized_keys` file is tracked. The
optional `zd-dropbear2222/authorized_keys` path is ignored and must contain an
operator-owned public key only. Generated controller state and local `.env`
files can contain sensitive information and must remain untracked.

## Licensed decryption source

`ruckus_tac_decrypt.py` is a small, dependency-free adaptation of the legacy
TAC archive routine in `ms264556/aioruckus` commit
`9bc44024601ed1798e096d99d192903fb5d16355`. Its BSD Zero Clause license and
attribution are retained in `THIRD_PARTY_NOTICES.md`. The implementation was
verified locally against paired encrypted/decrypted ZD1200 10.5.1.0.282 inputs.
This removes the previous licensing blocker for that specific legacy TAC
format; other encryption formats remain unsupported until independently
identified, licensed, and tested.
