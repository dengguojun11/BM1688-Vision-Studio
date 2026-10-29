from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from labelu.internal.application.service import edge_device_registry as mod
from labelu.internal.application.command.edge_device_registry import (
    CreateEdgeDeviceCommand,
    PatchEdgeDeviceCommand,
    SetActiveEdgeDeviceCommand,
)
from labelu.internal.application.service.edge_runtime_provision import (
    EdgeRuntimeProvisionError,
)


def _run_environment_failure(monkeypatch, exc):
    persisted = []
    logger = SimpleNamespace(warning=Mock(), exception=Mock())
    client = SimpleNamespace(close=Mock())
    record = {"id": "edge_test", "host": "192.168.1.20", "enabled": True}

    monkeypatch.setattr(mod, "logger", logger)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(
        mod.registry_io,
        "_record_to_runtime_config",
        lambda _record: {
            "host": "192.168.1.20",
            "port": 22,
            "user": "linaro",
            "password": "secret",
            "docker_container": "bm1688",
        },
    )
    monkeypatch.setattr(
        mod,
        "probe_edge_ssh_target",
        lambda _cfg: {
            "status": "ok",
            "stage": "remote_base",
            "code": "ok",
            "message": "ok",
            "checked_at": "2026-08-07T09:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        mod, "_update_device_if_present", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        mod,
        "_persist_environment_state",
        lambda *_args, **kwargs: persisted.append(kwargs) or True,
    )
    monkeypatch.setattr(mod, "ssh_connect", lambda **_kwargs: client)
    monkeypatch.setattr(
        mod.edge_runtime_provision,
        "probe_edge_runtime",
        lambda *_args, **_kwargs: {
            "environment_ready": False,
            "fatal_reasons": ["runtime_disk_space_insufficient"],
            "repairable_reasons": ["validator_missing"],
        },
    )
    monkeypatch.setattr(
        mod.edge_runtime_provision, "has_storage_pressure", lambda _report: True
    )

    def fail_runtime(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(mod.edge_runtime_provision, "ensure_edge_runtime", fail_runtime)
    mod._run_environment_check_sync("edge_test", provision=True)
    return logger, persisted, client


def test_environment_storage_block_is_warning_without_traceback(monkeypatch):
    logger, persisted, client = _run_environment_failure(
        monkeypatch,
        EdgeRuntimeProvisionError(
            "space remains insufficient",
            code="manual_cleanup_required",
            report={"fatal_reasons": ["runtime_disk_space_insufficient"]},
        ),
    )

    logger.warning.assert_called_once()
    logger.exception.assert_not_called()
    assert persisted[-1]["status_value"] == "blocked"
    assert persisted[-1]["code"] == "manual_cleanup_required"
    assert persisted[-1]["stage"] == "manual_intervention"
    client.close.assert_called_once()


def test_unexpected_environment_failure_keeps_error_traceback(monkeypatch):
    logger, persisted, client = _run_environment_failure(
        monkeypatch, RuntimeError("unexpected failure")
    )

    logger.warning.assert_not_called()
    logger.exception.assert_called_once()
    assert persisted[-1]["status_value"] == "failed"
    assert persisted[-1]["code"] == "environment_check_failed"
    assert persisted[-1]["stage"] == "environment_provision"
    client.close.assert_called_once()


def test_background_probe_does_not_overwrite_newer_manual_probe(monkeypatch):
    initial = {
        "id": "edge_race",
        "host": "192.168.1.21",
        "enabled": True,
        "probe_status": "unknown",
        "probe_stage": "",
        "probe_code": "",
        "probe_checked_at": "",
    }
    manual = {
        **initial,
        "probe_status": "fail",
        "probe_stage": "auth",
        "probe_code": "auth_failed",
        "probe_checked_at": "2026-07-08T00:00:00+00:00",
    }
    find_calls = iter((initial, manual))
    update_probe = Mock(return_value=True)
    persisted = []
    monkeypatch.setattr(
        mod.registry_io, "find_device_record", lambda _id: next(find_calls)
    )
    monkeypatch.setattr(
        mod.registry_io,
        "_record_to_runtime_config",
        lambda _record: {
            "host": "192.168.1.21",
            "port": 22,
            "user": "linaro",
            "password": "secret",
        },
    )
    monkeypatch.setattr(
        mod,
        "probe_edge_ssh_target",
        lambda _cfg: {
            "status": "fail",
            "stage": "connect",
            "code": "connect_failed",
            "message": "连接失败",
            "checked_at": "2026-09-20T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(mod, "_update_device_if_present", update_probe)
    monkeypatch.setattr(
        mod,
        "_persist_environment_state",
        lambda *_args, **kwargs: persisted.append(kwargs) or True,
    )
    mod._run_environment_check_sync("edge_race", provision=True)

    update_probe.assert_not_called()
    assert persisted[-1]["status_value"] == "failed"
    assert persisted[-1]["code"] == "connect_failed"


def test_busy_device_status_takes_priority_over_environment_state():
    record = {
        "id": "edge_busy",
        "enabled": True,
        "validation_channel": "ssh",
        "environment_status": "checking",
        "environment_report": {},
    }

    view = mod._device_to_view(record, active_device_id="", busy_job_id=42)

    assert view.scheduling_status == "busy"
    assert view.is_busy is True
    assert view.active_job_id == 42


def test_active_agent_bootstrap_overrides_stale_ready_projection():
    record = {
        "id": "edge_bootstrap_projection",
        "enabled": True,
        "validation_channel": "ssh",
        "last_probe_status": "ok",
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
        "agent_bootstrap": {"status": "WAITING_HEARTBEAT"},
    }

    view = mod._device_to_view(record, active_device_id="")

    assert view.scheduling_status == "agent_bootstrap_in_progress"
    assert view.available_for_dispatch is False


def test_auto_agent_control_plane_blocks_stale_ssh_ready_projection(monkeypatch):
    record = {
        "id": "edge_failed_bootstrap_projection",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.1.22",
        "user": "linaro",
        "password": "secret",
        "last_probe_status": "ok",
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
        "agent_bootstrap": {
            "status": "FAILED",
            "code": "heartbeat_timeout",
        },
    }
    monkeypatch.setattr(mod.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", "agent-token")

    view = mod._device_to_view(record, active_device_id="")

    assert view.scheduling_status == "environment_not_ready"
    assert view.available_for_dispatch is False


def test_auto_agent_control_plane_allows_only_completed_ssh_bootstrap(monkeypatch):
    record = {
        "id": "edge_ready_bootstrap_projection",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.1.23",
        "user": "linaro",
        "password": "secret",
        "last_probe_status": "ok",
        "last_probe_checked_at": datetime.now(timezone.utc).isoformat(),
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
        "agent_bootstrap": {"status": "READY"},
    }
    monkeypatch.setattr(mod.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", "agent-token")
    view = mod._device_to_view(record, active_device_id="")

    assert view.available_for_dispatch is True


def test_future_board_agent_heartbeat_is_not_fresh():
    now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
    record = {
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": (now + timedelta(hours=1)).isoformat(),
    }

    assert mod._board_agent_heartbeat_is_fresh(record, now=now) is False


def test_delete_busy_device_is_rejected_before_registry_mutation(monkeypatch):
    record = {"id": "edge_busy_delete"}
    delete = Mock()
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(mod.registry_io, "delete_device_record", delete)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: 701)

    with pytest.raises(Exception) as exc_info:
        mod.delete_edge_device(record["id"])

    assert exc_info.value.status_code == 409
    assert "任务 #701" in exc_info.value.detail
    delete.assert_not_called()


def test_set_active_device_fails_closed_when_busy_state_cannot_be_read(monkeypatch):
    records = {
        "edge_current": {"id": "edge_current"},
        "edge_target": {"id": "edge_target"},
    }
    set_active = Mock()
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        mod.registry_io, "find_device_record", lambda device_id: records.get(device_id)
    )
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: "edge_current")
    monkeypatch.setattr(mod.registry_io, "set_active_device_id", set_active)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: -1)

    with pytest.raises(Exception) as exc_info:
        mod.set_active_edge_device(
            SetActiveEdgeDeviceCommand(device_id="edge_target")
        )

    assert exc_info.value.status_code == 503
    assert "无法确认边端任务状态" in exc_info.value.detail
    set_active.assert_not_called()


def test_manual_environment_check_guard_rejects_running_device(monkeypatch):
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: 903)

    with pytest.raises(Exception) as exc_info:
        mod.assert_no_active_job_for_environment_check("edge_busy_check")

    assert exc_info.value.status_code == 409
    assert "任务 #903" in str(exc_info.value.detail)


def test_automatic_environment_check_skips_running_device(monkeypatch):
    record = {
        "id": "edge_busy_auto",
        "enabled": True,
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
    }
    thread = Mock()
    view = object()
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: "")
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: 904)
    monkeypatch.setattr(mod, "_device_to_view", lambda *_args, **_kwargs: view)
    monkeypatch.setattr(mod.threading, "Thread", thread)
    persist = Mock()
    monkeypatch.setattr(mod, "_persist_environment_state", persist)

    result = mod.start_environment_check("edge_busy_auto", provision=True)

    assert result is view
    thread.assert_not_called()
    persist.assert_not_called()


