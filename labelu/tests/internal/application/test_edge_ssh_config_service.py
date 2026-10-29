"""The legacy SSH API delegates to the MySQL-backed active device."""

import json

import pytest

from labelu.internal.application.command.edge_ssh_config import PatchEdgeSshConfigCommand
from labelu.internal.application.service import edge_ssh_config as svc
from labelu.internal.common import edge_ssh_config as cfg_io
from labelu.internal.application.response.edge_ssh_config import EdgeSshConfigResponse
from labelu.internal.common.error_code import ErrorCode, LabelUException


def _probe_ok(_cfg=None):
    return {
        "status": "ok",
        "stage": "remote_base",
        "code": "ok",
        "message": "ok",
        "resolved_host": "10.10.11.26",
        "resolved_user": "linaro",
        "remote_base": "/data/labelu_edge/jobs",
        "password_source": "mysql:encrypted",
        "checked_at": "2026-07-06T00:00:00+00:00",
    }


def test_patch_edge_ssh_saves_encrypted_mysql_password(monkeypatch):
    from labelu.internal.common import config
    from labelu.internal.common.edge_device_registry import load_device_store

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(svc, "probe_edge_ssh_target", _probe_ok)
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.schedule_edge_ssh_catchup_background",
        lambda: None,
    )

    out = svc.patch_edge_ssh_config(
        PatchEdgeSshConfigCommand(
            enabled=True,
            host="10.10.11.26",
            user="linaro",
            password_secret_action="set",
            password="secret-pass",
        )
    )
    assert out.host == "10.10.11.26"
    assert out.password_configured is True
    assert out.password_runtime_available is True
    assert out.password_source_type == "mysql:encrypted"
    assert out.auto_after_export is True
    assert out.probe_status == "ok"
    assert out.auto_run_ready is True

    raw = cfg_io.load_edge_ssh_config()
    assert raw["password"] == "secret-pass"
    serialized = json.dumps(load_device_store(), ensure_ascii=False)
    assert "secret-pass" not in serialized
    assert "password_env_name" not in serialized


def test_patch_edge_ssh_clear_encrypted_password(monkeypatch):
    from labelu.internal.common import config
    from labelu.internal.common.edge_device_registry import load_device_store

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(svc, "probe_edge_ssh_target", _probe_ok)

    svc.patch_edge_ssh_config(
        PatchEdgeSshConfigCommand(
            host="10.10.11.26",
            user="linaro",
            password_secret_action="set",
            password="secret-pass",
        )
    )
    out = svc.patch_edge_ssh_config(PatchEdgeSshConfigCommand(password_secret_action="clear"))
    assert out.password_configured is False
    assert out.password_runtime_available is False
    assert out.password_masked_hint == ""
    assert out.auto_run_ready is False
    raw = cfg_io.load_edge_ssh_config()
    assert raw["validation_channel"] == "ssh"
    assert raw["password_is_configured"] is False
    assert "password_encrypted" not in load_device_store()["devices"][0]


