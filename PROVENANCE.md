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
the repository license unless a file says otherwise. The repository contains
no prebuilt diagnostic SSH or SFTP payload. In particular, the former custom
Dropbear/OpenSSH binaries were removed because their source and license record
were not available.

The runtime retains an inactive development hook for an operator-supplied,
separately built payload. It is not part of a generated bundle, must not be
treated as a supported feature, and must remain disabled unless a future
implementation supplies reproducible source/build instructions, license
notices, and an operator-owned public key.

## Credentials

No private key, password, or default `authorized_keys` file is tracked.
Generated controller state and local `.env` files can contain sensitive
information and must remain untracked.

## Licensed decryption source

`ruckus_tac_decrypt.py` is a small, dependency-free adaptation of the legacy
TAC archive routine in `ms264556/aioruckus` commit
`9bc44024601ed1798e096d99d192903fb5d16355`. Its BSD Zero Clause license and
attribution are retained in `THIRD_PARTY_NOTICES.md`. The implementation was
verified locally against paired encrypted/decrypted ZD1200 10.5.1.0.282 inputs.
This removes the previous licensing blocker for that specific legacy TAC
format; other encryption formats remain unsupported until independently
identified, licensed, and tested.