def test_environment_worker_stops_before_ssh_when_job_starts(monkeypatch):
    record = {
        "id": "edge_busy_race",
        "enabled": True,
        "environment_report": {},
    }
    persisted = []
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: 905)
    monkeypatch.setattr(
        mod,
        "_persist_environment_state",
        lambda *_args, **kwargs: persisted.append(kwargs) or True,
    )
    probe = Mock(side_effect=AssertionError("busy device must not be probed"))
    monkeypatch.setattr(mod, "probe_edge_ssh_target", probe)

    mod._run_environment_check_sync("edge_busy_race", provision=True)

    probe.assert_not_called()
    assert persisted[-1]["status_value"] == "blocked"
    assert persisted[-1]["code"] == "device_busy"
    assert "任务 #905" in persisted[-1]["message"]


def _patch_update_side_effect_harness(monkeypatch, before, after):
    bootstrap_calls = []
    environment_calls = []
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: before)
    monkeypatch.setattr(
        mod.registry_io,
        "update_device_record",
        lambda *_args, **_kwargs: after,
    )
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: "")
    monkeypatch.setattr(
        mod,
        "_device_to_view",
        lambda record, **_kwargs: record,
    )
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(mod, "_has_complete_runtime_config", lambda _record: True)
    monkeypatch.setattr(
        mod,
        "_start_auto_agent_bootstrap",
        lambda device_id: bootstrap_calls.append(device_id) or True,
    )
    monkeypatch.setattr(
        mod,
        "start_environment_check",
        lambda device_id, provision=True: environment_calls.append(
            (device_id, provision)
        ),
    )
    return bootstrap_calls, environment_calls