def test_patch_edge_ssh_does_not_schedule_catchup_when_probe_fails(monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    scheduled = {"called": False}
    monkeypatch.setattr(
        svc,
        "probe_edge_ssh_target",
        lambda _cfg=None: {
            "status": "fail",
            "stage": "auth",
            "code": "auth_failed",
            "message": "SSH 认证失败",
            "resolved_host": "10.10.11.26",
            "resolved_user": "linaro",
            "remote_base": "/data/labelu_edge/jobs",
            "password_source": "mysql:encrypted",
            "checked_at": "2026-07-06T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_ssh_validation.schedule_edge_ssh_catchup_background",
        lambda: scheduled.__setitem__("called", True),
    )

    out = svc.patch_edge_ssh_config(
        PatchEdgeSshConfigCommand(
            enabled=True,
            auto_after_export=True,
            host="10.10.11.26",
            user="linaro",
            password_secret_action="set",
            password="secret-pass",
        )
    )
    assert out.probe_status == "fail"
    assert out.probe_code == "auth_failed"
    assert out.auto_run_ready is False
    assert scheduled["called"] is False


def test_legacy_ssh_config_requires_a_readable_private_key_for_auto_run(monkeypatch):
    monkeypatch.setattr(svc.cfg_io, "resolve_edge_ssh_password", lambda _cfg: "")
    monkeypatch.setattr(
        svc.cfg_io,
        "resolve_edge_ssh_private_key_path_with_source",
        lambda _cfg: ("C:/missing/labelu-edge.key", "mysql:encrypted"),
    )

    out = svc._config_response(
        {
            "enabled": True,
            "auto_after_export": True,
            "private_key_path_configured": True,
            "private_key_path_runtime_available": True,
            "probe_status": "ok",
        }
    )

    assert out.private_key_path_configured is True
    assert out.private_key_path_runtime_available is False
    assert out.auto_run_ready is False


def test_probe_edge_ssh_config_persists_structured_failure(monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    cfg_io.save_edge_ssh_config(
        {
            "enabled": True,
            "host": "10.10.11.26",
            "user": "linaro",
            "password": "secret-pass",
            "_replace_password": True,
        }
    )
    monkeypatch.setattr(
        svc,
        "probe_edge_ssh_target",
        lambda _cfg=None: {
            "status": "fail",
            "stage": "auth",
            "code": "auth_failed",
            "message": "SSH 认证失败",
            "resolved_host": "10.10.11.26",
            "resolved_user": "linaro",
            "remote_base": "/data/labelu_edge/jobs",
            "password_source": "mysql:encrypted",
            "checked_at": "2026-07-06T00:00:00+00:00",
        },
    )

    probe = svc.probe_edge_ssh_config()
    assert probe.status == "fail"
    assert probe.stage == "auth"
    assert probe.code == "auth_failed"


def test_legacy_patch_is_rejected_while_device_is_processing(monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    current = {
        "device_id": "edge-live",
        "host": "10.10.11.26",
        "user": "linaro",
        "password": "secret-pass",
    }
    monkeypatch.setattr(svc.cfg_io, "load_edge_ssh_config", lambda: current)
    busy = LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=409,
        message="边端设备正在执行任务 #77，暂不能修改运行中设备的连接配置",
    )
    guard = lambda _device_id: (_ for _ in ()).throw(busy)
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.assert_no_active_job_for_legacy_ssh_config",
        guard,
    )
    probe = lambda *_args, **_kwargs: pytest.fail("busy patch must not probe SSH")
    monkeypatch.setattr(svc, "probe_edge_ssh_target", probe)

    with pytest.raises(LabelUException) as exc_info:
        svc.patch_edge_ssh_config(
            PatchEdgeSshConfigCommand(host="10.10.11.27")
        )

    assert exc_info.value.status_code == 409
    assert "#77" in str(exc_info.value)


def test_legacy_probe_is_rejected_while_device_is_processing(monkeypatch):
    from labelu.internal.common import config

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    monkeypatch.setattr(
        svc.cfg_io,
        "load_edge_ssh_config",
        lambda: {"device_id": "edge-live", "host": "10.10.11.26"},
    )
    busy = LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=503,
        message="无法确认边端任务状态，暂不能探测边端设备",
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.edge_device_registry.assert_no_active_job_for_legacy_ssh_config",
        lambda _device_id: (_ for _ in ()).throw(busy),
    )
    monkeypatch.setattr(
        svc,
        "probe_edge_ssh_target",
        lambda *_args, **_kwargs: pytest.fail("busy probe must not connect SSH"),
    )

    with pytest.raises(LabelUException) as exc_info:
        svc.probe_edge_ssh_config()

    assert exc_info.value.status_code == 503


def test_legacy_member_projection_contains_status_but_no_connection_metadata():
    response = EdgeSshConfigResponse(
        enabled=True,
        device_id="edge-live",
        device_name="边端盒子",
        host="10.10.11.26",
        port=22,
        user="linaro",
        password_configured=True,
        password_source_type="mysql:encrypted",
        password_masked_hint="****1234",
        password_runtime_available=True,
        edge_root="/data/labelu_edge",
        remote_base="/data/labelu_edge/jobs",
        sophon_demo_root="/opt/sophon-demo",
        probe_status="ok",
        probe_stage="remote_base",
        probe_code="ok",
        probe_message="host=10.10.11.26",
        auto_run_ready=True,
    )

    projected = svc.redact_edge_ssh_config_for_user(response)

    assert projected.enabled is True
    assert projected.device_id == "edge-live"
    assert projected.probe_status == "ok"
    assert projected.probe_stage == "remote_base"
    assert projected.host == ""
    assert projected.port == 0
    assert projected.user == ""
    assert projected.password_configured is False
    assert projected.password_masked_hint == ""
    assert projected.password_runtime_available is False
    assert projected.edge_root == ""
    assert projected.remote_base == ""
    assert projected.sophon_demo_root == ""
    assert projected.probe_code == ""
    assert projected.probe_message == ""
    assert projected.auto_run_ready is False
