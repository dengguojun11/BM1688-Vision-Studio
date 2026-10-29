"""Contract tests for SSH Board Agent bootstrap.

These tests exercise rendering and the worker state machine with an in-memory
registry/fake SSH client.  No network or systemd command is executed.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from labelu.internal.application.command.edge_agent_bootstrap import (
    BootstrapEdgeAgentCommand,
)
from labelu.internal.application.service import edge_agent_bootstrap as bootstrap


def _record() -> dict:
    return {
        "id": "edge_test_01",
        "name": "测试边端",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.54.169",
        "port": 22,
        "user": "linaro",
        # The production registry stores this encrypted; the service accepts
        # the direct value only for an ephemeral test adapter.
        "password": "ssh-secret",
        "edge_root": "/data/labelu_edge",
        "sophon_demo_root": "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
        "docker_container": "bm1688",
        "run_coco_eval": False,
        "connect_timeout_sec": 5,
        "validation_timeout_sec": 30,
        "environment_report": {},
        "board_agent_status": "unknown",
        "board_agent_environment_ready": False,
    }


def _settings(monkeypatch):
    monkeypatch.setattr(
        bootstrap.settings, "BOARD_AGENT_TOKEN", "unit-test-agent-token"
    )
    monkeypatch.setattr(
        bootstrap.settings,
        "BOARD_AGENT_PUBLIC_BASE_URL",
        "http://labelu.example.test:8003",
    )
    monkeypatch.setattr(bootstrap.settings, "MEDIA_HOST", "")
    monkeypatch.setattr(bootstrap.settings, "BOARD_AGENT_HEARTBEAT_TTL_SECONDS", 120)


def test_preflight_command_is_read_only_and_parseable():
    command = bootstrap.build_preflight_command()
    encoded = command.split()[2]
    script = base64.b64decode(encoded).decode("utf-8")
    assert command.endswith("| sh")
    assert "LABELU_AGENT_PREFLIGHT_V1=1" in script
    assert "rm " not in script
    assert "sudo -n true" in script
    report = bootstrap.parse_preflight_output("""LABELU_AGENT_PREFLIGHT_V1=1
python3_ok=true
systemd_ok=true
sha256_ok=true
privilege_ok=true
root_writable=true
systemd_runtime=true
""")
    assert report["python3_ok"] is True
    assert report["format"] == bootstrap.BOOTSTRAP_PROTOCOL_VERSION
    assert "command -v systemctl" in script
    assert "ps -p 1 -o comm=" in script


def test_preflight_rejects_systemctl_without_systemd_runtime():
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap.parse_preflight_output(
            """LABELU_AGENT_PREFLIGHT_V1=1