def _ready_agent_record(**overrides):
    record = {
        "id": "edge_update",
        "name": "旧名称",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.1.22",
        "port": 22,
        "user": "linaro",
        "edge_root": "/data/labelu_edge",
        "remote_base": "/data/labelu_edge/jobs",
        "sophon_demo_root": "/opt/sophon-demo",
        "docker_container": "bm1688",
        "docker_use_sudo": True,
        "connect_timeout_sec": 30,
        "validation_timeout_sec": 7200,
        "password_encrypted": "ciphertext",
        "agent_bootstrap": {"status": "READY"},
    }
    record.update(overrides)
    return record


def _tracked_mutation_lock(monkeypatch, module=mod):
    """Install a lock double that exposes the service's critical section."""

    from contextlib import contextmanager

    state = {"held": False, "calls": []}

    @contextmanager
    def lock():
        state["held"] = True
        try:
            yield True
        finally:
            state["held"] = False

    monkeypatch.setattr(module.registry_io, "device_mutation_lock", lock)
    return state


def test_create_queues_agent_while_registry_mutation_lock_is_held(monkeypatch):
    state = _tracked_mutation_lock(monkeypatch)
    record = _ready_agent_record(id="edge_created", agent_bootstrap={})
    bootstrap_calls = []

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        mod.registry_io,
        "create_device_record",
        lambda **_kwargs: record,
    )
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: record["id"])
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(mod, "_device_to_view", lambda item, **_kwargs: item)

    def queue_bootstrap(device_id):
        bootstrap_calls.append((device_id, state["held"]))
        return True

    monkeypatch.setattr(mod, "_start_auto_agent_bootstrap", queue_bootstrap)

    result = mod.create_edge_device(
        CreateEdgeDeviceCommand(
            name="新边端",
            host="192.168.1.40",
            password="secret",
        )
    )

    assert result["id"] == "edge_created"
    assert bootstrap_calls == [("edge_created", True)]
    assert state["held"] is False


def test_create_key_only_device_passes_private_key_to_registry_and_bootstrap(
    monkeypatch,
):
    state = _tracked_mutation_lock(monkeypatch)
    captured = {}
    record = _ready_agent_record(
        id="edge_key_only_created",
        password_encrypted="",
        private_key_path_encrypted="encrypted-key-path",
    )
    bootstrap_calls = []

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)

    def create(**kwargs):
        captured.update(kwargs)
        return record

    monkeypatch.setattr(mod.registry_io, "create_device_record", create)
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: record["id"])
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(mod, "_device_to_view", lambda item, **_kwargs: item)
    monkeypatch.setattr(
        mod,
        "_start_auto_agent_bootstrap",
        lambda device_id: bootstrap_calls.append(device_id) or True,
    )

    result = mod.create_edge_device(
        CreateEdgeDeviceCommand(
            name="仅私钥边端",
            host="192.168.1.41",
            user="linaro",
            private_key_path="D:/labelu-secrets/id_ed25519",
        )
    )

    assert result["id"] == record["id"]
    assert captured["password"] is None
    assert captured["private_key_path"] == "D:/labelu-secrets/id_ed25519"
    assert bootstrap_calls == [record["id"]]
    assert state["held"] is False


def test_create_auto_config_failure_is_persisted_without_legacy_fallback(monkeypatch):
    state = _tracked_mutation_lock(monkeypatch)
    record = _ready_agent_record(
        id="edge_auto_config_failed",
        environment_report={"environment_ready": True, "existing": "kept"},
    )
    stored = {"record": dict(record)}
    updates = []
    bootstrap = Mock()
    environment = Mock()

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        mod.registry_io,
        "create_device_record",
        lambda **_kwargs: stored["record"],
    )
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: record["id"])
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: stored["record"])

    def update(_device_id, **changes):
        updates.append(changes)
        stored["record"].update(changes)
        return stored["record"]

    monkeypatch.setattr(mod.registry_io, "update_device_record", update)
    monkeypatch.setattr(
        mod,
        "_auto_configure_agent_for_page",
        lambda _record: (False, "public_base_url_missing", "无法推导边端回连地址"),
    )
    monkeypatch.setattr(mod, "_start_auto_agent_bootstrap", bootstrap)
    monkeypatch.setattr(mod, "start_environment_check", environment)
    monkeypatch.setattr(mod, "_device_to_view", lambda item, **_kwargs: item)

    result = mod.create_edge_device(
        CreateEdgeDeviceCommand(
            name="自动引导失败设备",
            host="192.168.1.42",
            password="secret",
            auto_configure_agent=True,
        )
    )

    assert result["agent_bootstrap"]["status"] == "FAILED"
    assert result["agent_bootstrap"]["stage"] == "control_plane"
    assert result["environment_status"] == "failed"
    assert result["environment_report"]["environment_ready"] is False
    assert result["environment_report"]["existing"] == "kept"
    bootstrap.assert_not_called()
    environment.assert_not_called()
    assert state["held"] is False


