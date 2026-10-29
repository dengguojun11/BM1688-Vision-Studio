from __future__ import annotations

from typing import Any, Dict

from labelu.internal.common import edge_ssh_config as cfg_io
from labelu.internal.common.edge_ssh_client import EdgeSshError, ssh_connect, ssh_run
from labelu.internal.common.edge_remote_paths import (
    edge_remote_paths,
    remote_edge_path_guard_command,
)

_WRITABLE_TOKEN = "__LABELU_EDGE_BASE_WRITABLE__"


def _shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _classify_connect_error(message: str) -> tuple[str, str]:
    text = str(message or "")
    lower = text.lower()
    if (
        "bad authentication type" in lower
        or "no authentication methods available" in lower
        or ("allowed types" in lower and "publickey" in lower)
    ):
        return "auth", "auth_mode_mismatch"
    if "authentication failed" in lower or "认证失败" in text:
        return "auth", "auth_failed"
    return "connect", "connect_failed"


def probe_edge_ssh_target(
    cfg: Dict[str, Any] | None = None,
    *,
    remote_base_required: bool = True,
) -> Dict[str, Any]:
    payload = dict(cfg or cfg_io.load_edge_ssh_config())
    host = str(payload.get("host") or "").strip()
    user = str(payload.get("user") or "").strip()
    port = int(payload.get("port") or 22)
    paths = edge_remote_paths(payload.get("edge_root"))
    remote_base = paths.jobs
    password, password_source = cfg_io.resolve_edge_ssh_password_with_source(payload)
    private_key_path, private_key_source = (
        cfg_io.resolve_edge_ssh_private_key_path_with_source(payload)
    )
    result: Dict[str, Any] = {
        "status": "fail",
        "stage": "config",
        "code": "config_incomplete",
        "message": "",
        "resolved_host": host,
        "resolved_user": user,
        "remote_base": remote_base,
        "password_source": password_source,
        "private_key_path_source": private_key_source,
        "checked_at": cfg_io._utc_now_iso(),
    }

    if not host or not user:
        result["message"] = "边端 SSH 配置缺少 host 或 user"
        return result
    if not password and not private_key_path:
        result.update(
            {
                "stage": "credentials",
                "code": "credentials_missing",
                "message": "当前设备没有可用的 SSH 密码或私钥路径，请在边端设备页面配置",
            }
        )
        return result

    client = None
    try:
        client = ssh_connect(
            host=host,
            port=port,
            username=user,
            password=password,
            private_key_path=private_key_path or None,
            timeout=int(payload.get("connect_timeout_sec") or 30),
        )
    except EdgeSshError as exc:
        stage, code = _classify_connect_error(str(exc))
        result.update(
            {
                "stage": stage,
                "code": code,
                "message": str(exc)[:800],
            }
        )
        return result

    try:
        code, out, err = ssh_run(client, "pwd && whoami", timeout=15)
        if code != 0:
            result.update(
                {
                    "stage": "remote_command",
                    "code": "remote_command_failed",
                    "message": ((err or out) or f"exit={code}")[:800],
                }
            )
            return result

        if remote_base_required:
            writable_cmd = (
                f"test ! -L {_shell_quote(paths.root)}; "
                f"mkdir -p {_shell_quote(paths.root)} 2>/dev/null || "
                f"sudo -n install -d -o \"$(id -u)\" -g \"$(id -g)\" "
                f"{_shell_quote(paths.root)}; "
                f"{remote_edge_path_guard_command(remote_base, root=paths.root)}; "
                f"mkdir -p {_shell_quote(remote_base)} 2>/dev/null || "
                f"sudo -n install -d -o \"$(id -u)\" -g \"$(id -g)\" "
                f"{_shell_quote(remote_base)}; "
                f"test -w {_shell_quote(remote_base)} && echo {_WRITABLE_TOKEN}"
            )
            base_code, base_out, base_err = ssh_run(client, writable_cmd, timeout=20)
            if base_code != 0 or _WRITABLE_TOKEN not in (base_out or ""):
                result.update(
                    {
                        "stage": "remote_base",
                        "code": "remote_base_not_writable",
                        "message": ((base_err or base_out) or f"remote_base not writable: {remote_base}")[
                            :800
                        ],
                    }
                )
                return result

        result.update(
            {
                "status": "ok",
                "stage": "remote_base" if remote_base_required else "remote_command",
                "code": "ok",
                "message": "SSH 认证成功，远端命令与工作目录写入检查通过",
            }
        )
        return result
    except EdgeSshError as exc:
        result.update(
            {
                "stage": "remote_command",
                "code": "remote_command_failed",
                "message": str(exc)[:800],
            }
        )
        return result
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def apply_probe_result(cfg: Dict[str, Any], probe: Dict[str, Any]) -> Dict[str, Any]:
    cfg["probe_status"] = str(probe.get("status") or "unknown")
    cfg["probe_stage"] = str(probe.get("stage") or "")
    cfg["probe_code"] = str(probe.get("code") or "")
    cfg["probe_message"] = str(probe.get("message") or "")[:800]
    cfg["probe_checked_at"] = probe.get("checked_at")
    return cfg


def probe_and_persist_edge_ssh_target(
    cfg: Dict[str, Any] | None = None,
    *,
    remote_base_required: bool = True,
) -> Dict[str, Any]:
    payload = dict(cfg or cfg_io.load_edge_ssh_config())
    probe = probe_edge_ssh_target(payload, remote_base_required=remote_base_required)
    apply_probe_result(payload, probe)
    cfg_io.save_edge_ssh_config(payload)
    return probe
