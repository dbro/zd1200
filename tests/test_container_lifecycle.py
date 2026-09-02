import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ContainerLifecycleTests(unittest.TestCase):
    def test_lifecycle_shell_scripts_parse(self):
        for relative in (
            "run-zd1200-qemu.sh",
            "run-zd1200-web.sh",
            "zd-healthcheck.sh",
            "boot-initrd-handoff",
        ):
            subprocess.run(
                ["bash" if relative.startswith("run-") else "sh", "-n", str(ROOT / relative)],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_container_stop_requests_stock_guest_reboot(self):
        qemu = (ROOT / "run-zd1200-qemu.sh").read_text()
        launcher = (ROOT / "run-zd1200-web.sh").read_text()
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        compose = (ROOT / "docker-compose.yml").read_text()
        macvlan_compose = (ROOT / "docker-compose.macvlan.yml").read_text()

        self.assertIn("isa-serial,chardev=zdctl", qemu)
        self.assertIn("-serial stdio", qemu)
        self.assertIn("-monitor none", qemu)
        self.assertIn('s.sendall(b"reboot\\n")', launcher)
        self.assertIn("Restarting system\\.", launcher)
        self.assertIn("trap on_signal INT TERM", launcher)
        self.assertIn("exit 0", launcher)
        self.assertIn("ZD-CONTAINER-CONTROL: orderly reboot requested", handoff)
        self.assertIn("exec /sbin/reboot", handoff)
        self.assertIn("stop_grace_period: 2m", compose)
        self.assertIn("stop_grace_period: 2m", macvlan_compose)

    def test_hda4_repair_falls_back_and_fails_closed(self):
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        self.assertIn("e2fsck -p /dev/hda4", handoff)
        self.assertIn("e2fsck -f -y /dev/hda4", handoff)
        self.assertIn("refusing to mount it", handoff)

    def test_healthcheck_rejects_filesystem_fault_after_ready(self):
        script = ROOT / "zd-healthcheck.sh"
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = pathlib.Path(temporary)
            fake_pgrep = temporary_path / "pgrep"
            fake_pgrep.write_text("#!/bin/sh\nexit 0\n")
            fake_pgrep.chmod(0o755)
            serial_log = temporary_path / "serial.log"
            environment = os.environ.copy()
            environment["PATH"] = f"{temporary}:{environment['PATH']}"
            environment["ZD_SERIAL_LOG"] = str(serial_log)

            serial_log.write_text("ZD-HEALTH: guest web service ready\n")
            self.assertEqual(
                subprocess.run(["sh", script], env=environment).returncode,
                0,
            )

            serial_log.write_text(
                "ZD-HEALTH: guest web service ready\n"
                "EXT2-fs error (device hda4): damaged bitmap\n"
                "Remounting filesystem read-only\n"
            )
            self.assertNotEqual(
                subprocess.run(["sh", script], env=environment).returncode,
                0,
            )

            serial_log.write_text(
                "ZD-HEALTH: guest web service ready\n"
                "Remounting filesystem read-only\n"
                "ZD-HEALTH: guest web service ready\n"
            )
            self.assertEqual(
                subprocess.run(["sh", script], env=environment).returncode,
                0,
            )


if __name__ == "__main__":
    unittest.main()