def test_create_auto_config_success_queues_agent_without_legacy_fallback(monkeypatch):
    state = _tracked_mutation_lock(monkeypatch)
    record = _ready_agent_record(
        id="edge_auto_config_success",
        agent_bootstrap={},
    )
    stored = {"record": record}
    bootstrap = Mock()
    environment = Mock()

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        mod.registry_io,
        "create_device_record",
        lambda **_kwargs: stored["record"],
    )
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: record["id"])
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: stored["record"])
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(
        mod,
        "_auto_configure_agent_for_page",
        lambda _record: (True, "", ""),
    )
    monkeypatch.setattr(mod, "_start_auto_agent_bootstrap", bootstrap)
    monkeypatch.setattr(mod, "start_environment_check", environment)
    monkeypatch.setattr(mod, "_device_to_view", lambda item, **_kwargs: item)

    result = mod.create_edge_device(
        CreateEdgeDeviceCommand(
            name="自动引导成功设备",
            host="192.168.1.43",
            password="secret",
            auto_configure_agent=True,
        )
    )

    assert result["id"] == record["id"]
    bootstrap.assert_called_once_with(record["id"])
    environment.assert_not_called()
    assert state["held"] is False


def test_update_auto_config_failure_does_not_rebootstrap_or_fallback(monkeypatch):
    state = _tracked_mutation_lock(monkeypatch)
    before = _ready_agent_record(id="edge_auto_config_update")
    stored = {"record": dict(before)}
    bootstrap = Mock()
    environment = Mock()

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: stored["record"])
    monkeypatch.setattr(
        mod.registry_io,
        "update_device_record",
        lambda _device_id, **changes: stored["record"].update(changes)
        or stored["record"],
    )
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: "")
    monkeypatch.setattr(
        mod,
        "_auto_configure_agent_for_page",
        lambda _record: (False, "control_plane_env_disabled", "自动配置已关闭"),
    )
    monkeypatch.setattr(mod, "_start_auto_agent_bootstrap", bootstrap)
    monkeypatch.setattr(mod, "start_environment_check", environment)
    monkeypatch.setattr(mod, "_device_to_view", lambda item, **_kwargs: item)

    result = mod.update_edge_device(
        "edge_auto_config_update",
        PatchEdgeDeviceCommand(name="仅修改名称", auto_configure_agent=True),
    )

    assert result["agent_bootstrap"]["status"] == "FAILED"
    assert result["agent_bootstrap"]["code"] == "control_plane_env_disabled"
    bootstrap.assert_not_called()
    environment.assert_not_called()
    assert state["held"] is False


def test_update_auto_config_success_queues_agent_without_legacy_fallback(monkeypatch):
    state = _tracked_mutation_lock(monkeypatch)
    before = _ready_agent_record(
        id="edge_auto_config_update_success",
        agent_bootstrap={"status": "WAITING_HEARTBEAT"},
    )
    stored = {"record": dict(before)}
    bootstrap = Mock()
    environment = Mock()

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: stored["record"])

    def update(_device_id, **changes):
        stored["record"].update(changes)
        return stored["record"]

    monkeypatch.setattr(mod.registry_io, "update_device_record", update)
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: "")
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(
        mod,
        "_auto_configure_agent_for_page",
        lambda _record: (True, "", ""),
    )
    monkeypatch.setattr(mod, "_start_auto_agent_bootstrap", bootstrap)
    monkeypatch.setattr(mod, "start_environment_check", environment)
    monkeypatch.setattr(mod, "_device_to_view", lambda item, **_kwargs: item)

    result = mod.update_edge_device(
        before["id"],
        PatchEdgeDeviceCommand(name="自动引导成功编辑", auto_configure_agent=True),
    )

    assert result["name"] == "自动引导成功编辑"
    bootstrap.assert_called_once_with(before["id"])
    environment.assert_not_called()
    assert state["held"] is False


