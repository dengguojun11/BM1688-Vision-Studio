"""远程 YOLO 训练节点配置（页面 CRUD + 连通性测试）。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import status

from labelu.internal.application.command.remote_train_nodes import (
    PatchRemoteTrainNodeCommand,
    PutRemoteTrainNodesCommand,
    RemoteTrainNodeItemCommand,
)
from labelu.internal.application.response.remote_train_nodes import (
    RemoteTrainNodeResponse,
    RemoteTrainNodeTestResponse,
    RemoteTrainNodesListResponse,
)
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.remote_train_nodes_config import (
    config_file_path,
    get_node_by_id,
    load_remote_train_nodes_config,
    save_remote_train_nodes_config,
    update_node_test_result,
)
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.user import User

_DOC_HINT = (
    "每台远程 GPU 机的目录可不同：在节点中填写本机 python_path、agent_path、remote_base。"
    "主读写来源为 MySQL 治理表，remote_train_nodes.json 仅作兼容同步。"
    "先在节点上安装 venv+ultralytics+agent，再点「测试连接」通过后方可用于创建训练作业。"
)


def _assert_remote_train_admin(user: User) -> None:
    if not is_task_superuser(user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _node_to_view(node: Dict[str, Any]) -> RemoteTrainNodeResponse:
    return RemoteTrainNodeResponse(
        id=str(node.get("id") or ""),
        name=str(node.get("name") or ""),
        enabled=bool(node.get("enabled", True)),
        host=str(node.get("host") or ""),
        port=int(node.get("port") or 22),
        user=str(node.get("user") or "root"),
        password_configured=bool(str(node.get("password") or "").strip()),
        private_key_path_configured=bool(str(node.get("private_key_path") or "").strip()),
        remote_base=str(node.get("remote_base") or ""),
        python_path=str(node.get("python_path") or ""),
        agent_path=str(node.get("agent_path") or ""),
        weights_cache_dir=str(node.get("weights_cache_dir") or ""),
        cuda_devices=str(node.get("cuda_devices") or "0"),
        payload_download_host=str(node.get("payload_download_host") or ""),
        max_concurrent_jobs=int(node.get("max_concurrent_jobs") or 1),
        env_ready=bool(node.get("env_ready")),
        last_test_at=node.get("last_test_at"),
        last_test_message=node.get("last_test_message"),
        doc_hint=_DOC_HINT,
    )


def list_remote_train_nodes_view(
    *,
    env_ready_only: bool = False,
) -> RemoteTrainNodesListResponse:
    nodes = load_remote_train_nodes_config().get("nodes", [])
    views: List[RemoteTrainNodeResponse] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if env_ready_only and not node.get("enabled"):
            continue
        if env_ready_only and not node.get("env_ready"):
            continue
        views.append(_node_to_view(node))
    return RemoteTrainNodesListResponse(
        nodes=views,
        config_backend_label="MySQL 治理表（system_config_*）",
        legacy_sync_file=str(config_file_path().resolve()),
        doc_hint=_DOC_HINT,
    )


def get_remote_train_nodes_admin_view(user: User) -> RemoteTrainNodesListResponse:
    _assert_remote_train_admin(user)
    return list_remote_train_nodes_view(env_ready_only=False)


def put_remote_train_nodes(
    user: User,
    cmd: PutRemoteTrainNodesCommand,
) -> RemoteTrainNodesListResponse:
    _assert_remote_train_admin(user)
    existing = {
        str(n.get("id")): n
        for n in load_remote_train_nodes_config().get("nodes", [])
        if isinstance(n, dict) and n.get("id")
    }
    merged_nodes: List[Dict[str, Any]] = []
    for item in cmd.nodes:
        raw = item.dict(exclude_unset=False)
        node_id = str(raw.get("id") or "").strip()
        prev = existing.get(node_id) or {}
        if raw.get("clear_password"):
            raw["password"] = ""
        elif not str(raw.get("password") or "").strip():
            raw["password"] = prev.get("password") or ""
        if raw.get("clear_private_key_path"):
            raw["private_key_path"] = ""
        elif not str(raw.get("private_key_path") or "").strip():
            raw["private_key_path"] = prev.get("private_key_path") or ""
        if node_id in existing:
            raw.setdefault("env_ready", prev.get("env_ready"))
            raw.setdefault("last_test_at", prev.get("last_test_at"))
            raw.setdefault("last_test_message", prev.get("last_test_message"))
        else:
            raw.setdefault("env_ready", False)
        merged_nodes.append(raw)
    save_remote_train_nodes_config({"nodes": merged_nodes})
    return get_remote_train_nodes_admin_view(user)


def patch_remote_train_node(
    user: User,
    node_id: str,
    cmd: PatchRemoteTrainNodeCommand,
) -> RemoteTrainNodeResponse:
    _assert_remote_train_admin(user)
    cfg = load_remote_train_nodes_config()
    found: Optional[Dict[str, Any]] = None
    for node in cfg.get("nodes", []):
        if isinstance(node, dict) and str(node.get("id")) == node_id:
            found = node
            break
    if not found:
        raise LabelUException(
            code=ErrorCode.CODE_57031_YOLO_REMOTE_TRAIN_NODE_INVALID,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    patch = cmd.dict(exclude_unset=True)
    if patch.get("clear_password"):
        found["password"] = ""
        patch.pop("clear_password", None)
    elif "password" in patch and not str(patch.get("password") or "").strip():
        patch.pop("password", None)
    if patch.get("clear_private_key_path"):
        found["private_key_path"] = ""
        patch.pop("clear_private_key_path", None)
    elif "private_key_path" in patch and not str(patch.get("private_key_path") or "").strip():
        patch.pop("private_key_path", None)
    for key, val in patch.items():
        found[key] = val
    save_remote_train_nodes_config(cfg)
    refreshed = get_node_by_id(node_id)
    return _node_to_view(refreshed or found)


def _shell_quote(s: str) -> str:
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def test_remote_train_node(user: User, node_id: str) -> RemoteTrainNodeTestResponse:
    _assert_remote_train_admin(user)
    node = get_node_by_id(node_id)
    if not node:
        raise LabelUException(
            code=ErrorCode.CODE_57031_YOLO_REMOTE_TRAIN_NODE_INVALID,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not settings.YOLO_REMOTE_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57030_YOLO_REMOTE_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    password = str(node.get("password") or "").strip()
    private_key_path = str(node.get("private_key_path") or "").strip()

    from labelu.internal.common.edge_ssh_client import (
        EdgeSshError,
        ssh_connect_for_remote_train_node,
        ssh_run,
    )

    checks: List[str] = []
    ok = True
    messages: List[str] = []
    client = None
    if not password and not private_key_path:
        checks.append("auth: 未配置密码/私钥，将尝试平台机默认 ~/.ssh 密钥")
    try:
        client = ssh_connect_for_remote_train_node(node, timeout=30)
        checks.append("ssh: ok")

        code, out, err = ssh_run(client, "nvidia-smi -L 2>/dev/null | head -3", timeout=30)
        if code == 0 and out.strip():
            checks.append("gpu: " + out.strip().split("\n")[0][:120])
        else:
            ok = False
            messages.append("nvidia-smi 不可用或无 GPU")
            checks.append("gpu: fail")

        py = _shell_quote(str(node.get("python_path") or ""))
        code, out, err = ssh_run(
            client,
            f"{py} -c \"import torch, ultralytics; "
            f"print('cuda', torch.cuda.is_available(), 'devices', torch.cuda.device_count())\"",
            timeout=60,
        )
        if code == 0:
            checks.append("python: " + (out.strip() or "ok")[:160])
        else:
            ok = False
            messages.append("Python 环境检查失败: " + (err or out)[:200])
            checks.append("python: fail")

        for path_key, label in (
            ("remote_base", "remote_base"),
            ("agent_path", "agent_path"),
        ):
            path_val = str(node.get(path_key) or "").strip()
            if not path_val:
                if path_key == "agent_path":
                    messages.append("agent_path 未配置（节点 Agent 尚未安装时仍可先测 SSH/Python）")
                    checks.append("agent_path: skip")
                continue
            code, out, err = ssh_run(client, f"test -e {_shell_quote(path_val)} && echo OK", timeout=20)
            if code == 0 and "OK" in out:
                checks.append(f"{label}: ok")
            else:
                if path_key == "agent_path":
                    messages.append(f"agent_path 不存在: {path_val}")
                    checks.append(f"{label}: missing")
                else:
                    ok = False
                    messages.append(f"{label} 不存在或不可访问: {path_val}")
                    checks.append(f"{label}: fail")

        code, out, err = ssh_run(
            client,
            f"test -d {_shell_quote(str(node.get('remote_base') or ''))} && "
            f"test -w {_shell_quote(str(node.get('remote_base') or ''))} && echo WRITABLE",
            timeout=20,
        )
        if code == 0 and "WRITABLE" in out:
            checks.append("remote_base_writable: ok")
        elif str(node.get("remote_base") or "").strip():
            ok = False
            messages.append("remote_base 不可写")
            checks.append("remote_base_writable: fail")

        from labelu.internal.application.service.remote_yolo_train import (
            infer_payload_download_host_for_node,
            payload_download_base_url,
        )

        dl_host = payload_download_base_url(node=node)
        inferred = infer_payload_download_host_for_node(node)
        if str(node.get("payload_download_host") or "").strip():
            checks.append(f"payload_download: {dl_host}")
        elif inferred:
            checks.append(f"payload_download: auto {dl_host}")
        else:
            checks.append(f"payload_download: 无内网地址，将 SFTP 推送（公网 HTTP 拉取极慢）")

        from labelu.internal.application.service.remote_yolo_train import (
            should_use_http_pull_for_node,
        )

        mode = "HTTP 拉取" if should_use_http_pull_for_node(node) else "SFTP 推送"
        checks.append(f"payload_transfer: {mode}")

        agent_ok = any(c.startswith("agent_path: ok") for c in checks)
        env_ready = ok and agent_ok
        if ok and not agent_ok:
            messages.append("SSH/Python/GPU 正常，待安装 agent_worker 后可开训")

        message = "；".join(messages) if messages else "测试通过"
        update_node_test_result(
            node_id,
            ok=ok,
            message=message,
            env_ready=env_ready,
            tested_at=_iso_now(),
        )
        return RemoteTrainNodeTestResponse(
            node_id=node_id,
            ok=ok,
            message=message,
            checks=checks,
            env_ready=env_ready,
        )
    except EdgeSshError as exc:
        msg = str(exc)[:500]
        update_node_test_result(
            node_id,
            ok=False,
            message=msg,
            env_ready=False,
            tested_at=_iso_now(),
        )
        return RemoteTrainNodeTestResponse(
            node_id=node_id,
            ok=False,
            message=msg,
            checks=checks + ["ssh: fail"],
            env_ready=False,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def resolve_node_for_train(node_id: str) -> Dict[str, Any]:
    """创建远程训练作业时校验节点。"""
    if not settings.YOLO_REMOTE_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57030_YOLO_REMOTE_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    node = get_node_by_id(node_id)
    if not node or not node.get("enabled"):
        raise LabelUException(
            code=ErrorCode.CODE_57031_YOLO_REMOTE_TRAIN_NODE_INVALID,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not node.get("env_ready"):
        raise LabelUException(
            code=ErrorCode.CODE_57031_YOLO_REMOTE_TRAIN_NODE_INVALID,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return node
