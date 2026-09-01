import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PingMonitorSettingsTests(unittest.TestCase):
    def test_guest_shell_scripts_parse(self):
        for relative in (
            "analytics/ping-monitor-settings-sync.sh",
            "analytics/network-snapshot-collect.sh",
            "boot-initrd-handoff",
        ):
            subprocess.run(
                ["sh", "-n", str(ROOT / relative)],
                check=True,
                capture_output=True,
                text=True,
            )

    def test_embedded_javascript_parses(self):
        page = (ROOT / "analytics/ping-monitor.html").read_text()
        script = re.search(r"<script>\n(.*)\n</script>", page, re.DOTALL)
        self.assertIsNotNone(script)
        subprocess.run(
            ["node", "--check"],
            input=script.group(1),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_page_uses_native_zd_configuration_api(self):
        page = (ROOT / "analytics/ping-monitor.html").read_text()
        self.assertIn("/admin10/_conf.jsp", page)
        self.assertIn("X-CSRF-Token", page)
        self.assertIn("setpref", page)
        self.assertIn('GET_PREFERENCE="zd1200-ping-monitor"', page)
        self.assertIn("user-perf-zd1200-ping-monitor", page)
        self.assertIn("waitForAppliedSettings", page)
        self.assertIn("preference_updated_at", page)
        self.assertNotIn("full-name", page)
        for field in ("ping-interval", "snapshot-interval"):
            self.assertRegex(
                page,
                rf'id="{field}"[^>]*min="30"[^>]*max="3600"',
            )

    def test_runtime_installs_and_publishes_settings_bridge(self):
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        self.assertIn("zd1200-ping-monitor-settings-sync", handoff)
        self.assertIn("zd1200-ping-monitor-settings.json", handoff)
        self.assertIn("PING_ENABLED", handoff)
        self.assertIn("SNAPSHOT_ENABLED", handoff)
        self.assertIn("PREFERENCE_UPDATED_AT", handoff)
        self.assertIn("HAS_NATIVE_SETTINGS", handoff)
        self.assertNotIn("native_settings=/writable", handoff)
        self.assertIn('apply_native_settings "$native_values"', handoff)
        self.assertIn('"$configured_ping" -ge 30', handoff)
        self.assertIn('"$configured_ping" -le 3600', handoff)

    def test_sync_bridges_native_preference_journal_to_root_cache(self):
        sync = (ROOT / "analytics/ping-monitor-settings-sync.sh").read_text()
        self.assertIn("/writable/etc/airespider/ajax_config.log", sync)
        self.assertIn("<zd1200-ping-monitor ", sync)
        self.assertIn("settings-cache.conf", sync)
        self.assertNotIn("credentials.env", sync)
        self.assertNotIn("/admin10/_conf.jsp", sync)
        self.assertNotIn("curl", sync)
        self.assertNotIn("native-settings.conf", sync)
        self.assertNotIn("full-name", sync)

    def test_snapshot_collector_uses_only_vendor_local_socket_helper(self):
        collector = (ROOT / "analytics/network-snapshot-collect.sh").read_text()
        helper = (ROOT / "analytics/zd1200-local-getstat.c").read_text()
        self.assertIn("zd1200-local-getstat", collector)
        self.assertNotIn("curl", collector)
        self.assertNotIn("credentials", collector)
        self.assertIn('"/tmp/getstate.socket"', helper)
        self.assertIn('"/tmp/getstat_response"', helper)
        self.assertIn("ap|client|mesh", helper)


if __name__ == "__main__":
    unittest.main()
