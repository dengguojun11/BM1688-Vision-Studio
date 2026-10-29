"""边端 SSH 配置服务。"""
from __future__ import annotations

from pathlib import Path

from fastapi import status

from labelu.internal.application.command.edge_ssh_config import PatchEdgeSshConfigCommand
from labelu.internal.application.response.edge_ssh_config import (
    EdgeSshConfigResponse,
    EdgeSshProbeResponse,
)
from labelu.internal.common import edge_ssh_config as cfg_io
from labelu.internal.common.config import settings
from labelu.internal.common.edge_ssh_probe import apply_probe_result, probe_edge_ssh_target
from labelu.internal.common.error_code import ErrorCode, LabelUException

_DOC = (
    "部署机通过 SSH 连接边端盒子，上传导出产物并执行轻量验证。"
    "设备配置统一存储在 MySQL，密码和私钥路径加密保存且接口不会回传明文。"
)


def _assert_yolo_enabled() -> None:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def _apply_password_secret_action(current: dict, cmd: PatchEdgeSshConfigCommand) -> None:
    action = cmd.password_secret_action
    if action == "keep":
        return
    if action == "clear":
        from labelu.internal.common.edge_device_registry import update_device_record

        update_device_record(str(current.get("device_id") or ""), password_secret_action="clear")
        current.pop("password", None)
        # Keep the explicit clear intent through the compatibility facade's
        # second persistence pass.  Without this marker the facade would
        # update the SSH channel with the default ``keep`` action and reject
        # the now intentionally incomplete, fail-closed configuration.
        current["_password_secret_action"] = "clear"
        return
    password = str(cmd.password or "")
    if not password:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="password_secret_action=set 时必须提供 password",
        )
    current["password"] = password
    current["_replace_password"] = True


def _apply_private_key_path_secret_action(
    current: dict, cmd: PatchEdgeSshConfigCommand
) -> None:
    action = "clear" if cmd.clear_private_key_path is True else cmd.private_key_path_secret_action
    if action == "keep" and str(cmd.private_key_path or "").strip():
        action = "set"
    if action == "keep":
        return
    if action == "clear":
        from labelu.internal.common.edge_device_registry import update_device_record

        update_device_record(
            str(current.get("device_id") or ""),
            private_key_path_secret_action="clear",
        )
        current.pop("private_key_path", None)
        current["_private_key_path_secret_action"] = "clear"
        return
    private_key_path = str(cmd.private_key_path or "").strip()
    if not private_key_path:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="private_key_path_secret_action=set 时必须提供 private_key_path",
        )
    current["private_key_path"] = private_key_path
    current["_replace_private_key_path"] = True
    current["_private_key_path_secret_action"] = "set"


def _config_response(raw: dict) -> EdgeSshConfigResponse:
    probe_status = str(raw.get("probe_status") or "unknown")
    password_runtime_available = bool(cfg_io.resolve_edge_ssh_password(raw))
    private_key_path, private_key_source = cfg_io.resolve_edge_ssh_private_key_path_with_source(raw)
    private_key_path_runtime_available = bool(
        private_key_path and Path(private_key_path).expanduser().is_file()
    )
    return EdgeSshConfigResponse(
        enabled=bool(raw.get("enabled")),
        device_id=str(raw.get("device_id") or ""),
        device_name=str(raw.get("device_name") or ""),
        host=raw.get("host") or "",
        port=int(raw.get("port") or 22),
        user=raw.get("user") or "linaro",
        password_configured=bool(raw.get("password_is_configured")),
        password_source_type=str(raw.get("password_source_type") or ""),
        password_masked_hint=str(raw.get("password_masked_hint") or ""),
        password_updated_at=raw.get("password_updated_at"),
        password_runtime_available=password_runtime_available,
        private_key_path_source_type=str(
            raw.get("private_key_path_source_type") or private_key_source or ""
        ),
        private_key_path_masked=str(raw.get("private_key_path_masked") or ""),
        private_key_path_configured=bool(
            raw.get("private_key_path_configured") or private_key_path
        ),
        private_key_path_runtime_available=bool(
            private_key_path_runtime_available
        ),
        private_key_path_updated_at=raw.get("private_key_path_updated_at"),
        edge_root=raw.get("edge_root") or "/data/labelu_edge",
        remote_base=raw.get("remote_base") or "/data/labelu_edge/jobs",
        sophon_demo_root=raw.get("sophon_demo_root") or "",
        auto_after_export=bool(raw.get("auto_after_export")),
        run_coco_eval=bool(raw.get("run_coco_eval")),
        docker_container=str(raw.get("docker_container") or ""),
        docker_use_sudo=bool(raw.get("docker_use_sudo", True)),
        probe_status=probe_status,
        probe_stage=str(raw.get("probe_stage") or ""),
        probe_code=str(raw.get("probe_code") or ""),
        probe_message=str(raw.get("probe_message") or ""),
        probe_checked_at=raw.get("probe_checked_at"),
        last_validation_status=str(raw.get("last_validation_status") or ""),
        last_validation_at=raw.get("last_validation_at"),
        last_used_at=raw.get("last_used_at"),
        auto_run_ready=bool(
            raw.get("enabled")
            and raw.get("auto_after_export")
            and (password_runtime_available or private_key_path_runtime_available)
            and probe_status == "ok"
        ),
        doc_hint=_DOC,
    )