def test_update_reconciliation_runs_before_registry_mutation_lock_is_released(
    monkeypatch,
):
    state = _tracked_mutation_lock(monkeypatch)
    before = _ready_agent_record(host="192.168.1.41")
    after = _ready_agent_record(host="192.168.1.42")
    bootstrap_calls = []

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: before)
    monkeypatch.setattr(
        mod.registry_io,
        "update_device_record",
        lambda *_args, **_kwargs: after,
    )
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: "")
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    monkeypatch.setattr(mod, "_should_reconcile_after_update", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(mod, "_device_to_view", lambda item, **_kwargs: item)

    def queue_bootstrap(device_id):
        bootstrap_calls.append((device_id, state["held"]))
        return True

    monkeypatch.setattr(mod, "_start_auto_agent_bootstrap", queue_bootstrap)

    result = mod.update_edge_device(
        "edge_update",
        PatchEdgeDeviceCommand(host="192.168.1.42"),
    )

    assert result["host"] == "192.168.1.42"
    assert bootstrap_calls == [("edge_update", True)]
    assert state["held"] is False


def test_ready_agent_metadata_and_strategy_patch_does_not_rebootstrap(monkeypatch):
    before = _ready_agent_record()
    after = _ready_agent_record(name="新名称")
    bootstrap_calls, environment_calls = _patch_update_side_effect_harness(
        monkeypatch, before, after
    )

    result = mod.update_edge_device(
        "edge_update",
        PatchEdgeDeviceCommand(
            name="新名称",
            auto_after_export=False,
            run_coco_eval=True,
        ),
    )

    assert result["name"] == "新名称"
    assert bootstrap_calls == []
    assert environment_calls == []


def test_runtime_patch_reboots_agent_even_when_previous_state_was_ready(monkeypatch):
    before = _ready_agent_record()
    after = _ready_agent_record(host="192.168.1.23")
    bootstrap_calls, environment_calls = _patch_update_side_effect_harness(
        monkeypatch, before, after
    )

    mod.update_edge_device(
        "edge_update",
        PatchEdgeDeviceCommand(host="192.168.1.23"),
    )

    assert bootstrap_calls == ["edge_update"]
    assert environment_calls == []


def test_non_ready_agent_metadata_patch_retries_bootstrap(monkeypatch):
    before = _ready_agent_record(agent_bootstrap={"status": "WAITING_HEARTBEAT"})
    after = _ready_agent_record(
        name="再次命名",
        agent_bootstrap={"status": "WAITING_HEARTBEAT"},
    )
    bootstrap_calls, environment_calls = _patch_update_side_effect_harness(
        monkeypatch, before, after
    )

    mod.update_edge_device(
        "edge_update",
        PatchEdgeDeviceCommand(name="再次命名"),
    )

    assert bootstrap_calls == ["edge_update"]
    assert environment_calls == []


def test_board_agent_patch_does_not_reconcile_retained_ssh_credentials(monkeypatch):
    before = _ready_agent_record(
        validation_channel="board_agent",
        agent_bootstrap={"status": "WAITING_HEARTBEAT"},
        environment_status="failed",
        environment_report={"environment_ready": False},
    )
    after = dict(before)
    after["name"] = "板端设备改名"
    bootstrap_calls, environment_calls = _patch_update_side_effect_harness(
        monkeypatch, before, after
    )

    mod.update_edge_device(
        "edge_update",
        PatchEdgeDeviceCommand(name="板端设备改名"),
    )

    assert bootstrap_calls == []
    assert environment_calls == []


def test_should_reconcile_returns_false_for_board_agent_runtime_field_changes():
    before = _ready_agent_record(
        validation_channel="board_agent",
        agent_bootstrap={"status": "FAILED"},
    )
    after = dict(before, host="192.168.1.99", port=2222)

    assert mod._should_reconcile_after_update(before, after) is False


def test_sync_bootstrap_validation_failure_is_visible_and_redacted(monkeypatch):
    ssh_secret = "ssh-password-123"
    agent_token = "agent-token-123"
    before = _ready_agent_record(
        password=ssh_secret,
        agent_bootstrap={"status": "WAITING_HEARTBEAT"},
    )
    after = dict(before)
    after["name"] = "同步失败设备"
    current = {"record": before}
    fallback = Mock()

    def find_device(_device_id):
        return current["record"]

    def update_device(_device_id, **changes):
        current["record"].update(
            {
                key: value
                for key, value in changes.items()
                if not (key == "password" and value is None)
            }
        )
        return current["record"]

    def fail_before_queue(_command):
        raise mod.edge_agent_bootstrap.EdgeAgentBootstrapError(
            f"回连地址无效 token={agent_token} password={ssh_secret}",
            code="public_base_url_missing",
        )

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", agent_token)
    monkeypatch.setattr(mod.registry_io, "find_device_record", find_device)
    monkeypatch.setattr(mod.registry_io, "update_device_record", update_device)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(mod, "_device_to_view", lambda record, **_kwargs: record)
    monkeypatch.setattr(
        mod.edge_agent_bootstrap,
        "start_bootstrap_background",
        fail_before_queue,
    )
    monkeypatch.setattr(mod, "start_environment_check", fallback)

    result = mod.update_edge_device(
        "edge_update",
        PatchEdgeDeviceCommand(name="同步失败设备"),
    )

    assert result["name"] == "同步失败设备"
    state = current["record"]["agent_bootstrap"]
    assert state["status"] == "FAILED"
    assert state["stage"] == "validation"
    assert state["code"] == "public_base_url_missing"
    assert "回连地址无效" in state["message"]
    assert agent_token not in state["message"]
    assert ssh_secret not in state["message"]
    assert current["record"]["environment_report"]["agent_bootstrap"] == state
    fallback.assert_not_called()


def test_manual_environment_check_does_not_bypass_automatic_agent_gate(monkeypatch):
    record = _ready_agent_record(
        agent_bootstrap={"status": "FAILED", "code": "preflight_failed"},
        environment_status="ready",
        environment_report={"environment_ready": True},
    )
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(mod.registry_io, "get_active_device_id", lambda: "")
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    thread = Mock()
    monkeypatch.setattr(mod.threading, "Thread", thread)
    monkeypatch.setattr(mod, "_device_to_view", lambda item, **_kwargs: item)

    result = mod.start_environment_check(record["id"], provision=True)

    assert result is record
    thread.assert_not_called()


def test_auto_bootstrap_lock_contention_does_not_overwrite_active_state(monkeypatch):
    contention = mod.edge_agent_bootstrap.EdgeAgentBootstrapError(
        "设备正在执行其他变更",
        code="device_mutation_lock_unavailable",
    )
    start = Mock(side_effect=contention)
    persist = Mock()
    schedule = Mock()
    logger = SimpleNamespace(info=Mock(), warning=Mock(), exception=Mock())

    monkeypatch.setattr(mod.edge_agent_bootstrap, "start_bootstrap_background", start)
    monkeypatch.setattr(mod.edge_agent_bootstrap, "persist_bootstrap_failure", persist)
    monkeypatch.setattr(mod, "_schedule_auto_agent_bootstrap_retry", schedule)
    monkeypatch.setattr(mod, "logger", logger)

    result = mod._start_auto_agent_bootstrap("edge_lock_busy")

    assert result is False
    start.assert_called_once()
    persist.assert_not_called()
    schedule.assert_called_once_with("edge_lock_busy")
    logger.info.assert_called_once()


def test_auto_bootstrap_persist_failure_uses_transient_retry_path(monkeypatch):
    transient = mod.edge_agent_bootstrap.EdgeAgentBootstrapError(
        "无法确认 Agent 引导状态已持久化",
        code="bootstrap_state_persist_failed",
    )
    start = Mock(side_effect=transient)
    persist = Mock()
    schedule = Mock()
    logger = SimpleNamespace(info=Mock(), warning=Mock(), exception=Mock())

    monkeypatch.setattr(mod.edge_agent_bootstrap, "start_bootstrap_background", start)
    monkeypatch.setattr(mod.edge_agent_bootstrap, "persist_bootstrap_failure", persist)
    monkeypatch.setattr(mod, "_schedule_auto_agent_bootstrap_retry", schedule)
    monkeypatch.setattr(mod, "logger", logger)

    result = mod._start_auto_agent_bootstrap("edge_state_persist_busy")

    assert result is False
    start.assert_called_once()
    persist.assert_not_called()
    schedule.assert_called_once_with("edge_state_persist_busy")
    logger.info.assert_called_once()


def test_auto_bootstrap_retry_timer_is_deduplicated_and_retries(monkeypatch):
    record = {
        "id": "edge_retry_timer",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.1.20",
        "user": "linaro",
        "password_encrypted": "configured",
        "agent_bootstrap": {},
    }
    attempts = []
    timers = []

    class _Timer:
        def __init__(self, _delay, target, args=()):
            self.target = target
            self.args = args
            self.started = False

        def is_alive(self):
            return self.started

        def start(self):
            self.started = True
            timers.append(self)

        def cancel(self):
            self.started = False

    contention = mod.edge_agent_bootstrap.EdgeAgentBootstrapError(
        "设备正在执行其他变更", code="device_mutation_lock_unavailable"
    )

    def start_bootstrap(_command):
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise contention
        return object()

    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(
        mod.edge_agent_bootstrap, "start_bootstrap_background", start_bootstrap
    )
    monkeypatch.setattr(mod.threading, "Timer", _Timer)

    assert mod._start_auto_agent_bootstrap(record["id"]) is False
    # A second collision notification must reuse the same pending timer.
    assert mod._schedule_auto_agent_bootstrap_retry(record["id"]) is False
    assert len(timers) == 1

    timers[0].target(*timers[0].args)

    assert attempts == [0, 1]
    assert record["id"] not in mod._AUTO_AGENT_RETRY_TIMERS
    assert record["id"] not in mod._AUTO_AGENT_RETRY_ATTEMPTS


def test_busy_auto_bootstrap_retry_remains_scheduled(monkeypatch):
    record = {
        "id": "edge_retry_busy",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.1.20",
        "user": "linaro",
        "password_encrypted": "configured",
        "agent_bootstrap": {"status": "FAILED", "code": "device_mutation_lock_unavailable"},
    }
    schedule = Mock(return_value=True)
    clear = Mock()

    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: True)
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_retryable", lambda _record: True)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: 991)
    monkeypatch.setattr(mod, "_schedule_auto_agent_bootstrap_retry", schedule)
    monkeypatch.setattr(mod, "_clear_auto_agent_bootstrap_retry", clear)

    mod._run_auto_agent_bootstrap_retry(record["id"])

    schedule.assert_called_once_with(record["id"])
    clear.assert_not_called()