python3_ok=true
systemd_ok=true
systemd_runtime=false
sha256_ok=true
privilege_ok=true
root_writable=true
"""
        )

    assert exc_info.value.code == "preflight_failed"
    assert exc_info.value.detail["missing"] == ["systemd_runtime"]


def test_artifacts_are_versioned_and_secret_is_not_in_install_command(monkeypatch):
    _settings(monkeypatch)
    record = _record()
    command = BootstrapEdgeAgentCommand(
        device_id=record["id"], agent_version="release-2026.10", dry_run=True
    )
    artifacts = bootstrap.build_agent_bootstrap_artifacts(record, command)
    assert artifacts.version == "release-2026.10"
    assert re.fullmatch(r"[0-9a-f]{64}", artifacts.artifact_sha256)
    assert 'BOARD_AGENT_ID="edge_test_01"' in artifacts.environment
    assert 'LABELU_EDGE_ROOT="/data/labelu_edge"' in artifacts.environment
    assert "X-Board-Agent-Id" not in artifacts.systemd_unit
    assert "Restart=always" in artifacts.systemd_unit
    install = bootstrap.build_install_command(artifacts)
    assert install.endswith("| sh")
    decoded = base64.b64decode(install.split()[2]).decode("utf-8")
    assert "ssh-secret" not in decoded
    assert "unit-test-agent-token" not in decoded
    assert artifacts.remote_version_root.endswith("/versions/release-2026.10")
    assert "trap cleanup_staging 0 1 2 3 15" in decoded
    assert 'cleanup_staging() {' in decoded
    assert 'rm -rf -- "$STAGING" || true' in decoded


def test_upload_artifacts_use_explicit_private_modes(monkeypatch):
    _settings(monkeypatch)
    record = _record()
    artifacts = bootstrap.build_agent_bootstrap_artifacts(
        record,
        BootstrapEdgeAgentCommand(device_id=record["id"], dry_run=True),
    )
    uploaded = []

    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "sftp_upload",
        lambda _client, local, remote, *, mode=None, **_kwargs: uploaded.append(
            (Path(local).name, remote, mode, Path(local).stat().st_mode & 0o777)
        ),
    )

    bootstrap._write_and_upload_artifacts(object(), artifacts)

    assert {name: mode for name, _remote, mode, _local_mode in uploaded} == {
        bootstrap.AGENT_FILE_NAME: 0o700,
        "agent.env": 0o600,
        bootstrap.AGENT_SERVICE_NAME: 0o600,
        "manifest.json": 0o600,
    }


def test_artifacts_pass_custom_edge_root_to_agent_environment(monkeypatch):
    _settings(monkeypatch)
    record = {
        **_record(),
        "edge_root": "/srv/labelu-edge",
        "sophon_demo_root": (
            "/srv/labelu-edge/runtime/sophon-demo/sample/YOLOv8_plus_det"
        ),
    }

    artifacts = bootstrap.build_agent_bootstrap_artifacts(
        record,
        BootstrapEdgeAgentCommand(device_id=record["id"], dry_run=True),
    )

    assert artifacts.remote_root == "/srv/labelu-edge"
    assert 'LABELU_EDGE_ROOT="/srv/labelu-edge"' in artifacts.environment
    assert (
        'LABELU_EDGE_EXECUTION_STATE_DIR="/srv/labelu-edge/agent/state"'
        in artifacts.environment
    )
    assert "WorkingDirectory=/srv/labelu-edge/agent/current" in artifacts.systemd_unit
    assert (
        "EnvironmentFile=-/srv/labelu-edge/agent/current/agent.env"
        in artifacts.systemd_unit
    )


def test_validation_rejects_shell_syntax_and_missing_token(monkeypatch):
    record = _record()
    monkeypatch.setattr(bootstrap.settings, "BOARD_AGENT_TOKEN", "")
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap.validate_bootstrap_device({**record, "host": "10.0.0.1;id"})
    assert exc_info.value.code == "invalid_host"

    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap.validate_bootstrap_device(record)
    assert exc_info.value.code == "board_agent_token_missing"


@pytest.mark.parametrize("value", [".", ".."])
def test_device_id_rejects_remote_path_special_segments(value):
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap._validate_device_id(value)
    assert exc_info.value.code == "invalid_device_id"


def test_public_base_url_requires_a_remote_reachable_address(monkeypatch):
    _settings(monkeypatch)
    assert bootstrap._public_base_url() == "http://labelu.example.test:8003"

    monkeypatch.setattr(bootstrap.settings, "BOARD_AGENT_PUBLIC_BASE_URL", "")
    monkeypatch.setattr(bootstrap.settings, "MEDIA_HOST", "http://10.20.30.40:8003/")
    assert bootstrap._public_base_url() == "http://10.20.30.40:8003"

    monkeypatch.setattr(bootstrap.settings, "MEDIA_HOST", "")
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap._public_base_url()
    assert exc_info.value.code == "public_base_url_missing"
    assert "BOARD_AGENT_PUBLIC_BASE_URL" in str(exc_info.value)


@pytest.mark.parametrize(
    "value",
    [
        "http://localhost:8003",
        "http://LOCALHOST:8003",
        "http://127.0.0.2:8003",
        "http://0.0.0.0:8003",
        "http://[::1]:8003",
        "http://[::]:8003",
        "http://[::ffff:127.0.0.1]:8003",
    ],
)
def test_public_base_url_rejects_loopback_and_unspecified_hosts(value):
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap._normalize_base_url(value)
    assert exc_info.value.code == "public_base_url_unreachable"
    assert "边端可访问" in str(exc_info.value)


@pytest.mark.parametrize("value", ["http://example.test:abc", "http://[bad:8003"])
def test_public_base_url_wraps_parser_value_errors(value):
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap._normalize_base_url(value)
    assert exc_info.value.code == "public_base_url_unsafe"


def test_dry_run_does_not_open_ssh_and_persists_preview(monkeypatch):
    _settings(monkeypatch)
    record = _record()
    reports = {record["id"]: {}}

    def find(device_id):
        return record if device_id == record["id"] else None

    def update(device_id, **changes):
        record.update(changes)
        reports[device_id] = record["environment_report"]
        return record

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_connect",
        lambda **kwargs: pytest.fail("dry run must not connect SSH"),
    )
    result = bootstrap.bootstrap_edge_agent(
        BootstrapEdgeAgentCommand(device_id=record["id"], dry_run=True)
    )
    assert result.status == "PREVIEW"
    assert result.artifact_sha256
    assert record["agent_bootstrap"]["code"] == "dry_run"
    assert reports[record["id"]]["agent_bootstrap"]["code"] == "dry_run"


def test_background_dry_run_does_not_replace_active_bootstrap(monkeypatch):
    _settings(monkeypatch)
    record = {
        **_record(),
        "agent_bootstrap": {
            "status": bootstrap.STATUS_INSTALLING,
            "stage": "install",
            "code": "running",
        },
    }
    updates = []

    monkeypatch.setattr(
        bootstrap.registry_io,
        "find_device_record",
        lambda device_id: record if device_id == record["id"] else None,
    )
    monkeypatch.setattr(
        bootstrap.registry_io,
        "update_device_record",
        lambda _device_id, **changes: updates.append(changes),
    )
    monkeypatch.setattr(
        bootstrap,
        "bootstrap_edge_agent",
        lambda *_args, **_kwargs: pytest.fail(
            "an active bootstrap preview must not start or overwrite a worker"
        ),
    )

    result = bootstrap.start_bootstrap_background(
        BootstrapEdgeAgentCommand(device_id=record["id"], dry_run=True)
    )

    assert result.status == bootstrap.STATUS_INSTALLING
    assert result.stage == "install"
    assert updates == []


def test_direct_dry_run_does_not_replace_active_bootstrap(monkeypatch):
    """The synchronous preview entry point must honor an active lifecycle."""

    _settings(monkeypatch)
    record = {
        **_record(),
        "agent_bootstrap": {
            "status": bootstrap.STATUS_INSTALLING,
            "stage": "install",
            "code": "running",
        },
    }
    updates = []

    monkeypatch.setattr(
        bootstrap.registry_io,
        "find_device_record",
        lambda device_id: record if device_id == record["id"] else None,
    )
    monkeypatch.setattr(
        bootstrap.registry_io,
        "update_device_record",
        lambda _device_id, **changes: updates.append(changes),
    )

    result = bootstrap.bootstrap_edge_agent(
        BootstrapEdgeAgentCommand(device_id=record["id"], dry_run=True)
    )

    assert result.status == bootstrap.STATUS_INSTALLING
    assert result.stage == "install"
    assert updates == []


def test_fake_remote_install_waits_for_real_heartbeat(monkeypatch):
    _settings(monkeypatch)
    record = _record()
    records = {record["id"]: record}
    calls: list[tuple[str, str]] = []

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        records[device_id].update(changes)
        if changes.get("validation_channel") == "board_agent":
            # The real Agent posts immediately after systemd starts.  Mimic
            # that post after the channel transition so the test exercises the
            # post-install heartbeat baseline instead of a stale prior pulse.
            records[device_id].update(
                {
                    "board_agent_status": "online",
                    "board_agent_environment_ready": True,
                    "board_agent_last_heartbeat_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            )
        return records[device_id]

    class FakeClient:
        def close(self):
            calls.append(("close", ""))

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_connect",
        lambda **kwargs: calls.append(("connect", kwargs["host"])) or FakeClient(),
    )
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_run",
        lambda client, command, timeout=0: (
            calls.append(("run", command[:20])),
            (
                (
                    0,
                    "LABELU_AGENT_PREFLIGHT_V1=1\npython3_ok=true\nsystemd_ok=true\nsystemd_runtime=true\nsha256_ok=true\nprivilege_ok=true\n",
                    "",
                )
                if "base64" in command and "LABELU_AGENT_PREFLIGHT" not in command
                else (0, "LABELU_AGENT_INSTALL_V1=1\nsystemd_active=true\n", "")
            ),
        )[1],
    )
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "sftp_upload",
        lambda client, local, remote, **_kwargs: calls.append(("upload", remote)),
    )
    monkeypatch.setattr(
        bootstrap.edge_runtime_provision,
        "probe_edge_runtime",
        lambda *args, **kwargs: {"environment_ready": True},
    )

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    record.update(
        {
            "board_agent_status": "online",
            "board_agent_last_heartbeat_at": now,
            "board_agent_environment_ready": True,
        }
    )
    result = bootstrap.bootstrap_edge_agent(
        BootstrapEdgeAgentCommand(
            device_id=record["id"],
            confirm_install=True,
            heartbeat_timeout_sec=1,
            heartbeat_poll_interval_sec=0.2,
        ),
        sleep_fn=lambda _seconds: None,
    )
    assert result.status == bootstrap.STATUS_READY
    assert result.board_agent_status == "online"
    assert records[record["id"]]["validation_channel"] == "board_agent"
    assert any(kind == "upload" for kind, _ in calls)
    assert any(kind == "close" for kind, _ in calls)


def test_install_timeout_keeps_board_agent_offline_and_not_ready(monkeypatch):
    _settings(monkeypatch)
    record = _record()
    records = {record["id"]: record}

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        records[device_id].update(changes)
        return records[device_id]

    class FakeClient:
        def close(self):
            return None

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_connect",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_run",
        lambda _client, command, timeout=0: (
            (
                0,
                "LABELU_AGENT_PREFLIGHT_V1=1\npython3_ok=true\nsystemd_ok=true\nsystemd_runtime=true\nsha256_ok=true\nprivilege_ok=true\n",
                "",
            )
            if "base64" in command and "LABELU_AGENT_PREFLIGHT" not in command
            else (0, "LABELU_AGENT_INSTALL_V1=1\nsystemd_active=true\n", "")
        ),
    )
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "sftp_upload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        bootstrap.edge_runtime_provision,
        "probe_edge_runtime",
        lambda *_args, **_kwargs: {"environment_ready": True},
    )

    result = bootstrap.bootstrap_edge_agent(
        BootstrapEdgeAgentCommand(
            device_id=record["id"],
            confirm_install=True,
            heartbeat_timeout_sec=0,
        ),
        sleep_fn=lambda _seconds: None,
    )

    assert result.status == bootstrap.STATUS_FAILED
    assert result.code == "heartbeat_timeout"
    assert result.completed_at
    assert records[record["id"]]["validation_channel"] == "board_agent"
    assert records[record["id"]]["board_agent_status"] == "offline"
    assert records[record["id"]]["board_agent_environment_ready"] is False
    assert records[record["id"]]["environment_status"] == "failed"
    assert records[record["id"]]["environment_code"] == "heartbeat_timeout"
    assert records[record["id"]]["agent_bootstrap"]["status"] == bootstrap.STATUS_FAILED


def test_heartbeat_without_ready_environment_stays_online_but_unschedulable(
    monkeypatch,
):
    _settings(monkeypatch)
    record = _record()
    records = {record["id"]: record}

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        records[device_id].update(changes)
        if changes.get("validation_channel") == "board_agent":
            records[device_id].update(
                {
                    "board_agent_status": "online",
                    "board_agent_environment_ready": False,
                    "board_agent_last_heartbeat_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            )
        return records[device_id]

    class FakeClient:
        def close(self):
            return None

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_connect",
        lambda **_kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_run",
        lambda _client, command, timeout=0: (
            (
                0,
                "LABELU_AGENT_PREFLIGHT_V1=1\npython3_ok=true\nsystemd_ok=true\nsystemd_runtime=true\nsha256_ok=true\nprivilege_ok=true\n",
                "",
            )
            if "base64" in command and "LABELU_AGENT_PREFLIGHT" not in command
            else (0, "LABELU_AGENT_INSTALL_V1=1\nsystemd_active=true\n", "")
        ),
    )
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "sftp_upload",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        bootstrap.edge_runtime_provision,
        "probe_edge_runtime",
        lambda *_args, **_kwargs: {"environment_ready": True},
    )

    result = bootstrap.bootstrap_edge_agent(
        BootstrapEdgeAgentCommand(
            device_id=record["id"],
            confirm_install=True,
            heartbeat_timeout_sec=0,
        ),
        sleep_fn=lambda _seconds: None,
    )

    assert result.status == bootstrap.STATUS_FAILED
    assert result.code == "environment_not_ready"
    assert records[record["id"]]["board_agent_status"] == "online"
    assert records[record["id"]]["board_agent_environment_ready"] is False


def test_late_ready_heartbeat_closes_retryable_timeout_with_ssh_secret(
    monkeypatch,
):
    """A delayed first pulse must make the installed Agent schedulable."""

    record = {
        **_record(),
        "validation_channel": "board_agent",
        "agent_bootstrap": {
            "status": bootstrap.STATUS_FAILED,
            "stage": "heartbeat",
            "code": "heartbeat_timeout",
            "message": "Agent 已安装但尚未收到新鲜心跳；设备保持不可调度",
        },
        "environment_report": {},
    }
    updates = {}
    monkeypatch.setattr(
        bootstrap.registry_io, "find_device_record", lambda _device_id: record
    )

    def update(_device_id, **changes):
        updates.update(changes)
        record.update(changes)
        return record

    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)

    from labelu.internal.common import edge_device_registry as registry

    result = registry.record_board_agent_heartbeat(
        record["id"],
        environment_ready=True,
        capabilities={"bm1688": True},
    )

    assert result["board_agent_environment_ready"] is True
    assert updates["password_secret_action"] == "clear"
    assert updates["agent_bootstrap"]["status"] == bootstrap.STATUS_READY
    assert updates["environment_status"] == "ready"


def test_timeout_finalization_serializes_heartbeat_after_atomic_write(
    monkeypatch, tmp_path
):
    """A heartbeat racing timeout finalization cannot be overwritten.

    The timeout worker owns the registry mutation lock while it rechecks and
    publishes the combined environment/lifecycle snapshot.  The heartbeat
    thread therefore waits for that single write, then advances the retryable
    timeout marker to READY.  Two timeout writes would expose three updates
    and could leave the final state FAILED.
    """

    _settings(monkeypatch)
    monkeypatch.setattr(bootstrap.settings, "BASE_DATA_DIR", str(tmp_path))
    record = {
        **_record(),
        "validation_channel": "board_agent",
        "board_agent_status": "offline",
        "board_agent_last_heartbeat_at": None,
        "board_agent_environment_ready": False,
        "agent_bootstrap": {
            "status": bootstrap.STATUS_WAITING_HEARTBEAT,
            "stage": "heartbeat",
            "code": "waiting",
            "message": "等待心跳",
        },
        "environment_report": {},
    }
    records = {record["id"]: record}
    updates: list[dict] = []
    first_read = Event()
    release_read = Event()
    find_calls = 0

    def find(device_id):
        nonlocal find_calls
        find_calls += 1
        if find_calls == 1:
            first_read.set()
            assert release_read.wait(2), "timeout worker did not resume"
        return records.get(device_id)

    def update(device_id, **changes):
        updates.append(dict(changes))
        target = records[device_id]
        target.update(changes)
        if "environment_report" in changes:
            target["environment_report"] = dict(changes["environment_report"])
        if "agent_bootstrap" in changes:
            target["agent_bootstrap"] = dict(changes["agent_bootstrap"])
        return target

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    bootstrap._STATE_CACHE.pop(record["id"], None)

    worker_result: dict[str, object] = {}

    def run_timeout() -> None:
        try:
            worker_result["value"] = bootstrap._finalize_heartbeat_wait(
                record["id"],
                heartbeat_not_before=datetime.now(timezone.utc),
                common_detail={"source": "race-test"},
            )
        except BaseException as exc:  # pragma: no cover - assertion below
            worker_result["error"] = exc

    heartbeat_result: dict[str, object] = {}
    heartbeat_started = Event()

    def run_heartbeat() -> None:
        heartbeat_started.set()
        try:
            from labelu.internal.common import edge_device_registry as registry

            heartbeat_result["value"] = registry.record_board_agent_heartbeat(
                record["id"],
                environment_ready=True,
                capabilities={"bm1688": True},
            )
        except BaseException as exc:  # pragma: no cover - assertion below
            heartbeat_result["error"] = exc

    worker = Thread(target=run_timeout)
    worker.start()
    assert first_read.wait(2)

    heartbeat = Thread(target=run_heartbeat)
    heartbeat.start()
    assert heartbeat_started.wait(2)
    # Let the timeout worker finish its locked read and one combined write.
    release_read.set()
    worker.join(3)
    heartbeat.join(3)
    assert not worker.is_alive()
    assert not heartbeat.is_alive()
    assert "error" not in worker_result
    assert "error" not in heartbeat_result

    # One timeout write followed by the blocked heartbeat completion.  The
    # former implementation had two timeout writes and could finish FAILED.
    assert len(updates) == 2
    assert records[record["id"]]["agent_bootstrap"]["status"] == bootstrap.STATUS_READY
    assert records[record["id"]]["board_agent_environment_ready"] is True
    assert records[record["id"]]["environment_status"] == "ready"


def test_heartbeat_baseline_rejects_stale_pulse(monkeypatch):
    _settings(monkeypatch)
    baseline = datetime.now(timezone.utc).replace(microsecond=0)
    record = {
        "board_agent_status": "online",
        "board_agent_environment_ready": True,
        "board_agent_last_heartbeat_at": baseline.isoformat(),
    }
    assert not bootstrap._fresh_heartbeat(record, not_before=baseline)
    record["board_agent_last_heartbeat_at"] = baseline.replace(
        microsecond=1
    ).isoformat()
    assert bootstrap._fresh_heartbeat(record, not_before=baseline)


def test_heartbeat_rejects_future_timestamp(monkeypatch):
    _settings(monkeypatch)
    now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
    record = {
        "board_agent_status": "online",
        "board_agent_environment_ready": True,
        "board_agent_last_heartbeat_at": (now + timedelta(hours=1)).isoformat(),
    }

    assert bootstrap._fresh_heartbeat(record, now=now) is False


def test_process_bootstrap_lock_is_exclusive_and_recoverable(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap.settings, "BASE_DATA_DIR", str(tmp_path))

    first = bootstrap._acquire_bootstrap_process_lock("edge-process-lock")
    assert first is not None
    try:
        assert bootstrap._acquire_bootstrap_process_lock("edge-process-lock") is None
    finally:
        bootstrap._release_bootstrap_process_lock(first)

    recovered = bootstrap._acquire_bootstrap_process_lock("edge-process-lock")
    assert recovered is not None
    bootstrap._release_bootstrap_process_lock(recovered)


def test_empty_top_level_bootstrap_status_keeps_nested_snapshot():
    record = {
        "agent_bootstrap": {"status": ""},
        "environment_report": {
            "agent_bootstrap": {
                "status": bootstrap.STATUS_READY,
                "stage": "heartbeat",
            }
        },
    }

    assert bootstrap.persisted_bootstrap_status(record) == bootstrap.STATUS_READY


def test_direct_bootstrap_fails_closed_when_process_lease_is_unavailable(monkeypatch):
    _settings(monkeypatch)
    record = _record()
    calls: list[str] = []

    monkeypatch.setattr(
        bootstrap.registry_io,
        "find_device_record",
        lambda _device_id: record,
    )
    monkeypatch.setattr(
        bootstrap,
        "_acquire_bootstrap_process_lock",
        lambda _device_id: None,
    )
    monkeypatch.setattr(
        bootstrap,
        "_persist_state",
        lambda *_args, **_kwargs: calls.append("persist"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_quiesce_existing_board_agent",
        lambda *_args, **_kwargs: calls.append("quiesce"),
    )

    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap.bootstrap_edge_agent(
            BootstrapEdgeAgentCommand(
                device_id=record["id"],
                confirm_install=True,
                wait_for_heartbeat=False,
            )
        )

    assert exc_info.value.code == "bootstrap_in_progress"
    assert calls == []


def test_background_duplicate_does_not_overwrite_active_run(monkeypatch):
    """A second click must observe the active run instead of queueing over it."""

    _settings(monkeypatch)
    record = _record()
    record["id"] = "edge_duplicate_bootstrap"
    record["agent_bootstrap"] = {
        "status": bootstrap.STATUS_FAILED,
        "started_at": "2026-01-01T00:00:00+00:00",
        "completed_at": "2026-01-01T00:05:00+00:00",
    }
    record["environment_report"] = {"agent_bootstrap": dict(record["agent_bootstrap"])}
    records = {record["id"]: record}
    created: list[object] = []
    worker_calls: list[object] = []

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        target = records[device_id]
        for key, value in changes.items():
            target[key] = value
        return target

    class FakeThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            created.append(self)

    def fake_bootstrap(command, *, _held_lock=None, **_kwargs):
        worker_calls.append(command)
        assert _held_lock is not None

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(bootstrap.threading, "Thread", FakeThread)
    monkeypatch.setattr(bootstrap, "bootstrap_edge_agent", fake_bootstrap)

    try:
        first = bootstrap.start_bootstrap_background(
            BootstrapEdgeAgentCommand(
                device_id=record["id"],
                agent_version="release-first",
                confirm_install=True,
            )
        )
        started_at = first.started_at
        second = bootstrap.start_bootstrap_background(
            BootstrapEdgeAgentCommand(
                device_id=record["id"],
                agent_version="release-second",
                confirm_install=True,
            )
        )

        assert len(created) == 1
        assert second.status == bootstrap.STATUS_QUEUED
        assert second.agent_version == "release-first"
        assert started_at is None
        assert second.started_at is None
        assert second.completed_at is None
        assert records[record["id"]]["agent_bootstrap"]["started_at"] is None
        assert records[record["id"]]["agent_bootstrap"]["completed_at"] is None
        assert worker_calls == []

        # Run the captured worker to prove the lock is released on completion.
        created[0].target()
        assert len(worker_calls) == 1
        assert bootstrap._device_lock(record["id"]).acquire(blocking=False)
        bootstrap._device_lock(record["id"]).release()
    finally:
        lock = bootstrap._device_lock(record["id"])
        if lock.locked():
            lock.release()
        bootstrap._STATE_CACHE.pop(record["id"], None)
        bootstrap._BOOTSTRAP_LOCKS.pop(record["id"], None)


def test_background_reloads_device_config_inside_mutation_lock(monkeypatch):
    """The worker marker must describe the record seen after lock acquisition."""

    from contextlib import contextmanager

    _settings(monkeypatch)
    initial = _record()
    initial["id"] = "edge_reload_under_lock"
    latest = {
        **initial,
        "host": "192.168.54.170",
        "validation_channel": "board_agent",
        "agent_bootstrap": {},
    }
    records = {latest["id"]: latest}
    find_calls = 0
    built: list[tuple[str, bool]] = []
    persisted: list[dict] = []
    threads: list[object] = []

    def find(_device_id):
        nonlocal find_calls
        find_calls += 1
        # The first read is intentionally stale; all reads after the lock see
        # the channel/host change made by an operator.
        return initial if find_calls == 1 else records[_device_id]

    def update(device_id, **changes):
        records[device_id].update(changes)
        persisted.append(dict(changes))
        return records[device_id]

    def build(record, _command, *, allow_board_agent_channel=False):
        built.append((str(record["host"]), bool(allow_board_agent_channel)))
        return SimpleNamespace(
            version="release-reloaded",
            artifact_sha256="a" * 64,
            remote_staging_root="/tmp/labelu-agent",
            remote_version_root="/data/labelu_edge/agent/versions/release-reloaded",
            remote_root="/data/labelu_edge",
        )

    class FakeThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target
            threads.append(self)

        def start(self):
            return None

    @contextmanager
    def available_lock():
        yield True

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(bootstrap, "build_agent_bootstrap_artifacts", build)
    monkeypatch.setattr(bootstrap, "_assert_device_not_busy", lambda _id: None)
    monkeypatch.setattr(bootstrap, "_quiesce_existing_board_agent", lambda _id: None)
    monkeypatch.setattr(bootstrap.registry_io, "device_mutation_lock", available_lock)
    monkeypatch.setattr(
        bootstrap, "_acquire_bootstrap_process_lock", lambda _id: object()
    )
    monkeypatch.setattr(
        bootstrap, "_release_bootstrap_process_lock", lambda _lock: None
    )
    monkeypatch.setattr(bootstrap.threading, "Thread", FakeThread)

    try:
        result = bootstrap.start_bootstrap_background(
            BootstrapEdgeAgentCommand(
                device_id=initial["id"],
                confirm_install=True,
                wait_for_heartbeat=False,
            )
        )
        assert result.status == bootstrap.STATUS_QUEUED
        assert len(threads) == 1
        assert built[0] == ("192.168.54.169", False)
        assert built[-1] == ("192.168.54.170", True)
        assert persisted[-1]["agent_bootstrap"]["artifact_sha256"] == "a" * 64
    finally:
        lock = bootstrap._device_lock(initial["id"])
        if lock.locked():
            lock.release()
        bootstrap._STATE_CACHE.pop(initial["id"], None)
        bootstrap._BOOTSTRAP_LOCKS.pop(initial["id"], None)


def test_background_rebootstrap_allows_ready_board_agent_channel(monkeypatch, tmp_path):
    """An explicit reinstall may reconnect through an existing pull channel."""

    _settings(monkeypatch)
    monkeypatch.setattr(bootstrap.settings, "BASE_DATA_DIR", str(tmp_path))
    record = _record()
    record.update(
        {
            "validation_channel": "board_agent",
            "board_agent_status": "online",
            "board_agent_environment_ready": True,
            "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
            "agent_bootstrap": {"status": bootstrap.STATUS_READY},
        }
    )
    records = {record["id"]: record}
    created: list[object] = []
    worker_kwargs: list[dict] = []

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        records[device_id].update(changes)
        return records[device_id]

    class FakeThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            created.append(self)

    def fake_bootstrap(command, **kwargs):
        worker_kwargs.append(kwargs)

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(bootstrap.threading, "Thread", FakeThread)
    monkeypatch.setattr(bootstrap, "bootstrap_edge_agent", fake_bootstrap)

    try:
        result = bootstrap.start_bootstrap_background(
            BootstrapEdgeAgentCommand(
                device_id=record["id"],
                agent_version="release-reinstall",
                confirm_install=True,
            )
        )

        assert result.status == bootstrap.STATUS_QUEUED
        assert len(created) == 1
        assert record["validation_channel"] == "board_agent"
        assert record["board_agent_status"] == "offline"

        created[0].target()
        assert worker_kwargs[0]["_allow_board_agent_channel"] is True
    finally:
        lock = bootstrap._device_lock(record["id"])
        if lock.locked():
            lock.release()
        bootstrap._STATE_CACHE.pop(record["id"], None)
        bootstrap._BOOTSTRAP_LOCKS.pop(record["id"], None)


@pytest.mark.parametrize("failure_phase", ["validate", "build"])
def test_worker_preamble_failure_persists_failed_state(monkeypatch, failure_phase):
    """A worker must not leave its already-queued record permanently queued."""

    _settings(monkeypatch)
    record = _record()
    record["id"] = f"edge_worker_{failure_phase}"
    records = {record["id"]: record}
    created: list[object] = []

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        target = records[device_id]
        target.update(changes)
        return target

    class FakeThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            created.append(self)

    def assert_bootstrap_marker(device_id):
        # The scheduler must be able to observe the durable marker before the
        # worker is allowed to quiesce the remote Agent.
        assert (
            records[device_id]["agent_bootstrap"]["status"] == bootstrap.STATUS_QUEUED
        )

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(bootstrap, "_assert_device_not_busy", lambda _id: None)
    monkeypatch.setattr(
        bootstrap, "_quiesce_existing_board_agent", assert_bootstrap_marker
    )
    monkeypatch.setattr(bootstrap.threading, "Thread", FakeThread)

    first = bootstrap.start_bootstrap_background(
        BootstrapEdgeAgentCommand(
            device_id=record["id"],
            agent_version="release-worker-preamble",
            confirm_install=True,
        )
    )
    assert first.status == bootstrap.STATUS_QUEUED
    assert len(created) == 1

    if failure_phase == "validate":
        monkeypatch.setattr(
            bootstrap,
            "validate_bootstrap_device",
            lambda _record: (_ for _ in ()).throw(
                bootstrap.EdgeAgentBootstrapError(
                    "worker validation failed", code="worker_validation_failed"
                )
            ),
        )
    else:
        monkeypatch.setattr(
            bootstrap,
            "build_agent_bootstrap_artifacts",
            lambda _record, _command: (_ for _ in ()).throw(
                bootstrap.EdgeAgentBootstrapError(
                    "worker artifact build failed", code="worker_build_failed"
                )
            ),
        )

    try:
        created[0].target()
        state = records[record["id"]]["agent_bootstrap"]
        assert state["status"] == bootstrap.STATUS_FAILED
        assert state["stage"] == "validation"
        expected_code = (
            "worker_validation_failed"
            if failure_phase == "validate"
            else "worker_build_failed"
        )
        assert state["code"] == expected_code
        assert records[record["id"]]["environment_report"]["agent_bootstrap"] == state
    finally:
        lock = bootstrap._device_lock(record["id"])
        if lock.locked():
            lock.release()
        bootstrap._STATE_CACHE.pop(record["id"], None)
        bootstrap._BOOTSTRAP_LOCKS.pop(record["id"], None)


def test_background_artifact_validation_failure_persists_failed_state(monkeypatch):
    """Synchronous artifact validation must not leave a device at NOT_STARTED."""

    _settings(monkeypatch)
    record = _record()
    record["id"] = "edge_sync_artifact_failure"
    records = {record["id"]: record}

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        target = records[device_id]
        target.update(changes)
        return target

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        bootstrap,
        "build_agent_bootstrap_artifacts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bootstrap.EdgeAgentBootstrapError(
                "边端 Agent 清单校验失败", code="agent_artifact_invalid"
            )
        ),
    )

    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap.start_bootstrap_background(
            BootstrapEdgeAgentCommand(
                device_id=record["id"],
                confirm_install=True,
            )
        )

    assert exc_info.value.code == "agent_artifact_invalid"
    state = records[record["id"]]["agent_bootstrap"]
    assert state["status"] == bootstrap.STATUS_FAILED
    assert state["stage"] == "validation"
    assert state["code"] == "agent_artifact_invalid"
    assert records[record["id"]]["environment_report"]["agent_bootstrap"] == state


def test_sync_bootstrap_rejects_processing_device_before_state_change(monkeypatch):
    _settings(monkeypatch)
    record = _record()
    calls: list[str] = []

    monkeypatch.setattr(bootstrap, "_active_processing_job_id", lambda _id: 77)
    monkeypatch.setattr(
        bootstrap,
        "_persist_state",
        lambda *_args, **_kwargs: calls.append("persist"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_quiesce_existing_board_agent",
        lambda *_args, **_kwargs: calls.append("quiesce"),
    )
    monkeypatch.setattr(
        bootstrap.registry_io,
        "find_device_record",
        lambda _device_id: record,
    )

    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap.bootstrap_edge_agent(
            BootstrapEdgeAgentCommand(
                device_id=record["id"],
                confirm_install=True,
                wait_for_heartbeat=False,
            )
        )

    assert exc_info.value.code == "device_busy"
    assert exc_info.value.detail == {"job_id": 77}
    assert "#77" in str(exc_info.value)
    assert calls == []


def test_held_lock_bootstrap_rechecks_processing_device_before_quiesce(monkeypatch):
    """A worker-held lock must not bypass the late PROCESSING guard."""

    _settings(monkeypatch)
    record = _record()
    record["id"] = "edge_busy_held_lock"
    calls: list[str] = []
    lock = bootstrap._device_lock(record["id"])
    assert lock.acquire(blocking=False)

    monkeypatch.setattr(bootstrap, "_active_processing_job_id", lambda _id: 99)
    monkeypatch.setattr(
        bootstrap,
        "_persist_state",
        lambda *_args, **_kwargs: calls.append("persist"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_quiesce_existing_board_agent",
        lambda *_args, **_kwargs: calls.append("quiesce"),
    )
    monkeypatch.setattr(
        bootstrap.registry_io,
        "find_device_record",
        lambda _device_id: record,
    )

    try:
        with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
            bootstrap.bootstrap_edge_agent(
                BootstrapEdgeAgentCommand(
                    device_id=record["id"],
                    confirm_install=True,
                    wait_for_heartbeat=False,
                ),
                _held_lock=lock,
            )

        assert exc_info.value.code == "device_busy"
        assert exc_info.value.detail == {"job_id": 99}
        assert "#99" in str(exc_info.value)
        assert calls == []
    finally:
        if lock.locked():
            lock.release()
        bootstrap._BOOTSTRAP_LOCKS.pop(record["id"], None)


def test_background_bootstrap_rejects_processing_device_before_quiesce(monkeypatch):
    _settings(monkeypatch)
    record = _record()
    record["id"] = "edge_busy_background"
    calls: list[str] = []

    monkeypatch.setattr(bootstrap, "_active_processing_job_id", lambda _id: 88)
    monkeypatch.setattr(
        bootstrap,
        "_persist_state",
        lambda *_args, **_kwargs: calls.append("persist"),
    )
    monkeypatch.setattr(
        bootstrap,
        "_quiesce_existing_board_agent",
        lambda *_args, **_kwargs: calls.append("quiesce"),
    )
    monkeypatch.setattr(
        bootstrap.registry_io,
        "find_device_record",
        lambda _device_id: record,
    )

    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap.start_bootstrap_background(
            BootstrapEdgeAgentCommand(
                device_id=record["id"],
                confirm_install=True,
            )
        )

    assert exc_info.value.code == "device_busy"
    assert exc_info.value.detail == {"job_id": 88}
    assert calls == []
    lock = bootstrap._device_lock(record["id"])
    assert lock.acquire(blocking=False)
    lock.release()
    bootstrap._BOOTSTRAP_LOCKS.pop(record["id"], None)


def test_rebootstrap_quiesces_existing_board_agent(monkeypatch):
    """An installed pull agent is removed from the scheduler before reinstall."""

    record = _record()
    record.update(
        {
            "validation_channel": "board_agent",
            "board_agent_status": "online",
            "board_agent_environment_ready": True,
            "board_agent_last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    records = {record["id"]: record}

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        records[device_id].update(changes)
        return records[device_id]

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)

    bootstrap._quiesce_existing_board_agent(record["id"])

    assert record["validation_channel"] == "board_agent"
    assert record["board_agent_status"] == "offline"
    assert record["board_agent_environment_ready"] is False
    assert record["environment_code"] == "agent_bootstrap_in_progress"
    assert record["environment_status"] == "checking"


def test_restart_recovery_finishes_waiting_heartbeat_without_reinstall(monkeypatch):
    """A post-install heartbeat should close a run after process restart."""

    _settings(monkeypatch)
    heartbeat_at = datetime.now(timezone.utc).isoformat()
    record = {
        **_record(),
        "id": "edge_restart_ready",
        "validation_channel": "board_agent",
        "password": "",
        "password_encrypted": "",
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": heartbeat_at,
        "board_agent_environment_ready": True,
        "agent_bootstrap": {
            "status": bootstrap.STATUS_WAITING_HEARTBEAT,
            "stage": "heartbeat",
            "code": "waiting",
        },
        "environment_report": {},
    }
    records = {record["id"]: record}

    def find(device_id):
        return records.get(device_id)

    def update(device_id, **changes):
        records[device_id].update(changes)
        return records[device_id]

    monkeypatch.setattr(bootstrap.registry_io, "find_device_record", find)
    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        bootstrap,
        "start_bootstrap_background",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fresh heartbeat must not reinstall over SSH")
        ),
    )

    result = bootstrap.resume_persisted_bootstrap(record["id"])

    assert result.status == bootstrap.STATUS_READY
    assert records[record["id"]]["agent_bootstrap"]["status"] == bootstrap.STATUS_READY


def test_restart_recovery_with_removed_ssh_secret_waits_for_agent_heartbeat(
    monkeypatch,
):
    """A board-agent record without SSH credentials must remain pull-only."""

    _settings(monkeypatch)
    record = {
        **_record(),
        "id": "edge_restart_waiting",
        "validation_channel": "board_agent",
        "password": "",
        "password_encrypted": "",
        "board_agent_status": "offline",
        "board_agent_environment_ready": False,
        "agent_bootstrap": {
            "status": bootstrap.STATUS_WAITING_HEARTBEAT,
            "stage": "heartbeat",
            "code": "waiting",
        },
        "environment_report": {},
    }
    records = {record["id"]: record}

    monkeypatch.setattr(
        bootstrap.registry_io,
        "find_device_record",
        lambda device_id: records.get(device_id),
    )

    def update(device_id, **changes):
        records[device_id].update(changes)
        return records[device_id]

    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        bootstrap,
        "start_bootstrap_background",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("missing SSH secret must not trigger reinstall")
        ),
    )

    result = bootstrap.resume_persisted_bootstrap(record["id"])

    assert result.status == bootstrap.STATUS_WAITING_HEARTBEAT
    assert result.code == "waiting_heartbeat"
    assert (
        records[record["id"]]["agent_bootstrap"]["status"]
        == bootstrap.STATUS_WAITING_HEARTBEAT
    )


def test_restart_recovery_skips_device_with_processing_job(monkeypatch):
    record = {
        **_record(),
        "id": "edge_restart_busy",
        "agent_bootstrap": {"status": bootstrap.STATUS_QUEUED},
    }
    monkeypatch.setattr(bootstrap.registry_io, "list_device_records", lambda: [record])
    monkeypatch.setattr(bootstrap, "_active_processing_job_id", lambda _device_id: 901)
    monkeypatch.setattr(
        bootstrap,
        "resume_persisted_bootstrap",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("busy device must stay untouched")
        ),
    )

    assert bootstrap.resume_persisted_bootstraps() == 0
    assert record["agent_bootstrap"]["status"] == bootstrap.STATUS_QUEUED


def test_state_response_prefers_newer_durable_snapshot_over_stale_cache(monkeypatch):
    device_id = "edge_state_merge"
    record = {
        **_record(),
        "id": device_id,
        "agent_bootstrap": {
            "status": bootstrap.STATUS_READY,
            "stage": "heartbeat",
            "updated_at": "2026-10-25T12:00:02+00:00",
        },
    }
    monkeypatch.setattr(
        bootstrap.registry_io, "find_device_record", lambda _device_id: record
    )
    bootstrap._STATE_CACHE[device_id] = {
        "device_id": device_id,
        "status": bootstrap.STATUS_WAITING_HEARTBEAT,
        "stage": "heartbeat",
        "updated_at": "2026-10-25T12:00:01+00:00",
    }
    try:
        result = bootstrap._state_response(device_id)
        assert result.status == bootstrap.STATUS_READY
        assert bootstrap._STATE_CACHE[device_id]["status"] == bootstrap.STATUS_READY
    finally:
        bootstrap._STATE_CACHE.pop(device_id, None)


def test_state_response_treats_legacy_durable_snapshot_as_authoritative(
    monkeypatch,
):
    device_id = "edge_legacy_state_merge"
    record = {
        **_record(),
        "id": device_id,
        "agent_bootstrap": {"status": bootstrap.STATUS_READY},
    }
    monkeypatch.setattr(
        bootstrap.registry_io, "find_device_record", lambda _device_id: record
    )
    bootstrap._STATE_CACHE[device_id] = {
        "device_id": device_id,
        "status": bootstrap.STATUS_WAITING_HEARTBEAT,
        "updated_at": "2026-10-25T12:00:01+00:00",
    }
    try:
        result = bootstrap._state_response(device_id)
        assert result.status == bootstrap.STATUS_READY
    finally:
        bootstrap._STATE_CACHE.pop(device_id, None)


def test_bootstrap_mutation_guard_preserves_body_errors(monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def available_lock():
        yield True

    monkeypatch.setattr(bootstrap.registry_io, "device_mutation_lock", available_lock)
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        with bootstrap._device_mutation_guard("edge_guard"):
            raise bootstrap.EdgeAgentBootstrapError(
                "任务占用", code="device_busy", detail={"job_id": 12}
            )
    assert exc_info.value.code == "device_busy"
    assert exc_info.value.detail == {"job_id": 12}


def test_bootstrap_mutation_guard_fails_closed_when_registry_lock_unavailable(
    monkeypatch,
):
    from contextlib import contextmanager

    @contextmanager
    def unavailable_lock():
        yield False

    monkeypatch.setattr(bootstrap.registry_io, "device_mutation_lock", unavailable_lock)
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        with bootstrap._device_mutation_guard("edge_guard_unavailable"):
            pass
    assert exc_info.value.code == "device_mutation_lock_unavailable"


@pytest.mark.parametrize(
    ("helper_name", "record_overrides", "kwargs"),
    [
        (
            "_activate_board_agent_channel",
            {},
            {},
        ),
        (
            "_quiesce_existing_board_agent",
            {"validation_channel": "board_agent"},
            {},
        ),
        (
            "_persist_agent_environment_state",
            {},
            {"ready": False, "code": "waiting_heartbeat", "message": "等待心跳"},
        ),
    ],
)
def test_bootstrap_registry_state_helpers_map_lock_contention(
    monkeypatch, helper_name, record_overrides, kwargs
):
    record = {**_record(), **record_overrides}
    lock_error = bootstrap.registry_io.DeviceMutationLockUnavailableError("busy")

    monkeypatch.setattr(
        bootstrap.registry_io, "find_device_record", lambda _device_id: record
    )
    monkeypatch.setattr(
        bootstrap.registry_io,
        "update_device_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(lock_error),
    )

    helper = getattr(bootstrap, helper_name)
    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        helper(record["id"], **kwargs)

    assert exc_info.value.code == "device_mutation_lock_unavailable"


def test_durable_bootstrap_state_lock_contention_is_retryable(monkeypatch):
    from contextlib import contextmanager

    record = _record()
    lock_error = bootstrap.registry_io.DeviceMutationLockUnavailableError("busy")

    @contextmanager
    def available_lock():
        yield True

    monkeypatch.setattr(bootstrap.registry_io, "device_mutation_lock", available_lock)
    monkeypatch.setattr(
        bootstrap.registry_io, "find_device_record", lambda _device_id: record
    )
    monkeypatch.setattr(
        bootstrap.registry_io,
        "update_device_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(lock_error),
    )

    with pytest.raises(bootstrap.EdgeAgentBootstrapError) as exc_info:
        bootstrap._persist_state(
            record["id"],
            require_durable=True,
            status=bootstrap.STATUS_QUEUED,
            stage="queue",
            code="queued",
            message="排队",
        )

    assert exc_info.value.code == "device_mutation_lock_unavailable"


def test_validate_bootstrap_device_accepts_private_key_only(tmp_path, monkeypatch):
    _settings(monkeypatch)
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("test-private-key-material", encoding="utf-8")
    record = {
        **_record(),
        "password": "",
        "password_encrypted": "",
        "private_key_path": str(key_path),
    }

    cfg = bootstrap.validate_bootstrap_device(record)

    assert cfg["password"] == ""
    assert cfg["private_key_path"] == str(key_path)


def test_bootstrap_uses_private_key_path_for_ssh_connection(tmp_path, monkeypatch):
    _settings(monkeypatch)
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("test-private-key-material", encoding="utf-8")
    record = {
        **_record(),
        "password": "",
        "password_encrypted": "",
        "private_key_path": str(key_path),
    }
    records = {record["id"]: record}
    connect_kwargs = {}

    monkeypatch.setattr(
        bootstrap.registry_io,
        "find_device_record",
        lambda device_id: records.get(device_id),
    )

    def update(device_id, **changes):
        records[device_id].update(changes)
        return records[device_id]

    monkeypatch.setattr(bootstrap.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        bootstrap,
        "_quiesce_existing_board_agent",
        lambda _device_id: None,
    )
    monkeypatch.setattr(
        bootstrap,
        "_run_remote_preflight",
        lambda _client, remote_root: {"environment_ready": True},
    )
    monkeypatch.setattr(
        bootstrap,
        "_runtime_prepare",
        lambda client, _cfg, _command: (client, {"environment_ready": True}),
    )
    monkeypatch.setattr(
        bootstrap,
        "_write_and_upload_artifacts",
        lambda *_args, **_kwargs: None,
    )

    class FakeClient:
        def close(self):
            return None

    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_connect",
        lambda **kwargs: (connect_kwargs.update(kwargs) or FakeClient()),
    )
    monkeypatch.setattr(
        bootstrap.edge_ssh_client,
        "ssh_run",
        lambda *_args, **_kwargs: (0, "LABELU_AGENT_INSTALL_V1=1", ""),
    )

    result = bootstrap.bootstrap_edge_agent(
        BootstrapEdgeAgentCommand(
            device_id=record["id"],
            confirm_install=True,
            wait_for_heartbeat=False,
            require_environment_ready=True,
        )
    )

    assert connect_kwargs["password"] == ""
    assert connect_kwargs["private_key_path"] == str(key_path)
    assert result.device_id == record["id"]
