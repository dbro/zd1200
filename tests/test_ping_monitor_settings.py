import gzip
import json
import os
import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PingMonitorSettingsTests(unittest.TestCase):
    def test_guest_shell_scripts_parse(self):
        for relative in (
            "analytics/ping-monitor-settings-sync.sh",
            "analytics/network-snapshot-collect.sh",
            "analytics/ping-daily-publish.sh",
            "analytics/snapshot-index-publish.sh",
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
        self.assertIn('id="monitoring-enabled"', page)
        self.assertIn('id="monitoring-interval"', page)
        self.assertNotIn('id="ping-enabled"', page)
        self.assertNotIn('id="snapshot-enabled"', page)
        self.assertIn('enabled=\"${values.enabled?1:0}\" interval=\"${values.interval}\"', page)
        self.assertIn("node.getAttribute('interval')", page)
        self.assertNotIn("full-name", page)
        self.assertRegex(page, r'id="monitoring-interval"[^>]*min="30"[^>]*max="3600"')

    def test_runtime_installs_and_publishes_settings_bridge(self):
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        self.assertIn("zd1200-ping-monitor-settings-sync", handoff)
        self.assertIn("zd1200-ping-monitor-settings.json", handoff)
        self.assertIn("MONITORING_ENABLED", handoff)
        self.assertNotIn("configured_ping_enabled", handoff)
        self.assertNotIn("configured_snapshot_enabled", handoff)
        self.assertIn("PREFERENCE_UPDATED_AT", handoff)
        self.assertIn("HAS_NATIVE_SETTINGS", handoff)
        self.assertNotIn("native_settings=/writable", handoff)
        self.assertIn('apply_native_settings "$native_values"', handoff)
        self.assertIn("monitoring_enabled=0", handoff)
        self.assertNotIn("ping_enabled=0", handoff)
        self.assertNotIn("snapshot_enabled=0", handoff)
        self.assertIn('"$configured_interval" -ge 30', handoff)
        self.assertIn('"$configured_interval" -le 3600', handoff)

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
        self.assertIn("ap|client|client-live|ap-detail|mesh", helper)
        self.assertIn('number=\\"6000\\"', helper)
        self.assertIn('<client/>', helper)
        self.assertIn('LEVEL=\\"2\\"', helper)
        self.assertIn('comp=\\"eventd\\"', helper)

    def test_radio_signal_and_events_use_compact_local_storage(self):
        monitor = (ROOT / "analytics/zd1200-ping-monitor.c").read_text()
        collector = (ROOT / "analytics/network-snapshot-collect.sh").read_text()
        handoff = (ROOT / "boot-initrd-handoff").read_text()

        for field in ("snr_db", "signal_dbm", "noise_floor_dbm"):
            self.assertIn(field, monitor)
        for table in ("ap_radio_sample", "mesh_link_sample", "zd_event"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", monitor)
        for field in (
            "airtime-total",
            "airtime-busy",
            "airtime-rx",
            "airtime-tx",
            "rf-samples",
        ):
            self.assertIn(field, monitor)
        self.assertIn("rf_samples", monitor)
        self.assertIn("INSERT OR IGNORE INTO zd_event", monitor)
        self.assertIn("event-watermark", monitor)
        self.assertIn("client-live", collector)
        self.assertIn("ap-detail", collector)
        self.assertIn("collect_events", collector)
        self.assertIn("telemetry-status-json", monitor)
        self.assertIn("zd1200-ping-monitor-telemetry-status.json", handoff)
        self.assertIn("zd1200-network-snapshot-collect live", handoff)
        self.assertNotIn("zd1200-network-snapshot-collect events", handoff)
        self.assertIn('tick "$current_client" "$current_ap_detail"', handoff)

    def test_ping_round_parses_clients_once_and_uses_bounded_raw_icmp(self):
        monitor = (ROOT / "analytics/zd1200-ping-monitor.c").read_text()
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        self.assertIn("parse_client_view(client_xml)", monitor)
        self.assertIn("PING_BATCH_SIZE 512", monitor)
        self.assertIn("SOCK_RAW,IPPROTO_ICMP", monitor)
        self.assertIn("BEGIN IMMEDIATE", monitor)
        self.assertNotIn("xml_has_client", monitor)
        self.assertNotIn('execl("/bin/ping"', monitor)
        self.assertIn("last_collection + monitoring_interval - after", handoff)
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
        self.assertIn("bundle_completed_days", publisher)
        self.assertIn("snapshot-$day.bundle.gz", publisher)
        self.assertIn("ZDSNAPSHOT1", publisher)
        self.assertIn("gzip -6 -c", publisher)
        self.assertIn('zd1200-ping-monitor-snapshot-manifest.json', handoff)
        self.assertNotIn('zd1200-ping-monitor-snapshot-index.json', page)
        self.assertIn('await loadSnapshotRange(resultStart,end)', page)
        self.assertIn('async function loadSnapshotRange', page)
        self.assertIn("DecompressionStream('gzip')", page)
        self.assertIn('${time}-${type}.xml.gz', page)
        self.assertIn("async function snapshotBundle", page)
        self.assertIn("ZDSNAPSHOT1", page)

    def test_completed_snapshot_day_is_one_cross_capture_gzip_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = pathlib.Path(temporary)
            snapshots = state / "snapshots"
            indexes = state / "snapshot-index"
            snapshots.mkdir()
            indexes.mkdir()
            timestamps = (1814313610, 1814313640)
            (indexes / "20999.timestamps").write_text(
                "".join(f"{value}\n" for value in timestamps)
            )
            expected = {}
            for timestamp in timestamps:
                for kind in ("ap", "client", "mesh"):
                    name = f"{timestamp}-{kind}.xml"
                    content = (
                        f'<ajax-response><{kind} stamp="{timestamp}">'
                        f'repeated structure</{kind}></ajax-response>\n'
                    ).encode()
                    expected[name] = content
                    with gzip.open(snapshots / f"{name}.gz", "wb", compresslevel=6) as stream:
                        stream.write(content)
            environment = os.environ.copy()
            environment.update(
                ZD_SNAPSHOT_STATE_DIR=str(state),
                ZD_BUSYBOX="/usr/bin/busybox",
            )
            subprocess.run(
                ["sh", str(ROOT / "analytics/snapshot-index-publish.sh"), "add", "1814400010"],
                check=True,
                env=environment,
            )
            bundle = snapshots / "snapshot-20999.bundle.gz"
            data = gzip.decompress(bundle.read_bytes())
            self.assertTrue(data.startswith(b"ZDSNAPSHOT1\n"))
            offset = len(b"ZDSNAPSHOT1\n")
            actual = {}
            while offset < len(data):
                newline = data.index(b"\n", offset)
                timestamp, kind, size = data[offset:newline].decode().split()
                start = newline + 1
                end = start + int(size)
                actual[f"{timestamp}-{kind}.xml"] = data[start:end]
                self.assertEqual(data[end], 10)
                offset = end + 1
            self.assertEqual(actual, expected)
            self.assertFalse(any(snapshots.glob("*-*.xml.gz")))
            index = json.loads((indexes / "20999.json").read_text())
            self.assertEqual(
                index["bundle"],
                "zd1200-ping-monitor-snapshots/snapshot-20999.bundle.gz",
            )

    def test_daily_browser_history_has_no_server_preaggregation(self):
        exporter = (ROOT / "analytics/zd1200-ping-export.c").read_text()
        publisher = (ROOT / "analytics/ping-daily-publish.sh").read_text()
        worker = (ROOT / "analytics/ping-monitor-worker.js").read_text()
        page = (ROOT / "analytics/ping-monitor.html").read_text()
        handoff = (ROOT / "boot-initrd-handoff").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()

        self.assertIn('write_bytes("ZDPMDAY\\0",8)', exporter)
        self.assertIn("#define FORMAT_VERSION 2U", exporter)
        self.assertIn("#define HEADER_SIZE 640U", exporter)
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
        self.assertIn("async function history(job)", worker)
        self.assertIn("latencySum", worker)
        self.assertIn("latencyMax", worker)
        self.assertIn("snrMin", worker)
        self.assertIn("airtimeSum", worker)
        self.assertIn("zd1200-ping-monitor-daily-manifest.json", handoff)
        self.assertIn("zd1200-network-monitor-worker.js", handoff)
        self.assertIn("zd1200-ping-export.c", dockerfile)
        self.assertIn("zd1200-ping-monitor-targets.json", page)
        self.assertIn("zd1200-ping-monitor-daily-manifest.json", page)
        self.assertIn("new Worker('zd1200-network-monitor-worker.js?v=4')", page)
        self.assertIn("type:'history'", page)
        self.assertIn('class="lanes"', page)
        self.assertIn('mean ${mean.toFixed(1)} ms, max ${max} ms', page)
        self.assertNotIn("fetch('zd1200-ping-monitor-snapshot.json')", page)

    def test_scalable_table_and_diff_controls_are_present(self):
        page = (ROOT / "analytics/ping-monitor.html").read_text()
        for identifier in (
            "search",
            "page-size",
            "page-previous",
            "page-next",
            "compare-panel",
            "target-a",
            "target-b",
            "build-prompt",
        ):
            self.assertIn(f'id="{identifier}"', page)
        self.assertIn("pageSize=25", page)
        self.assertIn("[data-sort]", page)
        self.assertIn("hay.includes(state.query.toLowerCase())", page)
        self.assertIn("position:sticky", page)
        self.assertIn("overflow:auto", page)
        self.assertIn("history.replaceState", page)
        self.assertIn("Group A", page)
        self.assertIn("Group B", page)
        self.assertIn("Download .txt", page)
        self.assertIn("Eventd data is not included", page)
        self.assertIn("Network Monitor - ZoneDirector", page)
        self.assertIn('id="info-details"', page)
        self.assertIn('id="gear-details"', page)
        self.assertIn('id="info-toggle"', page)
        self.assertIn('id="gear-toggle"', page)
        self.assertIn("if($('compare-panel').hidden)toggleExpanded", page)
        self.assertIn(".cell{cursor:pointer}", page)
        self.assertIn(".history.compare-mode .cell{cursor:crosshair}", page)
        self.assertIn("Attached to", page)
        self.assertIn("Path to wired", page)
        self.assertIn("Clients on this AP", page)
        self.assertIn("loadExpandedDetails", page)
        self.assertIn("associatedAt", page)
        self.assertIn("associatedAt", (ROOT / "analytics/ping-monitor-worker.js").read_text())
        self.assertIn("snapshotInBucket(start,end,observed||undefined)", page)
        self.assertIn("Math.max(0,40-snrValue)", page)
        self.assertIn("top:34px;bottom:auto", page)
        self.assertIn("Airtime: 0–100%", page)

        handoff = (ROOT / "boot-initrd-handoff").read_text()
        self.assertIn("zd1200-network-monitor.html", handoff)
        self.assertIn("#zd1200_network_monitor", handoff)
        self.assertIn('title:"Network Monitor"', handoff)


if __name__ == "__main__":
    unittest.main()