def test_startup_recovery_requeues_not_started_automatic_agent(monkeypatch):
    record = {
        "id": "edge_recover_not_started",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.1.20",
        "user": "linaro",
        "password_encrypted": "configured",
        "agent_bootstrap": {"status": "NOT_STARTED"},
    }
    monkeypatch.setattr(mod.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", "token")
    monkeypatch.setattr(
        mod.edge_agent_bootstrap, "resume_persisted_bootstraps", lambda: 0
    )
    monkeypatch.setattr(mod.registry_io, "list_device_records", lambda: [record])
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    start = Mock(return_value=True)
    monkeypatch.setattr(mod, "_start_auto_agent_bootstrap", start)
    monkeypatch.setattr(mod, "_has_complete_runtime_config", lambda _record: True)

    assert mod.recover_persisted_agent_bootstraps() == 1
    start.assert_called_once_with(record["id"])


def test_startup_recovery_does_not_retry_terminal_agent_validation_failure(monkeypatch):
    record = {
        "id": "edge_recover_terminal",
        "enabled": True,
        "validation_channel": "ssh",
        "host": "192.168.1.20",
        "user": "linaro",
        "password_encrypted": "configured",
        "agent_bootstrap": {
            "status": "FAILED",
            "code": "public_base_url_missing",
        },
    }
    monkeypatch.setattr(mod.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", "token")
    monkeypatch.setattr(
        mod.edge_agent_bootstrap, "resume_persisted_bootstraps", lambda: 0
    )
    monkeypatch.setattr(mod.registry_io, "list_device_records", lambda: [record])
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    start = Mock(return_value=True)
    monkeypatch.setattr(mod, "_start_auto_agent_bootstrap", start)
    monkeypatch.setattr(mod, "_has_complete_runtime_config", lambda _record: True)

    assert mod.recover_persisted_agent_bootstraps() == 0
    start.assert_not_called()


def test_environment_summary_preserves_agent_bootstrap_mirror():
    summary = mod._environment_report_summary(
        {
            "environment_ready": False,
            "agent_bootstrap": {
                "status": "FAILED",
                "stage": "validation",
                "code": "public_base_url_missing",
            },
        }
    )

    assert summary["environment_ready"] is False
    assert summary["agent_bootstrap"]["status"] == "FAILED"
    assert summary["agent_bootstrap"]["code"] == "public_base_url_missing"


def test_running_device_rejects_connection_patch_before_registry_write(monkeypatch):
    before = _ready_agent_record()
    after = _ready_agent_record(host="192.168.1.23")
    update = Mock(return_value=after)
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: before)
    monkeypatch.setattr(mod.registry_io, "update_device_record", update)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: 901)

    with pytest.raises(Exception) as exc_info:
        mod.update_edge_device(
            "edge_update",
            PatchEdgeDeviceCommand(host="192.168.1.23"),
        )

    assert exc_info.value.status_code == 409
    assert "#901" in str(exc_info.value.detail)
    update.assert_not_called()


