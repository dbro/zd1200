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
            "analytics/ping-daily-publish.sh",
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
        self.assertIn("ping_enabled=0", handoff)
        self.assertIn("snapshot_enabled=0", handoff)
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
        self.assertIn('number=\\"6000\\"', helper)

    def test_ping_round_parses_clients_once_and_uses_bounded_raw_icmp(self):
        monitor = (ROOT / "analytics/zd1200-ping-monitor.c").read_text()
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        self.assertIn("parse_client_view(client_xml)", monitor)
        self.assertIn("PING_BATCH_SIZE 512", monitor)
        self.assertIn("SOCK_RAW,IPPROTO_ICMP", monitor)
        self.assertIn("BEGIN IMMEDIATE", monitor)
        self.assertNotIn("xml_has_client", monitor)
        self.assertNotIn('execl("/bin/ping"', monitor)
        self.assertIn("last_ping + ping_interval - after", handoff)
        self.assertIn('sleep "$next_delay"', handoff)

    def test_ap_targets_use_mac_identity_and_legacy_rows_do_not_block_export(self):
        monitor = (ROOT / "analytics/zd1200-ping-monitor.c").read_text()
        exporter = (ROOT / "analytics/zd1200-ping-export.c").read_text()
        self.assertIn('id=attr(tag,end,"mac")', monitor)
        self.assertIn('if(append<0)continue;', exporter)

    def test_snapshots_are_gzipped_and_published_as_lazy_daily_indexes(self):
        collector = (ROOT / "analytics/network-snapshot-collect.sh").read_text()
        monitor = (ROOT / "analytics/zd1200-ping-monitor.c").read_text()
        publisher = (ROOT / "analytics/snapshot-index-publish.sh").read_text()
        page = (ROOT / "analytics/ping-monitor.html").read_text()
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        self.assertIn('destination="$snapshot_dir/${now}-${kind}.xml.gz"', collector)
        self.assertIn('gzip -c "$temporary"', collector)
        self.assertIn("current-client.xml", collector)
        self.assertIn('strcmp(end,"-ap.xml.gz")', monitor)
        self.assertIn('complete_snapshot(t,".gz")', monitor)
        self.assertIn('snapshot-times', monitor)
        self.assertIn('int($1/86400)', publisher)
        self.assertIn('\\"snapshots\\":[', publisher)
        self.assertIn('zd1200-ping-monitor-snapshot-index/%s.json', publisher)
        self.assertIn('zd1200-ping-monitor-snapshot-manifest.json', handoff)
        self.assertNotIn('zd1200-ping-monitor-snapshot-index.json', page)
        self.assertIn('loadSnapshotRange(now-86400,now+1)', page)
        self.assertIn('Snapshot XML is loaded only when compared.', page)
        self.assertIn("DecompressionStream('gzip')", page)
        self.assertIn('`${time}-${type}.xml.gz`', page)

    def test_daily_browser_history_has_no_server_preaggregation(self):
        exporter = (ROOT / "analytics/zd1200-ping-export.c").read_text()
        publisher = (ROOT / "analytics/ping-daily-publish.sh").read_text()
        worker = (ROOT / "analytics/ping-monitor-worker.js").read_text()
        page = (ROOT / "analytics/ping-monitor.html").read_text()
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertIn('write_bytes("ZDPMDAY\\0",8)', exporter)
        self.assertIn("targets-json|manifest|export-day", exporter)
        self.assertIn('mode=backfill', publisher)
        self.assertIn('.backfill-v2', publisher)
        self.assertIn('file.revision||file.bytes||1', worker)
        self.assertIn('\\"revision\\":%lld', exporter)
        self.assertIn('zd1200-ping-daily-publish backfill', handoff)
        self.assertIn('merge_legacy_aps(db,aps)', (ROOT / "analytics/zd1200-ping-monitor.c").read_text())
        self.assertNotIn('"p50"', exporter)
        self.assertNotIn('"p99"', exporter)
        self.assertNotIn('"attempts"', exporter)
        self.assertIn('gzip -6 -c "$raw"', publisher)
        self.assertIn('ping-$previous.bin.gz', publisher)
        self.assertIn("DecompressionStream('gzip')", worker)
        self.assertIn("Math.min(3,job.files.length)", worker)
        self.assertIn("percentile(histograms", worker)
        self.assertIn("zd1200-ping-monitor-daily-manifest.json", handoff)
        self.assertIn("zd1200-ping-monitor-worker.js", handoff)
        self.assertIn("zd1200-ping-export.c", dockerfile)
        self.assertIn("zd1200-ping-monitor-targets.json", page)
        self.assertIn("zd1200-ping-monitor-daily-manifest.json", page)
        self.assertIn("new Worker('zd1200-ping-monitor-worker.js')", page)
        self.assertIn("type:'sparklines'", page)
        self.assertIn("async function sparklines(job)", worker)
        self.assertIn('data-spark=', page)
        self.assertIn('24h latency', page)
        self.assertNotIn("fetch('zd1200-ping-monitor-snapshot.json')", page)

    def test_scalable_table_and_diff_controls_are_present(self):
        page = (ROOT / "analytics/ping-monitor.html").read_text()
        for identifier in (
            "target-search",
            "page-size",
            "page-previous",
            "page-next",
            "diff-mode",
        ):
            self.assertIn(f'id="{identifier}"', page)
        self.assertIn("TablePageSize=25", page)
        self.assertIn("button[data-sort]", page)
        self.assertIn("t.name.toLowerCase().includes(query)", page)
        self.assertIn("t.mac.includes(query)", page)
        self.assertIn("position:sticky", page)
        self.assertIn("overflow:auto", page)
        self.assertIn("diffMode='branches'", page)
        self.assertIn("Changed branches", page)
        self.assertIn("Changed lines", page)
        self.assertIn("All XML", page)
        self.assertIn("changedBranchIndexes", page)
        self.assertIn("syncDiffPanes", page)
        self.assertNotIn('id="changed-only"', page)


if __name__ == "__main__":
    unittest.main()
