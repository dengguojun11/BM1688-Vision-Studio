"""Monitor tests use temporary files and never import LabelU or its database."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess
from unittest.mock import patch

from scripts.ops.watch_runtime_logs import (
    REPLACE_RETRY_DELAYS,
    MonitorLock,
    RuntimeMonitor,
    atomic_json,
    main,
    validate_url,
)


def line(message, *, level="ERROR", second="01", pid=None):
    process = f"pid={pid} " if pid is not None else ""
    return f"2026-10-21 19:00:{second}.001 | {level:<8} | {process}rid=abcd1234 uid=1 | labelu.example:run:7 - {message}\n"


class RuntimeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.state = self.root / "state"
        self.health = patch("scripts.ops.watch_runtime_logs.probe_health", return_value={"tcp_open": True, "http_status": 200})
        self.health.start()
        self.addCleanup(self.health.stop)
        self.listener = patch("scripts.ops.watch_runtime_logs.probe_listener", return_value={"pid": 74144, "pids": [74144]})
        self.listener.start()
        self.addCleanup(self.listener.stop)

    def monitor(self, **kwargs):
        return RuntimeMonitor(self.logs, self.state, **kwargs)

    def test_incremental_counts_ignore_traceback_and_duplicate_error_sink(self):
        payload = line("ai_tag_fail token=TOP_SECRET body={private}")
        payload += "2026-10-21 19:00:01.001 | ERROR    | Traceback (most recent call last):\n"
        payload += "2026-10-21 19:00:01.001 | ERROR    | httpx.UnsupportedProtocol: Request URL is missing an 'http://' protocol.\n"
        payload += "[http] GET /api/data?token=TOP_SECRET response rid=abcd1234 status=500 elapsed_ms=12 body={private}\n"
        (self.logs / "labelu.log").write_text(payload, encoding="utf-8")
        (self.logs / "error.log").write_text(payload, encoding="utf-8")
        monitor = self.monitor(from_start=True)
        result = monitor.tick()
        self.assertEqual(result["event_count"], 2)
        self.assertEqual(monitor.tick()["event_count"], 2)
        with (self.logs / "labelu.log").open("a", encoding="utf-8") as stream:
            stream.write(line("retry_scheduled", level="WARNING", second="02"))
        self.assertEqual(monitor.tick()["event_count"], 3)
        output = "".join(path.read_text(encoding="utf-8") for path in self.state.glob("*.json*"))
        for forbidden in ("TOP_SECRET", "{private}", "/api/data", "token="):
            self.assertNotIn(forbidden, output)

    def test_default_starts_at_eof_and_checkpoint_resumes(self):
        path = self.logs / "labelu.log"
        path.write_text(line("old error"), encoding="utf-8")
        self.assertEqual(self.monitor().tick()["event_count"], 0)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line("new error", second="02"))
        self.assertEqual(self.monitor().tick()["event_count"], 1)
        self.assertEqual(self.monitor().tick()["event_count"], 1)

    def test_atomic_json_retries_temporary_sharing_conflicts(self):
        destination = self.root / "snapshot.json"
        real_replace = os.replace
        attempts = []

        def replace(source, target):
            attempts.append(target)
            if len(attempts) < 3:
                raise PermissionError("reader holds file")
            return real_replace(source, target)

        with patch("scripts.ops.watch_runtime_logs.os.replace", side_effect=replace), patch("scripts.ops.watch_runtime_logs.time.sleep") as sleep:
            atomic_json(destination, {"count": 1})

        self.assertEqual(len(attempts), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], list(REPLACE_RETRY_DELAYS[:2]))
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")), {"count": 1})

    def test_atomic_json_does_not_retry_unrelated_file_errors(self):
        with patch("scripts.ops.watch_runtime_logs.os.replace", side_effect=FileNotFoundError), patch("scripts.ops.watch_runtime_logs.time.sleep") as sleep:
            with self.assertRaises(FileNotFoundError):
                atomic_json(self.root / "snapshot.json", {"count": 1})
        sleep.assert_not_called()

    def test_status_sharing_conflict_is_bounded_and_restart_has_no_duplicates(self):
        path = self.logs / "labelu.log"
        path.write_text(line("first", second="01"), encoding="utf-8")
        monitor = self.monitor(from_start=True)
        monitor.tick()
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line("second", second="02"))
        real_replace = os.replace

        def replace(source, target):
            if Path(target).name == "status.json":
                raise PermissionError("reader holds status")
            return real_replace(source, target)

        with patch("scripts.ops.watch_runtime_logs.os.replace", side_effect=replace), patch("scripts.ops.watch_runtime_logs.time.sleep") as sleep, patch("scripts.ops.watch_runtime_logs.sys.stderr"):
            result = monitor.tick()
        self.assertEqual(result["event_count"], 2)
        self.assertTrue(monitor.persistence_deferred)
        self.assertEqual(sleep.call_count, len(REPLACE_RETRY_DELAYS))
        self.assertEqual(json.loads((self.state / "state.json").read_text(encoding="utf-8"))["event_count"], 2)
        self.assertEqual(json.loads((self.state / "status.json").read_text(encoding="utf-8"))["event_count"], 1)
        self.assertEqual(self.monitor().tick()["event_count"], 2)
        alerts = [json.loads(row) for row in (self.state / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(alerts), 2)
        self.assertEqual(len({row["id"] for row in alerts}), 2)

    def test_failed_checkpoint_recovers_pending_offsets_and_counts_after_restart(self):
        path = self.logs / "labelu.log"
        path.write_text(line("first", second="01"), encoding="utf-8")
        monitor = self.monitor(from_start=True)
        monitor.tick()
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line("second", second="02"))
        real_replace = os.replace

        def replace(source, target):
            if Path(target).name == "state.json":
                raise PermissionError("reader holds checkpoint")
            return real_replace(source, target)

        with patch("scripts.ops.watch_runtime_logs.os.replace", side_effect=replace), patch("scripts.ops.watch_runtime_logs.time.sleep"), patch("scripts.ops.watch_runtime_logs.sys.stderr"):
            self.assertEqual(monitor.tick()["event_count"], 2)
        self.assertTrue(monitor.persistence_deferred)
        self.assertEqual(json.loads((self.state / "state.json").read_text(encoding="utf-8"))["event_count"], 1)
        self.assertEqual(json.loads((self.state / "status.json").read_text(encoding="utf-8"))["event_count"], 1)
        pending = json.loads(next(self.state.glob("state.json.*.tmp")).read_text(encoding="utf-8"))
        self.assertEqual(pending["event_count"], 2)
        self.assertEqual(pending["cursors"]["labelu.log"]["offset"], path.stat().st_size)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line("third", second="03"))
        result = self.monitor().tick()
        self.assertEqual(result["event_count"], 3)
        self.assertEqual(result["file_transitions"]["recovered_pending_checkpoint"], 1)
        alerts = [json.loads(row) for row in (self.state / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(alerts), 3)
        self.assertEqual(len({row["id"] for row in alerts}), 3)

    def test_blocked_alert_append_retries_failed_line_after_restart(self):
        path = self.logs / "labelu.log"
        first = line("first", second="01")
        path.write_text(first + line("second", second="02"), encoding="utf-8")
        real_open = Path.open
        alert_opens = []

        def open_file(filename, *args, **kwargs):
            if filename.name == "alerts.jsonl" and args and args[0] == "a":
                alert_opens.append(filename)
                if len(alert_opens) == 2:
                    raise PermissionError("reader holds alert")
            return real_open(filename, *args, **kwargs)

        with patch("pathlib.Path.open", new=open_file):
            result = self.monitor(from_start=True).tick()
        self.assertEqual(result["event_count"], 1)
        state = json.loads((self.state / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["cursors"]["labelu.log"]["offset"], len(path.read_bytes().splitlines(keepends=True)[0]))
        self.assertEqual(self.monitor().tick()["event_count"], 2)
        alerts = [json.loads(row) for row in (self.state / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(alerts), 2)
        self.assertEqual(len({row["id"] for row in alerts}), 2)

    def test_invalid_pending_checkpoint_falls_back_to_committed_state(self):
        (self.logs / "labelu.log").write_text(line("first"), encoding="utf-8")
        self.monitor(from_start=True).tick()
        (self.state / "state.json.999.tmp").write_text('{"incomplete":', encoding="utf-8")
        result = self.monitor().tick()
        self.assertEqual(result["event_count"], 1)
        self.assertEqual(result["file_transitions"]["invalid_checkpoint"], 1)

    def test_once_cli_returns_failure_if_status_cannot_be_published(self):
        (self.logs / "labelu.log").write_text(line("first"), encoding="utf-8")
        real_replace = os.replace

        def replace(source, target):
            if Path(target).name == "status.json":
                raise PermissionError("reader holds status")
            return real_replace(source, target)

        with patch("scripts.ops.watch_runtime_logs.os.replace", side_effect=replace), patch("scripts.ops.watch_runtime_logs.time.sleep"), patch("scripts.ops.watch_runtime_logs.sys.stderr"):
            self.assertEqual(main(["--once", "--from-start", "--log-dir", str(self.logs), "--state-dir", str(self.state)]), 1)

    def test_larger_rewrite_is_detected_by_anchor_not_only_size(self):
        path = self.logs / "labelu.log"
        path.write_text(line("original"), encoding="utf-8")
        monitor = self.monitor(from_start=True)
        monitor.tick()
        path.write_text(line("rewritten longer error message", second="02") + line("another", second="03"), encoding="utf-8")
        result = monitor.tick()
        self.assertEqual(result["event_count"], 3)
        self.assertEqual(result["file_transitions"]["truncated_or_rewritten"], 1)

    def test_rotation_drains_unread_old_file_and_reads_new_file(self):
        path = self.logs / "labelu.log"
        path.write_text(line("old"), encoding="utf-8")
        monitor = self.monitor(from_start=True)
        monitor.tick()
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line("unread before rename", second="02"))
        path.rename(self.logs / "labelu.2026-10-21.log")
        path.write_text(line("new file", second="03"), encoding="utf-8")
        result = monitor.tick()
        self.assertEqual(result["event_count"], 3)
        self.assertEqual(result["file_transitions"]["rotated"], 1)
        self.assertEqual(monitor.tick()["event_count"], 3)

    def test_partial_utf8_line_waits_for_newline(self):
        path = self.logs / "labelu.log"
        content = line("自动安装依赖失败").encode("utf-8")
        path.write_bytes(content[:-3])
        monitor = self.monitor(from_start=True)
        self.assertEqual(monitor.tick()["event_count"], 0)
        with path.open("ab") as stream:
            stream.write(content[-3:])
        result = monitor.tick()
        self.assertEqual(result["event_count"], 1)
        self.assertEqual(result["categories"], {"dependency_unavailable": 1})

    def test_missing_primary_uses_error_fallback_and_deduplicates_switch(self):
        content = line("same error")
        (self.logs / "error.log").write_text(content, encoding="utf-8")
        monitor = self.monitor(from_start=True)
        self.assertEqual(monitor.tick()["event_count"], 1)
        (self.logs / "labelu.log").write_text(content, encoding="utf-8")
        self.assertEqual(monitor.tick()["event_count"], 1)

    def test_os_lock_prevents_second_monitor_and_releases(self):
        path = self.state / "monitor.lock"
        with MonitorLock(path):
            with self.assertRaises(RuntimeError):
                with MonitorLock(path):
                    self.fail("second monitor acquired lock")
        with MonitorLock(path):
            pass

    def test_once_cli_writes_state_without_labelu_import_or_network(self):
        (self.logs / "labelu.log").write_text(line("timeout"), encoding="utf-8")
        self.assertEqual(main(["--once", "--from-start", "--log-dir", str(self.logs), "--state-dir", str(self.state)]), 0)
        result = json.loads((self.state / "status.json").read_text(encoding="utf-8"))
        self.assertEqual(result["categories"], {"timeout": 1})
        self.assertEqual(result["target_service_event_count"], 0)
        self.assertEqual(result["attribution_counts"], {"shared_log_process_unconfirmed": 1})

    def test_pid_records_keep_other_service_out_of_target_counts(self):
        content = line("timeout", pid=74144)
        content += line("retry_scheduled", level="WARNING", second="02", pid=24004)
        content += line("old shared failure", second="03")
        content += line("[http] GET /private?token=SECRET response rid=abcd1234 status=503 elapsed_ms=11 body=SECRET", second="04", pid=74144)
        (self.logs / "labelu.log").write_text(content, encoding="utf-8")
        monitor = self.monitor(from_start=True)
        result = monitor.tick()
        self.assertEqual(result["event_count"], 4)
        self.assertEqual(result["target_service_event_count"], 2)
        self.assertEqual(result["target_service_categories"], {"timeout": 1, "http_server_error": 1})
        self.assertEqual(result["attribution_counts"]["other_process"], 1)
        self.assertEqual(result["attribution_counts"]["shared_log_process_unconfirmed"], 1)
        self.assertNotIn("SECRET", (self.state / "alerts.jsonl").read_text(encoding="utf-8"))

    def test_listener_change_recomputes_current_target_and_retains_change_time(self):
        path = self.logs / "labelu.log"
        path.write_text(line("timeout", pid=74144), encoding="utf-8")
        monitor = self.monitor(from_start=True)
        monitor.tick()
        monitor.last_listener = -float("inf")
        with patch("scripts.ops.watch_runtime_logs.probe_listener", return_value={"pid": 99999, "pids": [99999]}):
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line("retry_scheduled", level="WARNING", second="02", pid=74144))
                stream.write(line("timeout", second="03", pid=99999))
            result = monitor.tick()
            self.assertEqual(result["target_service_event_count"], 1)
            self.assertEqual(result["target_service_categories"], {"timeout": 1})
            self.assertEqual(result["listener"]["previous_pid"], 74144)
            self.assertTrue(result["listener"]["changed_at"])
            monitor.last_listener = -float("inf")
            changed_at = result["listener"]["changed_at"]
            self.assertEqual(monitor.tick()["listener"]["changed_at"], changed_at)

    def test_missing_listener_never_guesses_pid_attribution(self):
        (self.logs / "labelu.log").write_text(line("timeout", pid=74144), encoding="utf-8")
        with patch("scripts.ops.watch_runtime_logs.probe_listener", return_value={"pid": None, "pids": [], "error_type": "TimeoutExpired"}):
            result = self.monitor(from_start=True).tick()
        self.assertEqual(result["target_service_event_count"], 0)
        self.assertEqual(result["attribution_counts"], {"listener_pid_unavailable": 1})

    def test_listener_probe_is_bounded_and_filters_pids(self):
        # Call the actual helper despite the normal per-test probe isolation.
        actual_probe = self.listener.temp_original
        completed = subprocess.CompletedProcess([], 0, stdout="[74144,74144]", stderr="")
        with patch("scripts.ops.watch_runtime_logs.sys.platform", "win32"), patch("scripts.ops.watch_runtime_logs.subprocess.run", return_value=completed) as run:
            result = actual_probe("http://127.0.0.1:8003/")
        self.assertEqual(result["pid"], 74144)
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertIn("-LocalPort 8003", run.call_args.args[0][-1])
        with patch("scripts.ops.watch_runtime_logs.sys.platform", "win32"), patch("scripts.ops.watch_runtime_logs.subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 5)):
            result = actual_probe("http://127.0.0.1:8003/")
        self.assertIsNone(result["pid"])
        self.assertEqual(result["error_type"], "TimeoutExpired")

    def test_listener_probe_uses_readonly_netstat_after_powershell_timeout(self):
        actual_probe = self.listener.temp_original
        completed = subprocess.CompletedProcess([], 0, stdout=(
            "  TCP    127.0.0.1:8003    0.0.0.0:0    LISTENING    74144\n"
            "  TCP    127.0.0.1:8004    0.0.0.0:0    LISTENING    24004\n"
            "  TCP    127.0.0.1:8003    127.0.0.1:1234    ESTABLISHED    99999\n"
        ), stderr="")
        with patch("scripts.ops.watch_runtime_logs.sys.platform", "win32"), patch(
            "scripts.ops.watch_runtime_logs.subprocess.run",
            side_effect=[subprocess.TimeoutExpired("powershell", 5), completed],
        ) as run:
            result = actual_probe("http://127.0.0.1:8003/")
        self.assertEqual(result["pid"], 74144)
        self.assertEqual(result["source"], "netstat")
        self.assertEqual(run.call_args.kwargs["timeout"], 2)

    def test_upstream_causes_are_classified_without_copying_messages(self):
        payloads = (
            "provider billing arrearage token=PRIVATE_A",
            "provider model_not_found response=PRIVATE_B",
            "provider status=429 body=PRIVATE_C",
            "provider cooldown: billing arrearage token=PRIVATE_D",
        )
        (self.logs / "labelu.log").write_text("".join(
            line(message, second=f"{index:02d}", pid=74144)
            for index, message in enumerate(payloads)
        ), encoding="utf-8")
        result = self.monitor(from_start=True).tick()
        expected = {category: 1 for category in (
            "upstream_billing_arrearage", "upstream_model_unavailable", "upstream_rate_limited", "upstream_cooldown",
        )}
        self.assertEqual(result["target_service_categories"], expected)
        self.assertNotIn("PRIVATE_", (self.state / "alerts.jsonl").read_text(encoding="utf-8"))

    def test_health_target_rejects_credentials_query_and_external_host(self):
        for value in ("http://example.com", "http://localhost/?token=secret", "http://a:b@localhost", "http://localhost/api"):
            with self.assertRaises(argparse.ArgumentTypeError):
                validate_url(value)
        self.assertEqual(validate_url("http://127.0.0.1:8003"), "http://127.0.0.1:8003/")


if __name__ == "__main__":
    unittest.main()