def test_running_device_rejects_enabled_patch_before_registry_write(monkeypatch):
    before = _ready_agent_record()
    after = _ready_agent_record(enabled=False)
    update = Mock(return_value=after)
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: before)
    monkeypatch.setattr(mod.registry_io, "update_device_record", update)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: 902)

    with pytest.raises(Exception) as exc_info:
        mod.update_edge_device(
            "edge_update",
            PatchEdgeDeviceCommand(enabled=False),
        )

    assert exc_info.value.status_code == 409
    assert "#902" in str(exc_info.value.detail)
    update.assert_not_called()


def test_running_device_allows_name_and_strategy_patch(monkeypatch):
    before = _ready_agent_record()
    after = _ready_agent_record(name="运行中重命名")
    bootstrap_calls, environment_calls = _patch_update_side_effect_harness(
        monkeypatch, before, after
    )
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: 902)

    result = mod.update_edge_device(
        "edge_update",
        PatchEdgeDeviceCommand(name="运行中重命名", auto_after_export=False),
    )

    assert result["name"] == "运行中重命名"
    assert bootstrap_calls == []
    assert environment_calls == []


def test_empty_top_level_bootstrap_status_keeps_legacy_nested_status():
    record = {
        "agent_bootstrap": {"status": ""},
        "environment_report": {"agent_bootstrap": {"status": "READY"}},
    }

    assert mod._agent_bootstrap_status(record) == "READY"


def test_startup_scan_does_not_overwrite_persisted_bootstrap_when_gate_is_off(monkeypatch):
    record = {
        "id": "edge_pending_bootstrap",
        "enabled": True,
        "validation_channel": "ssh",
        "agent_bootstrap": {"status": "WAITING_HEARTBEAT"},
        "environment_status": "ready",
        "environment_report": {"environment_ready": True},
    }
    start_check = Mock()
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_ENABLED", False)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_PRIMARY_ENABLED", False)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", "")
    monkeypatch.setattr(mod.registry_io, "list_device_records", lambda: [record])
    monkeypatch.setattr(mod, "start_environment_check", start_check)

    assert mod.scan_and_start_configured_environment_checks() == 0
    start_check.assert_not_called()


def test_environment_scan_skips_agent_owned_devices(monkeypatch):
    records = [
        {
            "id": "edge-auto-pending",
            "validation_channel": "ssh",
            "agent_bootstrap": {"status": "WAITING_HEARTBEAT"},
        },
        {
            "id": "edge-board-ready",
            "validation_channel": "board_agent",
            "agent_bootstrap": {"status": "READY"},
        },
        {
            "id": "edge-legacy",
            "validation_channel": "ssh",
            "environment_status": "unknown",
        },
    ]
    queued = []
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "list_device_records", lambda: records)
    monkeypatch.setattr(mod, "_has_complete_runtime_config", lambda _record: True)
    monkeypatch.setattr(
        mod,
        "_auto_agent_bootstrap_enabled",
        lambda record: record.get("id") == "edge-auto-pending",
    )
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    monkeypatch.setattr(
        mod,
        "start_environment_check",
        lambda device_id, *, provision: queued.append((device_id, provision)),
    )

    count = mod.scan_and_start_configured_environment_checks(
        provision=True,
        only_pending=False,
    )

    assert count == 1
    assert queued == [("edge-legacy", True)]