def redact_edge_ssh_config_for_user(
    response: EdgeSshConfigResponse,
) -> EdgeSshConfigResponse:
    """Return a status-only view for non-admin callers of the legacy API.

    The old endpoint predates the per-device projection and otherwise leaks the
    active device's host, account, path and password metadata.  Keep only the
    finite operational status fields needed by older pages; all connection and
    diagnostic details are blanked at the response boundary.
    """

    payload = (
        response.model_dump() if hasattr(response, "model_dump") else response.dict()
    )
    payload.update(
        {
            "host": "",
            "port": 0,
            "user": "",
            "password_configured": False,
            "password_source_type": "",
            "password_masked_hint": "",
            "password_updated_at": None,
            "password_runtime_available": False,
            "private_key_path_source_type": "",
            "private_key_path_masked": "",
            "private_key_path_configured": False,
            "private_key_path_runtime_available": False,
            "private_key_path_updated_at": None,
            "edge_root": "",
            "remote_base": "",
            "sophon_demo_root": "",
            "auto_after_export": False,
            "run_coco_eval": False,
            "docker_container": "",
            "docker_use_sudo": False,
            "probe_code": "",
            "probe_message": "",
            "probe_checked_at": None,
            "last_validation_status": "",
            "last_validation_at": None,
            "last_used_at": None,
            "auto_run_ready": False,
        }
    )
    return response.__class__(**payload)


def _legacy_active_device_id(config: dict) -> str:
    device_id = str(config.get("device_id") or "").strip()
    if device_id:
        return device_id
    from labelu.internal.common import edge_device_registry as registry_io

    return str(registry_io.get_active_device_id() or "").strip()


def _assert_legacy_runtime_mutation_allowed(config: dict) -> None:
    device_id = _legacy_active_device_id(config)
    if not device_id:
        return
    # Import lazily to avoid coupling the compatibility facade into the device
    # registry module's import graph.
    from labelu.internal.application.service import edge_device_registry

    edge_device_registry.assert_no_active_job_for_legacy_ssh_config(device_id)


def get_edge_ssh_config_view() -> EdgeSshConfigResponse:
    _assert_yolo_enabled()
    return _config_response(cfg_io.load_edge_ssh_config())


def probe_edge_ssh_config(*, persist: bool = True) -> EdgeSshProbeResponse:
    _assert_yolo_enabled()
    cur = cfg_io.load_edge_ssh_config()
    _assert_legacy_runtime_mutation_allowed(cur)
    probe = probe_edge_ssh_target(cur)
    if persist:
        apply_probe_result(cur, probe)
        cfg_io.save_edge_ssh_config(cur)
    return EdgeSshProbeResponse(**probe)


def patch_edge_ssh_config(cmd: PatchEdgeSshConfigCommand) -> EdgeSshConfigResponse:
    _assert_yolo_enabled()
    cur = cfg_io.load_edge_ssh_config()
    _assert_legacy_runtime_mutation_allowed(cur)
    if cmd.enabled is not None:
        cur["enabled"] = cmd.enabled
    if cmd.host is not None:
        cur["host"] = cmd.host.strip()
    if cmd.port is not None:
        cur["port"] = int(cmd.port)
    if cmd.user is not None:
        cur["user"] = cmd.user.strip()
    _apply_password_secret_action(cur, cmd)
    _apply_private_key_path_secret_action(cur, cmd)
    if cmd.edge_root is not None:
        cur["edge_root"] = cmd.edge_root.strip()
    if cmd.remote_base is not None:
        cur["remote_base"] = cmd.remote_base.strip()
    if cmd.sophon_demo_root is not None:
        cur["sophon_demo_root"] = cmd.sophon_demo_root.strip()
    if cmd.auto_after_export is not None:
        cur["auto_after_export"] = cmd.auto_after_export
    if cmd.run_coco_eval is not None:
        cur["run_coco_eval"] = cmd.run_coco_eval
    if cmd.docker_container is not None:
        cur["docker_container"] = str(cmd.docker_container).strip()
    if cmd.docker_use_sudo is not None:
        cur["docker_use_sudo"] = bool(cmd.docker_use_sudo)

    host = str(cur.get("host") or "").strip()
    user = str(cur.get("user") or "").strip()
    password = str(cfg_io.resolve_edge_ssh_password(cur) or "").strip()
    private_key_path = str(cfg_io.resolve_edge_ssh_private_key_path(cur) or "").strip()
    probe_result = None
    if host and user and (password or private_key_path):
        probe_result = probe_edge_ssh_target(cur)
        apply_probe_result(cur, probe_result)
        if probe_result.get("status") == "ok":
            if cmd.enabled is None:
                cur["enabled"] = True
            if cmd.auto_after_export is None:
                cur["auto_after_export"] = True
    else:
        apply_probe_result(
            cur,
            {
                "status": "fail",
                "stage": "credentials" if host and user else "config",
                "code": "credentials_missing" if host and user else "config_incomplete",
                "message": "运行时未解析到边端 SSH 密码或私钥路径"
                if host and user
                else "边端 SSH 配置缺少 host 或 user",
                "resolved_host": host,
                "resolved_user": user,
                "remote_base": str(cur.get("remote_base") or "/data/labelu_edge/jobs"),
                "password_source": cfg_io.resolve_edge_ssh_password_with_source(cur)[1],
                "private_key_path_source": cfg_io.resolve_edge_ssh_private_key_path_with_source(cur)[1],
                "checked_at": cfg_io._utc_now_iso(),
            },
        )

    cfg_io.save_edge_ssh_config(cur)
    view = _config_response(cfg_io.load_edge_ssh_config())
    if probe_result and probe_result.get("status") == "ok" and cfg_io.should_auto_run_edge_ssh(cur):
        from labelu.internal.application.service.edge_ssh_validation import (
            schedule_edge_ssh_catchup_background,
        )

        schedule_edge_ssh_catchup_background()
    return view
