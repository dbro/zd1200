#!/usr/bin/env python3
"""Exercise the stock PID-1 reboot path through the patched QEMU reset."""

from __future__ import annotations

import os
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BOOT_MARKER = b"Linux version 2.6.32.24"
SHELL_MARKER = b"/bin/sh: can't access tty"
RESTART_MARKER = b"Restarting system."


def wait_for(proc: subprocess.Popen[bytes], output: bytearray, predicate, timeout: int) -> None:
    selector = selectors.DefaultSelector()
    assert proc.stdout is not None
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(output):
            return
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
        for key, _ in selector.select(timeout=1):
            chunk = os.read(key.fd, 65536)
            if chunk:
                output.extend(chunk)
    raise TimeoutError("timed out waiting for the expected guest restart state")


def main() -> int:
    qemu = shutil.which("qemu-system-i386")
    if qemu is None:
        raise SystemExit("qemu-system-i386 is required")

    kernel = REPO / "image" / "bzImage"
    vmlinux = REPO / "image" / "vmlinux"
    initrd = REPO / "image" / "bootinitramfs.gz"
    for required in (kernel, vmlinux, initrd):
        if not required.is_file():
            raise SystemExit(f"missing proprietary local fixture: {required}")

    scratch_root = REPO / "qemu-tmp"
    scratch_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="restart-smoke-", dir=scratch_root) as temporary:
        patched_kernel = Path(temporary) / "bzImage.patched"
        subprocess.run(
            [
                sys.executable,
                str(REPO / "patch_binary_artifact.py"),
                "--artifact",
                "zd1200_kernel_elf",
                "--in",
                str(kernel),
                "--out",
                str(patched_kernel),
                "--vmlinux",
                str(vmlinux),
            ],
            check=True,
        )

        accelerator = "kvm" if os.access("/dev/kvm", os.R_OK | os.W_OK) else "tcg"
        command = [
            qemu,
            "-name",
            "zd1200-restart-smoke",
            "-accel",
            accelerator,
            "-machine",
            "pc",
            "-cpu",
            "pentium3",
            "-m",
            "512",
            "-smp",
            "1",
            "-kernel",
            str(patched_kernel),
            "-initrd",
            str(initrd),
            "-append",
            "console=ttyS0,115200n8 rdinit=/sbin/init nohz=off",
            "-net",
            "none",
            "-nographic",
        ]
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = bytearray()
        try:
            wait_for(proc, output, lambda data: SHELL_MARKER in data, 90)
            assert proc.stdin is not None
            proc.stdin.write(b"/sbin/reboot\n")
            proc.stdin.flush()
            wait_for(
                proc,
                output,
                lambda data: RESTART_MARKER in data and data.count(BOOT_MARKER) >= 2,
                90,
            )
        except Exception:
            sys.stderr.buffer.write(output[-12000:])
            raise
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    print("PASS: stock PID 1 rebooted through machine_restart_qemu into a second boot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