def test_startup_recovery_precedes_legacy_scan(monkeypatch):
    order = []
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", "token")
    monkeypatch.setattr(mod, "_SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED", False)
    monkeypatch.setattr(
        mod,
        "recover_persisted_agent_bootstraps",
        lambda: order.append("recover") or 1,
    )
    monkeypatch.setattr(
        mod,
        "scan_and_start_configured_environment_checks",
        lambda **_kwargs: order.append("scan") or 0,
    )

    class _Thread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(mod.threading, "Thread", _Thread)

    mod.schedule_system_environment_checks()

    assert order == ["recover", "scan"]


def test_startup_recovery_runs_when_yolo_scan_is_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", False)
    monkeypatch.setattr(mod.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_PRIMARY_ENABLED", True)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", "token")
    monkeypatch.setattr(mod, "_SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED", False)
    monkeypatch.setattr(
        mod,
        "recover_persisted_agent_bootstraps",
        lambda: calls.append("recover") or 2,
    )
    scan = Mock()
    monkeypatch.setattr(mod, "scan_and_start_configured_environment_checks", scan)

    class _Thread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(mod.threading, "Thread", _Thread)

    mod.schedule_system_environment_checks()

    assert calls == ["recover"]
    scan.assert_not_called()


def test_startup_recovery_does_nothing_when_both_control_planes_are_disabled(
    monkeypatch,
):
    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", False)
    monkeypatch.setattr(mod.settings, "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", False)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_ENABLED", False)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_PRIMARY_ENABLED", False)
    monkeypatch.setattr(mod.settings, "BOARD_AGENT_TOKEN", "")
    monkeypatch.setattr(mod, "_SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED", False)
    recover = Mock()
    thread = Mock()
    monkeypatch.setattr(mod, "recover_persisted_agent_bootstraps", recover)
    monkeypatch.setattr(mod.threading, "Thread", thread)

    mod.schedule_system_environment_checks()

    recover.assert_not_called()
    thread.assert_not_called()
    assert mod._SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED is False


def test_environment_worker_defers_when_registry_mutation_lock_is_busy(monkeypatch):
    record = {
        "id": "edge_lock_busy_worker",
        "environment_report": {},
        "probe_status": "unknown",
        "probe_stage": "",
        "probe_code": "",
        "probe_checked_at": "",
    }
    logger = SimpleNamespace(info=Mock(), warning=Mock(), exception=Mock())
    lock_error = mod.registry_io.DeviceMutationLockUnavailableError("busy")

    monkeypatch.setattr(mod, "logger", logger)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: False)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    monkeypatch.setattr(
        mod.registry_io,
        "_record_to_runtime_config",
        lambda _record: {"host": "192.168.1.20", "port": 22, "user": "linaro"},
    )
    monkeypatch.setattr(
        mod,
        "probe_edge_ssh_target",
        lambda _cfg: {
            "status": "ok",
            "stage": "remote_base",
            "code": "ok",
            "message": "ok",
            "checked_at": "2026-10-25T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        mod,
        "_update_device_if_present",
        Mock(side_effect=lock_error),
    )

    # A transient lock failure must not escape the daemon worker as an
    # unhandled exception.  A later startup/manual scan can retry it.
    mod._run_environment_check_sync(record["id"], provision=True)

    logger.info.assert_called_once()


def test_start_environment_check_maps_registry_lock_contention_to_503(monkeypatch):
    record = {"id": "edge_lock_busy_queue", "environment_report": {}}
    lock_error = mod.registry_io.DeviceMutationLockUnavailableError("busy")

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(mod, "_active_processing_job_id", lambda _id: None)
    monkeypatch.setattr(mod, "_auto_agent_bootstrap_enabled", lambda _record: False)
    monkeypatch.setattr(
        mod,
        "_persist_environment_state",
        Mock(side_effect=lock_error),
    )

    with pytest.raises(Exception) as exc_info:
        mod.start_environment_check(record["id"], provision=True)

    assert exc_info.value.status_code == 503
    assert "其他变更" in str(exc_info.value.detail)


def test_probe_maps_lock_contention_when_environment_queue_cannot_start(monkeypatch):
    record = {
        "id": "edge_lock_busy_probe",
        "validation_channel": "ssh",
        "host": "192.168.1.20",
        "user": "linaro",
        "password": "secret",
    }
    lock_error = mod.registry_io.DeviceMutationLockUnavailableError("busy")

    monkeypatch.setattr(mod.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(mod.registry_io, "find_device_record", lambda _id: record)
    monkeypatch.setattr(mod, "_assert_no_active_job_operation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mod.registry_io,
        "_record_to_runtime_config",
        lambda _record: {"host": record["host"], "port": 22, "user": record["user"]},
    )
    monkeypatch.setattr(
        mod,
        "probe_edge_ssh_target",
        lambda _cfg: {
            "status": "ok",
            "stage": "remote_base",
            "code": "ok",
            "message": "ok",
            "checked_at": "2026-10-25T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(mod.registry_io, "update_device_record", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(mod, "start_environment_check", Mock(side_effect=lock_error))

    with pytest.raises(Exception) as exc_info:
        mod.probe_edge_device(record["id"])

    assert exc_info.value.status_code == 503
    assert "其他变更" in str(exc_info.value.detail)
