"""SSH bootstrap for the resident BM1688 Board Agent.

The existing environment provisioner prepares the BM1688 runtime, but it does
not install a long-running agent.  This module owns that last mile.  The
remote operation is deliberately split into deterministic, inspectable pieces:

* validate the device and server contract;
* render a versioned agent release, environment file and systemd unit;
* upload the release to a temporary directory;
* run a checksum-verified, idempotent install command; and
* wait for a real Board Agent heartbeat before reporting the device ready.

No HTTP route is registered here.  The device router can call
``start_bootstrap_background`` after an explicit operator confirmation and use
``get_bootstrap_status`` for polling.  Keeping the service route-neutral also
makes all command construction testable without an SSH connection.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import posixpath
import re
import shlex
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from loguru import logger

from labelu.internal.application.command.edge_agent_bootstrap import (
    BootstrapEdgeAgentCommand,
)
from labelu.internal.application.response.edge_agent_bootstrap import (
    EdgeAgentBootstrapResponse,
)
from labelu.internal.application.service import edge_runtime_provision
from labelu.internal.common import edge_device_registry as registry_io
from labelu.internal.common import edge_agent_control_plane
from labelu.internal.common import edge_ssh_client
from labelu.internal.common.config import settings
from labelu.internal.common.db import session_scope
from labelu.internal.common.edge_remote_paths import (
    edge_remote_paths,
    require_edge_path,
)
from labelu.internal.common.edge_script_paths import resolve_board_agent_script
from labelu.internal.domain.models.yolo_edge_validation_job import (
    YoloEdgeValidationJob,
)

BOOTSTRAP_PROTOCOL_VERSION = "labelu_board_agent_bootstrap_v1"
AGENT_RELEASE_FORMAT = "labelu_board_agent_release_v1"
AGENT_SERVICE_NAME = "labelu-board-agent.service"
AGENT_FILE_NAME = "labelu_board_agent.py"
DEFAULT_HEARTBEAT_TTL_SECONDS = 120

STATUS_NOT_STARTED = "NOT_STARTED"
STATUS_QUEUED = "QUEUED"
STATUS_PREFLIGHT = "PREFLIGHT"
STATUS_RUNTIME_PROVISION = "RUNTIME_PROVISION"
STATUS_UPLOADING = "UPLOADING"
STATUS_INSTALLING = "INSTALLING"
STATUS_STARTING = "STARTING"
STATUS_WAITING_HEARTBEAT = "WAITING_HEARTBEAT"
STATUS_READY = "READY"
STATUS_FAILED = "FAILED"

# A heartbeat timeout is terminal for the current installation attempt, but
# the resident service may come online a little later.  Keep these failure
# codes explicit so a late, authenticated, environment-ready heartbeat can
# close the same run without accepting an unrelated failed installation.
HEARTBEAT_RETRYABLE_FAILURE_CODES = frozenset(
    {"heartbeat_timeout", "environment_not_ready"}
)

# These states mean that the service owns the device while it is installing or
# validating the resident Agent.  The marker is persisted in the device
# registry so a fresh application process can resume the operation instead of
# treating the device as an ordinary SSH environment-check target.
ACTIVE_BOOTSTRAP_STATUSES = frozenset(
    {
        STATUS_QUEUED,
        STATUS_PREFLIGHT,
        STATUS_RUNTIME_PROVISION,
        STATUS_UPLOADING,
        STATUS_INSTALLING,
        STATUS_STARTING,
        STATUS_WAITING_HEARTBEAT,
    }
)

_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9_.:%-]+$")
_USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_DEVICE_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

_LOCKS_GUARD = threading.Lock()
_BOOTSTRAP_LOCKS: dict[str, threading.Lock] = {}
_STATE_LOCK = threading.RLock()
_STATE_CACHE: dict[str, dict[str, Any]] = {}
_BOOTSTRAP_PROCESS_LOCK_DIRNAME = ".edge_agent_bootstrap_locks"


@contextmanager
def _device_mutation_guard(device_id: str):
    """Hold the registry lock while claiming a device for bootstrap work.

    The scheduler uses the same host/process lock around its final readiness
    re-check and database claim.  Keeping the busy check, durable lifecycle
    marker, and pull-agent quiesce in one critical section closes the race in
    which a scheduler could claim a device after the check but before the
    ``QUEUED`` marker became visible.
    """

    try:
        lock_context = registry_io.device_mutation_lock()
    except Exception as exc:
        raise EdgeAgentBootstrapError(
            "无法取得边端设备变更锁，暂不能引导 Agent；请稍后重试",
            code="device_mutation_lock_unavailable",
            detail={"device_id": str(device_id or "")},
        ) from exc
    with lock_context as acquired:
        if not acquired:
            raise EdgeAgentBootstrapError(
                "边端设备正在执行其他变更，暂不能引导 Agent；请稍后重试",
                code="device_mutation_lock_unavailable",
                detail={"device_id": str(device_id or "")},
            )
        yield


class EdgeAgentBootstrapError(RuntimeError):
    """A safe, user-facing bootstrap failure with a stable error code."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "edge_agent_bootstrap_failed",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.detail = dict(detail or {})


@dataclass(frozen=True)
class EdgeAgentBootstrapArtifacts:
    """Rendered release files.  Secret-bearing environment text is private."""

    version: str
    artifact_sha256: str
    script: str
    environment: str
    systemd_unit: str
    manifest: str
    remote_root: str
    remote_version_root: str
    remote_staging_root: str
    service_user: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _safe_text(value: Any, *, limit: int = 800) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit]


def _redact(text: Any, *, secrets: tuple[str, ...] = ()) -> str:
    value = _safe_text(text, limit=3000)
    for secret in secrets:
        candidate = str(secret or "")
        if candidate:
            value = value.replace(candidate, "[REDACTED]")
    return value


def _device_lock(device_id: str) -> threading.Lock:
    key = str(device_id or "").strip()
    with _LOCKS_GUARD:
        return _BOOTSTRAP_LOCKS.setdefault(key, threading.Lock())


def _bootstrap_process_lock_path(device_id: str) -> Path:
    """Return a stable, non-secret lock-file path for one device."""

    digest = hashlib.sha256(str(device_id or "").encode("utf-8")).hexdigest()
    root = Path(str(getattr(settings, "BASE_DATA_DIR", "") or "."))
    return root / _BOOTSTRAP_PROCESS_LOCK_DIRNAME / f"{digest}.lock"


def _acquire_bootstrap_process_lock(device_id: str) -> object | None:
    """Acquire a cross-process lease for remote Agent installation.

    The lease is held for the lifetime of the background worker.  Unlike the
    registry mutation lock it is per-device and therefore does not serialize
    unrelated devices or long-running SSH operations.  OS-level release on
    process exit makes an interrupted worker recoverable on the next startup.
    """

    lock_file = None
    try:
        path = _bootstrap_process_lock_path(device_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = path.open("a+b")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_file
    except (ImportError, OSError, TypeError, ValueError):
        if lock_file is not None:
            try:
                lock_file.close()
            except (OSError, ValueError):
                pass
        return None


def _release_bootstrap_process_lock(lock_file: object | None) -> None:
    if lock_file is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except (ImportError, OSError, TypeError, ValueError):
        pass
    finally:
        try:
            lock_file.close()  # type: ignore[attr-defined]
        except (OSError, ValueError):
            pass


def _active_processing_job_id(device_id: str) -> int | None:
    """Return the first PROCESSING job currently occupying ``device_id``.

    Keep this query local to the bootstrap service instead of importing the
    device-registry service (which imports this module).  Legacy jobs without
    an explicit device use the registry's active-device affinity, matching the
    scheduler's existing busy mapping.
    """

    target_device_id = str(device_id or "").strip()
    if not target_device_id:
        return None
    active_device_id = str(registry_io.get_active_device_id() or "").strip()
    with session_scope() as db:
        rows = (
            db.query(YoloEdgeValidationJob.id, YoloEdgeValidationJob.edge_device_id)
            .filter(YoloEdgeValidationJob.status == "PROCESSING")
            .order_by(YoloEdgeValidationJob.id.asc())
            .all()
        )
    for job_id, assigned_device_id in rows:
        resolved_device_id = str(assigned_device_id or "").strip() or active_device_id
        if resolved_device_id == target_device_id:
            try:
                return int(job_id)
            except (TypeError, ValueError):
                continue
    return None


def _assert_device_not_busy(device_id: str) -> None:
    """Reject a reinstall that could restart an Agent serving a live job."""

    try:
        job_id = _active_processing_job_id(device_id)
    except EdgeAgentBootstrapError:
        raise
    except Exception as exc:
        raise EdgeAgentBootstrapError(
            "无法确认边端任务状态，暂不能重新引导 Agent",
            code="device_busy_check_failed",
        ) from exc
    if job_id is not None:
        raise EdgeAgentBootstrapError(
            f"边端设备正在执行任务 #{job_id}，暂不能重新引导 Agent；任务结束后再重试",
            code="device_busy",
            detail={"job_id": job_id},
        )


def _validate_device_id(value: str) -> str:
    device_id = str(value or "").strip()
    if not _DEVICE_RE.fullmatch(device_id):
        raise EdgeAgentBootstrapError(
            "设备 ID 只能包含字母、数字、点、下划线、冒号或短横线",
            code="invalid_device_id",
        )
    # The identifier is also used as one component of a POSIX staging path.
    # ``.`` and ``..`` pass the character allow-list but would normalize to
    # directory control segments on the remote host.
    if device_id in {".", ".."}:
        raise EdgeAgentBootstrapError(
            "设备 ID 不能使用路径特殊段 . 或 ..",
            code="invalid_device_id",
        )
    return device_id


def _validate_host(value: Any) -> str:
    host = str(value or "").strip()
    if not host or len(host) > 256 or any(ord(ch) < 32 for ch in host):
        raise EdgeAgentBootstrapError("边端 SSH 主机地址无效", code="invalid_host")
    # Accept IPv4/IPv6 literals and DNS names, but never shell/path syntax.
    if not _HOST_RE.fullmatch(host) or host.startswith(":") or host.endswith(":"):
        raise EdgeAgentBootstrapError(
            "边端 SSH 主机地址包含不安全字符", code="invalid_host"
        )
    return host


def _validate_user(value: Any) -> str:
    user = str(value or "").strip()
    if not _USER_RE.fullmatch(user):
        raise EdgeAgentBootstrapError("边端 SSH 用户名无效", code="invalid_user")
    return user


def _validate_version(value: Any) -> str:
    version = str(value or "").strip()
    if not version or not _VERSION_RE.fullmatch(version):
        raise EdgeAgentBootstrapError(
            "Agent 版本只能包含字母、数字、点、下划线或短横线",
            code="invalid_agent_version",
        )
    return version


def _normalize_base_url(value: Any) -> str:
    raw = str(value or "").replace("\r", "").replace("\n", "").strip().rstrip("/")
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise EdgeAgentBootstrapError(
            "Board Agent 回连地址的主机或端口无效",
            code="public_base_url_unsafe",
        ) from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EdgeAgentBootstrapError(
            "BOARD_AGENT_PUBLIC_BASE_URL 必须是 http 或 https 地址",
            code="public_base_url_missing",
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise EdgeAgentBootstrapError(
            "Board Agent 公网地址不得包含账号、密码、查询参数或片段",
            code="public_base_url_unsafe",
        )
    if any(ord(ch) < 32 for ch in raw):
        raise EdgeAgentBootstrapError(
            "Board Agent 公网地址包含控制字符",
            code="public_base_url_unsafe",
        )
    try:
        hostname = str(parsed.hostname or "").strip().rstrip(".").lower()
        # Accessing ``port`` validates malformed bracketed/numbered hosts and
        # keeps errors deterministic instead of leaking ValueError from
        # urllib.parse into the API layer.
        _ = parsed.port
    except ValueError as exc:
        raise EdgeAgentBootstrapError(
            "Board Agent 回连地址的主机或端口无效",
            code="public_base_url_unsafe",
        ) from exc
    if not hostname:
        raise EdgeAgentBootstrapError(
            "Board Agent 回连地址必须包含可访问主机名或 IP",
            code="public_base_url_missing",
        )
    blocked_names = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}
    blocked = hostname in blocked_names
    if not blocked:
        try:
            address = ipaddress.ip_address(hostname.split("%", 1)[0])
            blocked = bool(address.is_loopback or address.is_unspecified)
            if not blocked and getattr(address, "ipv4_mapped", None) is not None:
                mapped = address.ipv4_mapped
                blocked = bool(mapped and (mapped.is_loopback or mapped.is_unspecified))
        except ValueError:
            # DNS names are valid; their runtime resolution belongs to the
            # remote Agent's connectivity check rather than this formatter.
            pass
    if blocked:
        raise EdgeAgentBootstrapError(
            "Board Agent 回连地址不能使用 localhost、127.0.0.1、0.0.0.0 或 ::1；"
            "请配置边端可访问的 BOARD_AGENT_PUBLIC_BASE_URL 或 MEDIA_HOST",
            code="public_base_url_unreachable",
        )
    return raw


def _systemd_env_value(value: Any) -> str:
    raw = str(value or "")
    if any(ord(ch) < 32 for ch in raw):
        raise EdgeAgentBootstrapError(
            "Agent 环境变量包含控制字符", code="agent_config_unsafe"
        )
    # systemd EnvironmentFile accepts shell-like double quoted values.  Keep
    # the generated file deterministic and never use shell interpolation.
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _agent_token() -> str:
    token = edge_agent_control_plane.board_agent_token()
    if not token:
        raise EdgeAgentBootstrapError(
            "未配置 BOARD_AGENT_TOKEN，不能安装 Board Agent",
            code="board_agent_token_missing",
        )
    return token


def _public_base_url() -> str:
    configured = edge_agent_control_plane.public_base_url()
    if not configured:
        configured = str(getattr(settings, "MEDIA_HOST", "") or "").strip()
    if not configured:
        raise EdgeAgentBootstrapError(
            "未配置边端可访问的回连地址；请设置 BOARD_AGENT_PUBLIC_BASE_URL，"
            "或设置可被边端访问的 MEDIA_HOST",
            code="public_base_url_missing",
        )
    return _normalize_base_url(configured)


def _script_source() -> tuple[Path, str, str]:
    path = resolve_board_agent_script()
    if not path.is_file():
        raise EdgeAgentBootstrapError(
            f"未找到 Board Agent 程序包: {path}", code="agent_artifact_missing"
        )
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EdgeAgentBootstrapError(
            f"读取 Board Agent 程序包失败: {exc}", code="agent_artifact_unreadable"
        ) from exc
    if not source.strip():
        raise EdgeAgentBootstrapError(
            "Board Agent 程序包为空", code="agent_artifact_empty"
        )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return path, source, digest


def _safe_remote_roots(record: Mapping[str, Any]) -> tuple[str, str]:
    configured = str(record.get("edge_root") or "").strip()
    try:
        paths = edge_remote_paths(configured or None)
    except ValueError as exc:
        raise EdgeAgentBootstrapError(
            f"边端运行时目录不安全: {exc}", code="unsafe_remote_root"
        ) from exc
    agent_root = require_edge_path(posixpath.join(paths.root, "agent"), root=paths.root)
    return paths.root, agent_root


def validate_bootstrap_device(
    record: Mapping[str, Any] | None,
    *,
    base_url: str | None = None,
    token: str | None = None,
    allow_board_agent_channel: bool = False,
) -> dict[str, Any]:
    """Validate SSH credentials and the server contract without connecting.

    A device normally has to remain on the SSH channel until installation has
    completed.  ``allow_board_agent_channel`` is an internal restart-recovery
    escape hatch: the persisted lifecycle marker may have been written after
    the channel switched to ``board_agent`` but before the worker published its
    final READY state.  It is never enabled by the public request model.
    """

    if not isinstance(record, Mapping):
        raise EdgeAgentBootstrapError("边端设备不存在", code="device_not_found")
    device_id = _validate_device_id(str(record.get("id") or ""))
    if not bool(record.get("enabled", True)):
        raise EdgeAgentBootstrapError("边端设备已禁用", code="device_disabled")
    channel = str(record.get("validation_channel") or "ssh").strip().lower()
    if channel != "ssh" and not (
        allow_board_agent_channel and channel == "board_agent"
    ):
        raise EdgeAgentBootstrapError(
            "只有 SSH 验证通道支持自动安装 Board Agent",
            code="ssh_channel_required",
        )
    host = _validate_host(record.get("host"))
    user = _validate_user(record.get("user") or "linaro")
    try:
        port = int(record.get("port") or 22)
    except (TypeError, ValueError) as exc:
        raise EdgeAgentBootstrapError("SSH 端口无效", code="invalid_port") from exc
    if not 1 <= port <= 65535:
        raise EdgeAgentBootstrapError("SSH 端口超出范围", code="invalid_port")
    password = registry_io.resolve_device_password(dict(record))
    # Unit/integration adapters may provide an ephemeral password directly;
    # production registry records resolve the encrypted value above.  Never
    # persist this fallback or include it in a response.
    if not str(password or "").strip():
        password = str(record.get("password") or "")
    private_key_path = registry_io.resolve_device_private_key_path(dict(record))
    if not str(private_key_path or "").strip():
        private_key_path = str(record.get("private_key_path") or "")
    if not str(password or "").strip() and not str(private_key_path or "").strip():
        raise EdgeAgentBootstrapError(
            "SSH 通道缺少可用密码或密钥", code="ssh_credentials_missing"
        )
    remote_root, agent_root = _safe_remote_roots(record)
    resolved_token = str(token or _agent_token()).strip()
    if any(ord(ch) < 32 for ch in resolved_token):
        raise EdgeAgentBootstrapError(
            "BOARD_AGENT_TOKEN 包含控制字符", code="board_agent_token_unsafe"
        )
    resolved_base = _normalize_base_url(base_url or _public_base_url())
    paths = edge_remote_paths(remote_root)
    try:
        demo_root = require_edge_path(
            str(record.get("sophon_demo_root") or paths.sophon_demo_root),
            root=remote_root,
        )
    except ValueError as exc:
        raise EdgeAgentBootstrapError(
            f"SOPHON_DEMO_ROOT 不在受控边端目录内: {exc}",
            code="unsafe_demo_root",
        ) from exc
    return {
        "device_id": device_id,
        "host": host,
        "port": port,
        "user": user,
        "password": str(password),
        "private_key_path": str(private_key_path),
        "remote_root": remote_root,
        "agent_root": agent_root,
        "sophon_demo_root": demo_root,
        "base_url": resolved_base,
        "token": resolved_token,
        "docker_container": str(record.get("docker_container") or "bm1688").strip()
        or "bm1688",
        "connect_timeout_sec": max(1, int(record.get("connect_timeout_sec") or 30)),
        "validation_timeout_sec": max(
            1, int(record.get("validation_timeout_sec") or 7200)
        ),
    }


def _encode_shell_script(script: str) -> str:
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    # ``ssh.exec_command`` invokes the remote shell, but it does not execute
    # text that a pipeline merely decodes to stdout.  Pipe the decoded payload
    # into a POSIX shell so both the read-only preflight and the installer run
    # and return their real exit status/output.
    return f"printf %s {shlex.quote(encoded)} | base64 -d | sh"


def build_preflight_script(*, remote_root: str = "/data/labelu_edge") -> str:
    """Return the read-only remote preflight script body."""

    root = require_edge_path(remote_root)
    quoted_root = shlex.quote(root)
    return f"""set +e
echo LABELU_AGENT_PREFLIGHT_V1=1
command -v python3 >/dev/null 2>&1; [ $? -eq 0 ] && echo python3_ok=true || echo python3_ok=false
command -v systemctl >/dev/null 2>&1; [ $? -eq 0 ] && echo systemd_ok=true || echo systemd_ok=false
command -v sha256sum >/dev/null 2>&1; [ $? -eq 0 ] && echo sha256_ok=true || echo sha256_ok=false
if [ "$(id -u)" = 0 ] || (command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1); then echo privilege_ok=true; else echo privilege_ok=false; fi
if [ -d {quoted_root} ] && [ -w {quoted_root} ]; then echo root_writable=true; else echo root_writable=false; fi
    # ``systemctl`` may be installed in a container/chroot whose PID 1 is not
    # systemd.  The installer enables and restarts a host service, so require
    # an actual systemd runtime rather than only the client binary.
    if [ -d /run/systemd/system ] && [ "$(ps -p 1 -o comm= 2>/dev/null | tr -d ' ')" = "systemd" ]; then echo systemd_runtime=true; else echo systemd_runtime=false; fi
"""


def build_preflight_command(*, remote_root: str = "/data/labelu_edge") -> str:
    """Return a base64-wrapped command safe to pass to ``exec_command``."""

    return _encode_shell_script(build_preflight_script(remote_root=remote_root))


def parse_preflight_output(
    output: str, *, require_writable_root: bool = False
) -> dict[str, Any]:
    values: dict[str, str] = {}
    for line in str(output or "").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    if values.get("LABELU_AGENT_PREFLIGHT_V1") != "1":
        raise EdgeAgentBootstrapError(
            "边端预检返回格式无法识别", code="preflight_protocol_invalid"
        )
    bools = {
        key: values.get(key) == "true"
        for key in (
            "python3_ok",
            "systemd_ok",
            "sha256_ok",
            "privilege_ok",
            "root_writable",
            "systemd_runtime",
        )
    }
    # ``systemctl`` being present is not sufficient: containers and chroots
    # commonly ship the client binary without running systemd as PID 1.  The
    # install command enables and restarts a system service, so reject that
    # environment during the read-only preflight instead of failing later with
    # a less actionable systemd error.
    required = [
        "python3_ok",
        "systemd_ok",
        "systemd_runtime",
        "sha256_ok",
        "privilege_ok",
    ]
    if require_writable_root:
        required.append("root_writable")
    missing = [key for key in required if not bools[key]]
    if missing:
        raise EdgeAgentBootstrapError(
            "边端 Agent 预检未通过: " + ", ".join(missing),
            code="preflight_failed",
            detail={"missing": missing, "report": bools},
        )
    return {**bools, "format": BOOTSTRAP_PROTOCOL_VERSION}


def _build_environment_file(
    *,
    device_id: str,
    base_url: str,
    token: str,
    edge_root: str,
    demo_root: str,
    state_dir: str,
    run_coco_eval: bool,
    poll_interval_sec: int,
) -> str:
    values = {
        "LABELU_BASE_URL": base_url,
        "BOARD_AGENT_TOKEN": token,
        "BOARD_AGENT_ID": device_id,
        "BM1688_DEVICE_ID": device_id,
        "LABELU_EDGE_ROOT": edge_root,
        "SOPHON_DEMO_ROOT": demo_root,
        "LABELU_EDGE_EXECUTION_STATE_DIR": state_dir,
        "RUN_COCO_EVAL": "1" if run_coco_eval else "0",
        "POLL_INTERVAL_SEC": str(int(poll_interval_sec)),
    }
    return "# Generated by LabelU; do not edit.\n" + "".join(
        f"{key}={_systemd_env_value(value)}\n" for key, value in values.items()
    )


def build_systemd_unit(
    *,
    device_id: str,
    agent_root: str,
    service_user: str,
    remote_root: str | None = None,
) -> str:
    """Render an idempotent systemd unit pointing at the version symlink."""

    _validate_device_id(device_id)
    user = _validate_user(service_user)
    edge_root = edge_remote_paths(remote_root).root
    root = require_edge_path(agent_root, root=edge_root)
    current = require_edge_path(posixpath.join(root, "current"), root=root)
    script_path = require_edge_path(posixpath.join(current, AGENT_FILE_NAME), root=root)
    env_path = require_edge_path(posixpath.join(current, "agent.env"), root=root)
    state_dir = require_edge_path(posixpath.join(root, "state"), root=root)
    return f"""[Unit]
Description=LabelU BM1688 Board Agent ({device_id})
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={user}
WorkingDirectory={current}
EnvironmentFile=-{env_path}
ExecStart=/usr/bin/env python3 {script_path}
Restart=always
RestartSec=10
TimeoutStartSec=60
TimeoutStopSec=30
UMask=0077
ReadWritePaths={root} {state_dir}

[Install]
WantedBy=multi-user.target
"""


def _manifest_json(*, device_id: str, version: str, digest: str) -> str:
    return (
        json.dumps(
            {
                "format": AGENT_RELEASE_FORMAT,
                "protocol_version": BOOTSTRAP_PROTOCOL_VERSION,
                "device_id": device_id,
                "version": version,
                "artifact_sha256": digest,
                "artifact_name": AGENT_FILE_NAME,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


def build_agent_bootstrap_artifacts(
    record: Mapping[str, Any],
    command: BootstrapEdgeAgentCommand | None = None,
    *,
    base_url: str | None = None,
    token: str | None = None,
    allow_board_agent_channel: bool = False,
) -> EdgeAgentBootstrapArtifacts:
    """Build release files without opening SSH or writing remote state."""

    cmd = command or BootstrapEdgeAgentCommand(
        device_id=str(record.get("id") or ""), dry_run=True
    )
    cfg = validate_bootstrap_device(
        record,
        base_url=base_url,
        token=token,
        allow_board_agent_channel=allow_board_agent_channel,
    )
    _path, script, digest = _script_source()
    requested_version = str(cmd.agent_version or "").strip()
    version = (
        _validate_version(requested_version)
        if requested_version
        else f"board-agent-{digest[:12]}"
    )
    paths = edge_remote_paths(cfg["remote_root"])
    agent_root = cfg["agent_root"]
    version_root = require_edge_path(
        posixpath.join(agent_root, "versions", version), root=paths.root
    )
    staging_root = f"/tmp/labelu-board-agent/{cfg['device_id']}/{version}"
    if any(ord(ch) < 32 for ch in staging_root):
        raise EdgeAgentBootstrapError("Agent 临时目录无效", code="unsafe_staging_path")
    environment = _build_environment_file(
        device_id=cfg["device_id"],
        base_url=cfg["base_url"],
        token=cfg["token"],
        edge_root=cfg["remote_root"],
        demo_root=cfg["sophon_demo_root"],
        state_dir=require_edge_path(
            posixpath.join(agent_root, "state"), root=paths.root
        ),
        run_coco_eval=(
            bool(cmd.run_coco_eval)
            if cmd.run_coco_eval is not None
            else bool(record.get("run_coco_eval"))
        ),
        poll_interval_sec=int(cmd.poll_interval_sec),
    )
    unit = build_systemd_unit(
        device_id=cfg["device_id"],
        agent_root=agent_root,
        service_user=cfg["user"],
        remote_root=paths.root,
    )
    manifest = _manifest_json(
        device_id=cfg["device_id"], version=version, digest=digest
    )
    return EdgeAgentBootstrapArtifacts(
        version=version,
        artifact_sha256=digest,
        script=script,
        environment=environment,
        systemd_unit=unit,
        manifest=manifest,
        remote_root=paths.root,
        remote_version_root=version_root,
        remote_staging_root=staging_root,
        service_user=cfg["user"],
    )


def build_install_command(
    artifacts: EdgeAgentBootstrapArtifacts,
    *,
    service_name: str = AGENT_SERVICE_NAME,
) -> str:
    """Build the checksum-verified remote install command.

    The command contains no password or Board Agent token.  Those values are
    uploaded through the mode-0600 environment file and never echoed by the
    remote script.
    """

    staging = artifacts.remote_staging_root
    version_root = artifacts.remote_version_root
    agent_root = posixpath.dirname(posixpath.dirname(version_root))
    root = artifacts.remote_root
    for path in (staging, version_root, agent_root, root):
        if path.startswith("/tmp/"):
            continue
        require_edge_path(path, root=root)
    q = shlex.quote
    expected = artifacts.artifact_sha256
    service_user = _validate_user(artifacts.service_user)
    script = f"""set -eu
umask 077
STAGING={q(staging)}
VERSION_ROOT={q(version_root)}
AGENT_ROOT={q(agent_root)}
SERVICE={q(service_name)}
if [ "$(id -u)" = 0 ]; then SUDO=""; else SUDO="sudo -n"; fi
cleanup_staging() {{
  $SUDO rm -rf -- "$STAGING" || true
}}
trap cleanup_staging 0 1 2 3 15
test ! -L "$STAGING"
test ! -L "$STAGING/{AGENT_FILE_NAME}"
test ! -L "$STAGING/agent.env"
test ! -L "$STAGING/$SERVICE"
$SUDO chmod 700 "$STAGING"
test -f "$STAGING/{AGENT_FILE_NAME}"
test -f "$STAGING/agent.env"
test -f "$STAGING/$SERVICE"
test "$(sha256sum "$STAGING/{AGENT_FILE_NAME}" | awk '{{print $1}}')" = {q(expected)}
$SUDO install -d -m 0755 "$AGENT_ROOT/versions"
$SUDO install -d -m 0750 "$AGENT_ROOT/state"
$SUDO install -d -m 0755 "$VERSION_ROOT"
$SUDO install -m 0755 "$STAGING/{AGENT_FILE_NAME}" "$VERSION_ROOT/{AGENT_FILE_NAME}"
$SUDO install -m 0600 "$STAGING/agent.env" "$VERSION_ROOT/agent.env"
$SUDO install -m 0644 "$STAGING/manifest.json" "$VERSION_ROOT/manifest.json"
$SUDO install -m 0644 "$STAGING/$SERVICE" "/etc/systemd/system/$SERVICE"
$SUDO chown {q(service_user)} "$AGENT_ROOT/state"
$SUDO chown -R {q(service_user)} "$VERSION_ROOT"
$SUDO ln -sfn "$VERSION_ROOT" "$AGENT_ROOT/current"
$SUDO systemctl daemon-reload
$SUDO systemctl enable "$SERVICE"
$SUDO systemctl restart "$SERVICE"
echo LABELU_AGENT_INSTALL_V1=1
echo version={q(artifacts.version)}
echo artifact_sha256={q(expected)}
if ! $SUDO systemctl is-active --quiet "$SERVICE"; then
  echo systemd_active=false
  $SUDO systemctl --no-pager --full status "$SERVICE" 2>&1 | tail -n 80 || true
  exit 74
fi
echo systemd_active=true
"""
    return _encode_shell_script(script)


def _state_from_record(record: Mapping[str, Any] | None) -> dict[str, Any]:
    report = record.get("environment_report") if isinstance(record, Mapping) else None
    nested = report.get("agent_bootstrap") if isinstance(report, Mapping) else None
    raw = record.get("agent_bootstrap") if isinstance(record, Mapping) else None
    # Older records keep the snapshot below environment_report.  Merge it
    # first, then let the canonical top-level field override matching keys.
    # This also handles normalized records whose new field is still empty.
    merged: dict[str, Any] = {}
    if isinstance(nested, Mapping):
        merged.update(dict(nested))
    if isinstance(raw, Mapping):
        raw_state = dict(raw)
        # Some records created during the migration window contain an empty
        # top-level marker while the compatibility mirror still has the real
        # lifecycle status.  An empty canonical value must not hide that
        # durable nested snapshot.
        if not str(raw_state.get("status") or "").strip() and isinstance(
            nested, Mapping
        ):
            nested_status = str(nested.get("status") or "").strip()
            if nested_status:
                raw_state["status"] = nested_status
        merged.update(raw_state)
    return merged


def _state_updated_at(state: Mapping[str, Any] | None) -> datetime | None:
    """Parse a lifecycle snapshot timestamp for cross-process reconciliation."""

    if not isinstance(state, Mapping):
        return None
    raw = str(state.get("updated_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _merge_state_snapshots(
    persisted: Mapping[str, Any] | None,
    cached: Mapping[str, Any] | None,
    explicit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge lifecycle snapshots without letting an old process cache win.

    Every writer stores ``updated_at`` in the durable marker.  Sort snapshots
    by that timestamp and use source priority only for ties: an explicit
    in-call snapshot is newest, then the durable registry value, then the local
    cache.  A durable snapshot without a timestamp is still treated as the
    source of truth over a cached snapshot, which keeps old records from being
    accidentally hidden by a stale process-local lifecycle.
    """

    candidates: list[tuple[int, Mapping[str, Any]]] = []
    if isinstance(persisted, Mapping):
        candidates.append((20, persisted))
    if isinstance(cached, Mapping):
        candidates.append((10, cached))
    if isinstance(explicit, Mapping):
        candidates.append((30, explicit))

    def sort_key(item: tuple[int, Mapping[str, Any]]) -> tuple[datetime, int]:
        priority, snapshot = item
        timestamp = _state_updated_at(snapshot)
        if timestamp is None:
            # The durable registry is authoritative for legacy records whose
            # lifecycle marker predates ``updated_at``.  A missing timestamp in
            # the process cache, by contrast, should never displace a durable
            # value.  Explicit snapshots always carry a fresh timestamp when
            # created through ``_state_with_changes``.
            timestamp = (
                datetime.max.replace(tzinfo=timezone.utc)
                if priority in {20, 30}
                else datetime.min.replace(tzinfo=timezone.utc)
            )
        return timestamp, priority

    merged: dict[str, Any] = {}
    for _priority, snapshot in sorted(candidates, key=sort_key):
        merged.update(dict(snapshot))
    return merged


def persisted_bootstrap_status(record: Mapping[str, Any] | None) -> str:
    """Return the durable Agent lifecycle status from either record layout."""

    state = _state_from_record(record)
    return str(state.get("status") or "").strip().upper()


def bootstrap_in_progress(record: Mapping[str, Any] | None) -> bool:
    """Whether a persisted bootstrap currently owns the device."""

    if not isinstance(record, Mapping):
        return False
    if persisted_bootstrap_status(record) in ACTIVE_BOOTSTRAP_STATUSES:
        return True
    # Older records written by the channel transition may have the lifecycle
    # marker only in the flat environment fields.
    return (
        str(record.get("environment_code") or "").strip().lower()
        == "agent_bootstrap_in_progress"
    )


def _state_response(
    device_id: str,
    state: Mapping[str, Any] | None = None,
    *,
    dry_run: bool | None = None,
) -> EdgeAgentBootstrapResponse:
    record = registry_io.find_device_record(device_id)
    persisted = _state_from_record(record)
    with _STATE_LOCK:
        cached = dict(_STATE_CACHE.get(str(device_id), {}))
    merged = _merge_state_snapshots(persisted, cached, state)
    # Once another worker has written a newer durable snapshot, refresh this
    # process' cache so later responses cannot resurrect the old lifecycle.
    if state is None:
        persisted_at = _state_updated_at(persisted)
        cached_at = _state_updated_at(cached)
        if persisted and (
            persisted_at is None or cached_at is None or persisted_at >= cached_at
        ):
            with _STATE_LOCK:
                current_cached = _STATE_CACHE.get(str(device_id))
                if isinstance(current_cached, Mapping):
                    current_at = _state_updated_at(current_cached)
                    if (
                        persisted_at is None
                        or current_at is None
                        or persisted_at >= current_at
                    ):
                        _STATE_CACHE[str(device_id)] = dict(merged)
    board_status = str(
        (record or {}).get("board_agent_status")
        or merged.get("board_agent_status")
        or "unknown"
    )
    heartbeat = (record or {}).get("board_agent_last_heartbeat_at") or merged.get(
        "board_agent_last_heartbeat_at"
    )
    env_ready = bool(
        (record or {}).get("board_agent_environment_ready")
        if record is not None
        else merged.get("environment_ready")
    )
    return EdgeAgentBootstrapResponse(
        device_id=str(device_id),
        status=str(merged.get("status") or STATUS_NOT_STARTED),
        stage=str(merged.get("stage") or ""),
        code=str(merged.get("code") or ""),
        message=str(merged.get("message") or ""),
        agent_version=str(merged.get("agent_version") or ""),
        artifact_sha256=str(merged.get("artifact_sha256") or ""),
        service_name=str(merged.get("service_name") or AGENT_SERVICE_NAME),
        remote_root=str(merged.get("remote_root") or "/data/labelu_edge"),
        remote_version_root=str(merged.get("remote_version_root") or ""),
        environment_ready=env_ready,
        board_agent_status=board_status,
        board_agent_last_heartbeat_at=heartbeat,
        started_at=merged.get("started_at"),
        updated_at=merged.get("updated_at"),
        completed_at=merged.get("completed_at"),
        heartbeat_deadline_at=merged.get("heartbeat_deadline_at"),
        dry_run=bool(merged.get("dry_run") if dry_run is None else dry_run),
        detail=dict(merged.get("detail") or {}),
    )


def _state_with_changes(
    base: Mapping[str, Any] | None,
    changes: Mapping[str, Any],
    *,
    now: str,
    device_id: str,
) -> dict[str, Any]:
    """Apply one local lifecycle update to a reconciled state snapshot."""

    current = dict(base or {})
    for key, value in changes.items():
        # ``None`` is meaningful for lifecycle timestamps: a retry must clear
        # old completion/deadline data instead of inheriting it.
        if value is not None or key in {
            "started_at",
            "completed_at",
            "heartbeat_deadline_at",
        }:
            current[key] = value
    current.setdefault("device_id", str(device_id))
    current.setdefault("service_name", AGENT_SERVICE_NAME)
    current["updated_at"] = now
    return current


def _persist_state(
    device_id: str,
    *,
    require_durable: bool = False,
    registry_changes: Mapping[str, Any] | None = None,
    **changes: Any,
) -> EdgeAgentBootstrapResponse:
    """Persist non-secret state in the existing device JSON metadata.

    The top-level ``agent_bootstrap`` field is the canonical location.  Keep
    the same snapshot under ``environment_report.agent_bootstrap`` as a
    compatibility mirror for older readers and existing device-list payloads.
    Neither location contains SSH passwords or the Board Agent token.
    """

    now = _utc_iso()
    current: dict[str, Any] = {}
    try:
        lock_context = registry_io.device_mutation_lock()
    except Exception as exc:
        lock_context = None
        lock_error = exc
    else:
        lock_error = None

    if lock_context is None:
        # Keep an in-process snapshot for diagnostics, but never continue a
        # queueing operation that explicitly requires a durable marker.
        with _STATE_LOCK:
            current = _state_with_changes(
                _STATE_CACHE.get(str(device_id)),
                changes,
                now=now,
                device_id=device_id,
            )
            _STATE_CACHE[str(device_id)] = current
        if require_durable:
            raise EdgeAgentBootstrapError(
                "无法取得边端设备变更锁，已停止远端安装",
                code="device_mutation_lock_unavailable",
            ) from lock_error
    else:
        try:
            with lock_context as lock_acquired:
                if not lock_acquired:
                    with _STATE_LOCK:
                        current = _state_with_changes(
                            _STATE_CACHE.get(str(device_id)),
                            changes,
                            now=now,
                            device_id=device_id,
                        )
                        _STATE_CACHE[str(device_id)] = current
                    if require_durable:
                        raise EdgeAgentBootstrapError(
                            "边端设备正在执行其他变更，已停止远端安装",
                            code="device_mutation_lock_unavailable",
                        )
                else:
                    # Read and write the registry while holding the same lock
                    # used by scheduler claims.  This prevents an old local
                    # cache from overwriting a newer state from another
                    # process during a read/modify/write cycle.
                    record = registry_io.find_device_record(device_id)
                    persisted_state = _state_from_record(record)
                    with _STATE_LOCK:
                        current = _state_with_changes(
                            _merge_state_snapshots(
                                persisted_state,
                                _STATE_CACHE.get(str(device_id)),
                            ),
                            changes,
                            now=now,
                            device_id=device_id,
                        )
                        _STATE_CACHE[str(device_id)] = current
                    if record is not None:
                        report = dict(record.get("environment_report") or {})
                        report["agent_bootstrap"] = dict(current)
                        update_kwargs: dict[str, Any] = {
                            "agent_bootstrap": dict(current),
                            "environment_report": report,
                        }
                        if registry_changes:
                            # Some lifecycle transitions (notably heartbeat
                            # timeout finalization) must publish the
                            # scheduler-facing environment and bootstrap
                            # marker in one registry mutation.  Keep these
                            # fields explicit instead of storing them in the
                            # lifecycle snapshot itself.
                            update_kwargs.update(dict(registry_changes))
                        try:
                            persisted = registry_io.update_device_record(
                                device_id, **update_kwargs
                            )
                        except TypeError:
                            # Compatibility with older adapters/test doubles
                            # that only expose environment_report.
                            if require_durable and registry_changes:
                                raise EdgeAgentBootstrapError(
                                    "无法持久化 Agent 环境与引导状态，已停止远端安装",
                                    code="bootstrap_state_persist_failed",
                                )
                            if require_durable:
                                raise EdgeAgentBootstrapError(
                                    "无法持久化 Agent 引导状态，已停止远端安装",
                                    code="bootstrap_state_persist_failed",
                                )
                            persisted = registry_io.update_device_record(
                                device_id,
                                environment_report=report,
                            )
                        if require_durable:
                            persisted_state = _state_from_record(persisted)
                            if not isinstance(persisted, Mapping) or str(
                                persisted_state.get("status") or ""
                            ) != str(current.get("status") or ""):
                                # A storage adapter that silently drops the
                                # lifecycle marker is unsafe: another
                                # scheduler process could claim this device.
                                raise EdgeAgentBootstrapError(
                                    "无法确认 Agent 引导状态已持久化，已停止远端安装",
                                    code="bootstrap_state_persist_failed",
                                )
                    elif require_durable:
                        raise EdgeAgentBootstrapError(
                            "边端设备已不存在，无法持久化 Agent 引导状态",
                            code="bootstrap_state_persist_failed",
                        )
        except registry_io.DeviceMutationLockUnavailableError as exc:
            # Lock contention is transient.  Keep non-durable progress in the
            # in-process snapshot, but surface a stable retryable code when a
            # durable marker was required for safety.
            logger.info(
                "defer edge agent state persistence while mutation lock is busy device_id={} error={}",
                device_id,
                str(exc)[:200],
            )
            if require_durable:
                raise EdgeAgentBootstrapError(
                    "边端设备正在执行其他变更，暂不能持久化 Agent 状态；请稍后重试",
                    code="device_mutation_lock_unavailable",
                    detail={"device_id": str(device_id or "")},
                ) from exc
        except EdgeAgentBootstrapError:
            raise
        except Exception as exc:
            # State remains available in this process for ordinary progress
            # updates; the initial queue marker is fail-closed because
            # proceeding without a durable marker could race the scheduler.
            logger.exception(
                "persist edge agent bootstrap state failed device_id={}", device_id
            )
            if require_durable:
                raise EdgeAgentBootstrapError(
                    "无法持久化 Agent 引导状态，已停止远端安装",
                    code="bootstrap_state_persist_failed",
                ) from exc
    return _state_response(device_id, current)


def _bootstrap_failure_secrets(device_id: str) -> tuple[str, ...]:
    """Collect known secret values solely for failure-message redaction."""

    candidates: list[str] = []
    for setting_name in (
        "BOARD_AGENT_TOKEN",
        "BOARD_AGENT_TASK_TOKEN_SECRET",
        "SEVEN_GAI_AGENT_ENROLLMENT_TOKEN",
        "SEVEN_GAI_AGENT_OTA_SIGNING_KEY",
    ):
        value = getattr(settings, setting_name, "")
        if value:
            candidates.append(str(value))
    try:
        effective = edge_agent_control_plane.resolve_control_plane()
        candidates.extend(
            value
            for value in (
                effective.token,
                effective.task_token_secret,
                effective.enrollment_token,
            )
            if value
        )
    except Exception:
        pass

    try:
        record = registry_io.find_device_record(device_id)
    except Exception:
        record = None
    if isinstance(record, Mapping):
        for key in ("password", "password_encrypted"):
            value = record.get(key)
            if value:
                candidates.append(str(value))
        try:
            runtime_config = registry_io._record_to_runtime_config(dict(record))
        except Exception:
            runtime_config = {}
        for key in ("password", "token"):
            value = (
                runtime_config.get(key) if isinstance(runtime_config, Mapping) else None
            )
            if value:
                candidates.append(str(value))

    # Preserve order for deterministic tests while avoiding repeated replaces.
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def persist_bootstrap_failure(
    device_id: str,
    exc: Exception,
    *,
    stage: str = "validation",
) -> EdgeAgentBootstrapResponse:
    """Persist a synchronous bootstrap failure without exposing credentials.

    ``start_bootstrap_background`` intentionally performs artifact/config
    validation before starting its worker.  Callers need a durable FAILED
    state for those errors too, but exception ``detail`` may contain remote
    command output or future secret-bearing fields, so only a redacted message
    and stable code are persisted here.
    """

    secrets = _bootstrap_failure_secrets(device_id)
    code = (
        _redact(_safe_text(getattr(exc, "code", ""), limit=64), secrets=secrets)
        or "edge_agent_bootstrap_failed"
    )
    message = _redact(exc, secrets=secrets)[:800]
    if not message:
        message = "Agent 自动引导预检失败"
    safe_stage = _safe_text(stage, limit=64) or "validation"
    return _persist_state(
        device_id,
        status=STATUS_FAILED,
        stage=safe_stage,
        code=code,
        message=message,
        completed_at=_utc_iso(),
        heartbeat_deadline_at=None,
        detail={"source": "automatic_bootstrap", "synchronous": True},
    )


def get_bootstrap_status(device_id: str) -> EdgeAgentBootstrapResponse:
    """Read the latest provisioning state without probing or connecting."""

    normalized = _validate_device_id(device_id)
    if registry_io.find_device_record(normalized) is None:
        raise EdgeAgentBootstrapError("边端设备不存在", code="device_not_found")
    return _state_response(normalized)


def _run_remote_preflight(client: Any, *, remote_root: str) -> dict[str, Any]:
    command = build_preflight_command(remote_root=remote_root)
    try:
        code, out, err = edge_ssh_client.ssh_run(client, command, timeout=120)
    except Exception as exc:
        raise EdgeAgentBootstrapError(
            f"边端 Agent 预检命令执行失败: {_redact(exc)}",
            code="preflight_command_failed",
        ) from exc
    if int(code) != 0:
        raise EdgeAgentBootstrapError(
            f"边端 Agent 预检失败 (exit={code}): {_redact(err or out)}",
            code="preflight_command_failed",
        )
    return parse_preflight_output(out)


def _write_and_upload_artifacts(
    client: Any, artifacts: EdgeAgentBootstrapArtifacts
) -> None:
    with tempfile.TemporaryDirectory(prefix="labelu-agent-bootstrap-") as temp_dir:
        root = Path(temp_dir)
        files = {
            AGENT_FILE_NAME: artifacts.script,
            "agent.env": artifacts.environment,
            AGENT_SERVICE_NAME: artifacts.systemd_unit,
            "manifest.json": artifacts.manifest,
        }
        modes = {
            AGENT_FILE_NAME: 0o700,
            "agent.env": 0o600,
            AGENT_SERVICE_NAME: 0o600,
            "manifest.json": 0o600,
        }
        for name, content in files.items():
            local = root / name
            local.write_text(content, encoding="utf-8", newline="\n")
            local.chmod(modes[name])
            remote = posixpath.join(artifacts.remote_staging_root, name)
            edge_ssh_client.sftp_upload(
                client,
                local,
                remote,
                mode=modes[name],
            )


def _fresh_heartbeat(
    record: Mapping[str, Any],
    *,
    now: datetime | None = None,
    not_before: datetime | None = None,
) -> bool:
    status = str(record.get("board_agent_status") or "").strip().lower()
    if status != "online":
        return False
    raw = str(record.get("board_agent_last_heartbeat_at") or "").strip()
    if not raw:
        return False
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if not_before is not None:
        baseline = not_before
        if baseline.tzinfo is None:
            baseline = baseline.replace(tzinfo=timezone.utc)
        if parsed.astimezone(timezone.utc) <= baseline.astimezone(timezone.utc):
            return False
    ttl = max(
        10,
        int(
            getattr(
                settings,
                "BOARD_AGENT_HEARTBEAT_TTL_SECONDS",
                DEFAULT_HEARTBEAT_TTL_SECONDS,
            )
            or DEFAULT_HEARTBEAT_TTL_SECONDS
        ),
    )
    # Persisted timestamps are intentionally second-precision, but callers
    # that omit ``now`` may pass a heartbeat carrying microseconds (for
    # example the in-process Agent callback).  Use a full-precision clock for
    # this comparison so a same-second heartbeat is not misclassified as a
    # future timestamp merely because ``_utc_now`` truncates microseconds.
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)
    # A clock-skewed future heartbeat must not satisfy the liveness check.
    # Keep this lower bound aligned with the device-registry projection and
    # the frontend readiness helper.
    return timedelta(0) <= age <= timedelta(seconds=ttl)


def _wait_for_heartbeat(
    device_id: str,
    *,
    timeout_sec: int,
    poll_interval_sec: float,
    not_before: datetime | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bool:
    deadline = time.monotonic() + max(0, int(timeout_sec))
    while True:
        record = registry_io.find_device_record(device_id)
        if (
            record is not None
            and _fresh_heartbeat(record, not_before=not_before)
            and bool(record.get("board_agent_environment_ready"))
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        sleep_fn(min(float(poll_interval_sec), max(0.05, deadline - time.monotonic())))


def resume_persisted_bootstrap(device_id: str) -> EdgeAgentBootstrapResponse:
    """Resume one lifecycle run recovered from the device registry.

    The process may have stopped after the remote service was installed but
    before the final READY marker was written.  A fresh valid heartbeat is
    enough to finish that transition without reinstalling anything; all other
    active stages are replayed through the idempotent background installer.
    """

    normalized = _validate_device_id(device_id)
    with _device_mutation_guard(normalized):
        record = registry_io.find_device_record(normalized)
        if record is None:
            raise EdgeAgentBootstrapError("边端设备不存在", code="device_not_found")
        state = _state_from_record(record)
        status = str(state.get("status") or "").strip().upper()
        if status not in ACTIVE_BOOTSTRAP_STATUSES:
            return _state_response(normalized)

        channel = str(record.get("validation_channel") or "ssh").strip().lower()
        if (
            status == STATUS_WAITING_HEARTBEAT
            and _fresh_heartbeat(record)
            and bool(record.get("board_agent_environment_ready"))
        ):
            message = "Board Agent 心跳已确认，设备可进入任务调度池"
            _persist_agent_environment_state(
                normalized,
                ready=True,
                code="agent_ready",
                message=message,
            )
            return _persist_state(
                normalized,
                status=STATUS_READY,
                stage="heartbeat",
                code="ready",
                message=message,
                completed_at=_utc_iso(),
                heartbeat_deadline_at=None,
                environment_ready=True,
            )

        if channel == "board_agent":
            # Once installation has switched the durable channel, an operator
            # may have removed the bootstrap SSH secret. Never fall back to a
            # second SSH install in that state: keep waiting for the resident
            # Agent to report, or expose a clear reconfiguration failure for
            # earlier stages.
            try:
                password = registry_io.resolve_device_password(dict(record))
            except Exception:
                password = ""
            try:
                private_key_path = registry_io.resolve_device_private_key_path(dict(record))
            except Exception:
                private_key_path = ""
            if not (
                str(password or record.get("password") or "").strip()
                or str(private_key_path or record.get("private_key_path") or "").strip()
            ):
                if status in {STATUS_STARTING, STATUS_WAITING_HEARTBEAT}:
                    return _persist_state(
                        normalized,
                        status=STATUS_WAITING_HEARTBEAT,
                        stage="heartbeat",
                        code="waiting_heartbeat",
                        message="Board Agent 已安装，当前无 SSH 凭据，等待 Agent 心跳",
                    )
                return _persist_state(
                    normalized,
                    status=STATUS_FAILED,
                    stage="resume",
                    code="ssh_credentials_missing",
                    message="引导中断且当前无 SSH 凭据，补充连接配置后重新引导 Agent",
                    completed_at=_utc_iso(),
                )

        requested_version = str(state.get("agent_version") or "").strip() or None
    command = BootstrapEdgeAgentCommand(
        device_id=normalized,
        agent_version=requested_version,
        confirm_install=True,
        wait_for_heartbeat=True,
        provision_runtime=True,
        require_environment_ready=True,
    )
    return start_bootstrap_background(command, _resume=True)


def resume_persisted_bootstraps() -> int:
    """Recover all durable in-flight Agent runs after an app restart.

    A device with a PROCESSING validation job is deliberately left untouched;
    the worker can be retried by the next startup/manual action after the job
    releases the device.  Database/read failures also fail closed and leave
    the persisted marker intact for diagnosis.
    """

    resumed = 0
    for record in registry_io.list_device_records():
        if not isinstance(record, Mapping) or not bootstrap_in_progress(record):
            continue
        device_id = str(record.get("id") or "").strip()
        if not device_id:
            continue
        try:
            # Re-read and make the busy decision under the same registry lock
            # as scheduler claims.  The snapshot used by the startup scan may
            # already be stale by the time this device is resumed.
            with _device_mutation_guard(device_id):
                latest = registry_io.find_device_record(device_id)
                if not isinstance(latest, Mapping) or not bootstrap_in_progress(latest):
                    continue
                active_job_id = _active_processing_job_id(device_id)
                if active_job_id is not None:
                    logger.info(
                        "skip persisted edge agent bootstrap recovery for busy device_id={} job_id={}",
                        device_id,
                        active_job_id,
                    )
                    continue
                resume_persisted_bootstrap(device_id)
            resumed += 1
        except EdgeAgentBootstrapError as exc:
            if exc.code in {
                "device_busy",
                "device_busy_check_failed",
                "device_mutation_lock_unavailable",
            }:
                logger.info(
                    "defer persisted edge agent bootstrap recovery device_id={} code={}",
                    device_id,
                    exc.code,
                )
            else:
                logger.warning(
                    "persisted edge agent bootstrap recovery failed device_id={} code={} message={}",
                    device_id,
                    exc.code,
                    str(exc)[:300],
                )
        except Exception:
            logger.exception(
                "persisted edge agent bootstrap recovery failed device_id={}",
                device_id,
            )
    return resumed


def _activate_board_agent_channel(device_id: str) -> None:
    """Promote an installed Agent to the unified pull channel.

    The channel is switched immediately after systemd reports the service
    active, because the heartbeat endpoint intentionally rejects unregistered
    SSH-channel devices.  The device is explicitly marked offline and its
    environment as not ready; scheduler eligibility therefore remains false
    until a post-install heartbeat is accepted.
    """

    record = registry_io.find_device_record(device_id)
    if record is None:
        raise EdgeAgentBootstrapError("边端设备不存在", code="device_not_found")
    try:
        registry_io.update_device_record(
            device_id,
            validation_channel="board_agent",
            preserve_password_on_board_agent=True,
            preserve_private_key_on_board_agent=True,
            board_agent_status="offline",
            board_agent_environment_ready=False,
            board_agent_message="Board Agent 已安装，等待首次有效心跳",
            environment_status="checking",
            environment_stage="agent_heartbeat",
            environment_code="waiting_heartbeat",
            environment_message="Board Agent 已安装，等待首次有效心跳",
            environment_checked_at=_utc_iso(),
        )
    except registry_io.DeviceMutationLockUnavailableError as exc:
        raise EdgeAgentBootstrapError(
            "边端设备状态正在更新，暂不能切换板端主动拉取通道；请稍后重试",
            code="device_mutation_lock_unavailable",
            detail={"device_id": str(device_id or "")},
        ) from exc
    except ValueError as exc:
        raise EdgeAgentBootstrapError(
            f"切换板端主动拉取通道失败: {exc}",
            code="channel_activation_failed",
        ) from exc


def _quiesce_existing_board_agent(device_id: str) -> None:
    """Remove an already-installed Agent from scheduling during rebootstrap.

    A reinstall restarts the resident service and invalidates the old
    heartbeat.  Marking a pull-channel device offline before SSH work starts
    prevents the scheduler from assigning jobs while the old process is being
    replaced.  SSH-channel devices are left untouched until installation
    succeeds, preserving the normal failure/retry path for first enrollment.
    """

    record = registry_io.find_device_record(device_id)
    if record is None:
        raise EdgeAgentBootstrapError("边端设备不存在", code="device_not_found")
    if str(record.get("validation_channel") or "ssh").strip().lower() != "board_agent":
        return
    if (
        str(record.get("board_agent_status") or "").strip().lower() == "offline"
        and not bool(record.get("board_agent_environment_ready"))
        and str(record.get("environment_code") or "") == "agent_bootstrap_in_progress"
    ):
        return
    try:
        registry_io.update_device_record(
            device_id,
            validation_channel="board_agent",
            preserve_password_on_board_agent=True,
            preserve_private_key_on_board_agent=True,
            board_agent_status="offline",
            board_agent_environment_ready=False,
            board_agent_message="Board Agent 正在重新引导，设备暂不可调度",
            environment_status="checking",
            environment_stage="agent_bootstrap",
            environment_code="agent_bootstrap_in_progress",
            environment_message="Board Agent 正在重新引导，设备暂不可调度",
            environment_checked_at=_utc_iso(),
        )
    except registry_io.DeviceMutationLockUnavailableError as exc:
        raise EdgeAgentBootstrapError(
            "边端设备状态正在更新，暂不能暂停现有 Board Agent；请稍后重试",
            code="device_mutation_lock_unavailable",
            detail={"device_id": str(device_id or "")},
        ) from exc
    except ValueError as exc:
        raise EdgeAgentBootstrapError(
            f"暂停现有 Board Agent 调度状态失败: {exc}",
            code="channel_state_persist_failed",
        ) from exc


def _persist_agent_environment_state(
    device_id: str,
    *,
    ready: bool,
    code: str,
    message: str,
    online: bool | None = None,
) -> None:
    """Persist the scheduler-facing environment summary after heartbeat wait."""

    try:
        registry_io.update_device_record(
            device_id,
            validation_channel="board_agent",
            password_secret_action="clear" if ready else "keep",
            private_key_path_secret_action="clear" if ready else "keep",
            preserve_password_on_board_agent=not ready,
            preserve_private_key_on_board_agent=not ready,
            board_agent_environment_ready=bool(ready),
            board_agent_status=(
                "online"
                if (bool(online) if online is not None else bool(ready))
                else "offline"
            ),
            board_agent_message=message,
            environment_status="ready" if ready else "failed",
            environment_stage="agent_heartbeat",
            environment_code=code,
            environment_message=message,
            environment_checked_at=_utc_iso(),
        )
    except registry_io.DeviceMutationLockUnavailableError as exc:
        raise EdgeAgentBootstrapError(
            "边端设备状态正在更新，暂不能保存 Board Agent 环境状态；请稍后重试",
            code="device_mutation_lock_unavailable",
            detail={"device_id": str(device_id or "")},
        ) from exc
    except ValueError as exc:
        raise EdgeAgentBootstrapError(
            f"保存 Board Agent 环境状态失败: {exc}",
            code="channel_state_persist_failed",
        ) from exc


def _finalize_heartbeat_wait(
    device_id: str,
    *,
    heartbeat_not_before: datetime,
    common_detail: Mapping[str, Any],
) -> EdgeAgentBootstrapResponse:
    """Close the bounded heartbeat wait under one registry mutation lock.

    The timeout worker used to persist the scheduler-facing environment and
    the lifecycle marker in two independent writes.  A heartbeat could land
    between those writes, complete the run as ``READY``, and then be replaced
    by the worker's stale ``FAILED`` marker.  Re-read the device while holding
    the same mutation lock used by heartbeat ingestion, then publish both
    views in one ``update_device_record`` call.  A heartbeat that arrives
    after this critical section will observe the retryable timeout marker and
    can still complete the transition through ``record_board_agent_heartbeat``.
    """

    with _device_mutation_guard(device_id):
        latest_record = registry_io.find_device_record(device_id)
        if latest_record is None:
            raise EdgeAgentBootstrapError("边端设备不存在", code="device_not_found")

        latest_state = _state_from_record(latest_record)
        latest_status = str(latest_state.get("status") or "").strip().upper()
        # An operator retry or a heartbeat callback may have advanced the run
        # while the timeout worker was unwinding.  Never let that old worker
        # overwrite a newer lifecycle marker.
        if latest_status not in {STATUS_STARTING, STATUS_WAITING_HEARTBEAT}:
            return _state_response(device_id)

        heartbeat_seen = _fresh_heartbeat(
            latest_record,
            not_before=heartbeat_not_before,
        )
        environment_ready = bool(
            heartbeat_seen and latest_record.get("board_agent_environment_ready")
        )
        checked_at = _utc_iso()
        if environment_ready:
            message = "Board Agent 心跳已确认，设备可进入任务调度池"
            return _persist_state(
                device_id,
                require_durable=True,
                registry_changes={
                    "validation_channel": "board_agent",
                    "password_secret_action": "clear",
                    "private_key_path_secret_action": "clear",
                    "preserve_password_on_board_agent": False,
                    "preserve_private_key_on_board_agent": False,
                    "board_agent_environment_ready": True,
                    "board_agent_status": "online",
                    "board_agent_message": message,
                    "environment_status": "ready",
                    "environment_stage": "agent_heartbeat",
                    "environment_code": "agent_ready",
                    "environment_message": message,
                    "environment_checked_at": checked_at,
                },
                status=STATUS_READY,
                stage="heartbeat",
                code="ready",
                message=message,
                completed_at=checked_at,
                heartbeat_deadline_at=None,
                environment_ready=True,
                detail=dict(common_detail),
            )

        timeout_code = (
            "environment_not_ready" if heartbeat_seen else "heartbeat_timeout"
        )
        timeout_message = (
            "Agent 已上报心跳，但 BM1688 运行环境仍未就绪；设备保持不可调度"
            if heartbeat_seen
            else "Agent 已安装但尚未收到新鲜心跳；设备保持不可调度"
        )
        return _persist_state(
            device_id,
            require_durable=True,
            registry_changes={
                "validation_channel": "board_agent",
                "password_secret_action": "keep",
                "private_key_path_secret_action": "keep",
                "preserve_password_on_board_agent": True,
                "preserve_private_key_on_board_agent": True,
                "board_agent_environment_ready": False,
                "board_agent_status": "online" if heartbeat_seen else "offline",
                "board_agent_message": timeout_message,
                "environment_status": "failed",
                "environment_stage": "agent_heartbeat",
                "environment_code": timeout_code,
                "environment_message": timeout_message,
                "environment_checked_at": checked_at,
            },
            status=STATUS_FAILED,
            stage="heartbeat",
            code=timeout_code,
            message=timeout_message,
            completed_at=checked_at,
            heartbeat_deadline_at=None,
            environment_ready=False,
            detail={
                **dict(common_detail),
                "retryable": True,
                "heartbeat_seen": heartbeat_seen,
            },
        )


def _runtime_prepare(
    client: Any,
    cfg: Mapping[str, Any],
    command: BootstrapEdgeAgentCommand,
) -> tuple[Any, dict[str, Any]]:
    """Reuse the existing runtime probe/provision contract."""

    try:
        report = edge_runtime_provision.probe_edge_runtime(
            client,
            configured_demo_root=str(cfg.get("sophon_demo_root") or ""),
            docker_container=str(cfg.get("docker_container") or "bm1688"),
            remote_root=str(cfg.get("remote_root") or ""),
        )
    except TypeError:
        # Compatibility with older test doubles / deployments that predate the
        # optional remote_root argument.
        report = edge_runtime_provision.probe_edge_runtime(
            client,
            configured_demo_root=str(cfg.get("sophon_demo_root") or ""),
            docker_container=str(cfg.get("docker_container") or "bm1688"),
        )
    if bool(report.get("environment_ready")) or not command.require_environment_ready:
        return client, report
    if not command.provision_runtime:
        raise EdgeAgentBootstrapError(
            "BM1688 运行环境尚未就绪，未执行运行时安装",
            code="environment_not_ready",
            detail={"report": report},
        )

    def reconnect() -> Any:
        return edge_ssh_client.ssh_connect(
            host=str(cfg["host"]),
            port=int(cfg["port"]),
            username=str(cfg["user"]),
            password=str(cfg["password"]),
            private_key_path=str(cfg.get("private_key_path") or "") or None,
            timeout=int(cfg.get("connect_timeout_sec") or 30),
        )

    try:
        client, result = edge_runtime_provision.ensure_edge_runtime(
            client,
            configured_demo_root=str(cfg.get("sophon_demo_root") or ""),
            docker_container=str(cfg.get("docker_container") or "bm1688"),
            reconnect=reconnect,
        )
    except Exception as exc:
        raise EdgeAgentBootstrapError(
            f"BM1688 运行时自动准备失败: {_redact(exc)}",
            code=str(
                getattr(exc, "code", "runtime_provision_failed")
                or "runtime_provision_failed"
            ),
            detail={"report": getattr(exc, "report", {}) or {}},
        ) from exc
    after = result.get("after") if isinstance(result, Mapping) else {}
    if command.require_environment_ready and not bool(
        isinstance(after, Mapping) and after.get("environment_ready")
    ):
        raise EdgeAgentBootstrapError(
            "BM1688 运行时安装后复检仍未就绪",
            code="environment_verification_failed",
            detail={"report": after or result},
        )
    return client, dict(after or result or {})


def bootstrap_edge_agent(
    command: BootstrapEdgeAgentCommand,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    _held_lock: threading.Lock | None = None,
    _held_process_lock: object | None = None,
    _allow_board_agent_channel: bool = False,
) -> EdgeAgentBootstrapResponse:
    """Synchronously bootstrap one SSH device.

    Routes should normally call ``start_bootstrap_background`` because runtime
    installation and artifact upload can take minutes.  This synchronous entry
    point is intentional for workers and deterministic tests.
    """

    # Validation and artifact rendering happen before the main try/except
    # below because they must not open SSH. A background worker has already
    # persisted QUEUED before entering this function, so capture those early
    # failures or a changed record can leave the device stuck in QUEUED. A
    # synchronous caller has not queued anything and therefore keeps the
    # existing exception-only behavior.
    device_id = str(command.device_id or "").strip()
    try:
        device_id = _validate_device_id(device_id)
        record = registry_io.find_device_record(device_id)
        if _allow_board_agent_channel:
            cfg = validate_bootstrap_device(
                record,
                allow_board_agent_channel=True,
            )
            artifacts = build_agent_bootstrap_artifacts(
                record,
                command,
                allow_board_agent_channel=True,
            )
        else:
            cfg = validate_bootstrap_device(record)
            artifacts = build_agent_bootstrap_artifacts(record, command)
    except Exception as exc:
        if _held_lock is not None and _DEVICE_RE.fullmatch(device_id):
            try:
                persist_bootstrap_failure(device_id, exc, stage="validation")
            except Exception:
                logger.exception(
                    "persist worker edge agent bootstrap validation failure failed device_id={}",
                    device_id,
                )
        raise
    common_detail = {
        "protocol_version": BOOTSTRAP_PROTOCOL_VERSION,
        "artifact_name": AGENT_FILE_NAME,
        "remote_staging_root": artifacts.remote_staging_root,
    }
    if command.dry_run:
        # A preview is still a registry write (it stores the rendered
        # artifact metadata), so its active-run check must share the same
        # mutation lock as lifecycle updates.  Otherwise a direct synchronous
        # caller can read an idle record, race a worker that publishes
        # ``QUEUED``/``INSTALLING``, and overwrite that active marker with
        # ``PREVIEW``.
        with _device_mutation_guard(device_id):
            latest_record = registry_io.find_device_record(device_id)
            if bootstrap_in_progress(latest_record):
                return get_bootstrap_status(device_id)
            return _persist_state(
                device_id,
                status="PREVIEW",
                stage="artifact_render",
                code="dry_run",
                message="已生成 Agent 版本化安装清单；未连接边端或写入远端",
                agent_version=artifacts.version,
                artifact_sha256=artifacts.artifact_sha256,
                remote_root=artifacts.remote_root,
                remote_version_root=artifacts.remote_version_root,
                dry_run=True,
                detail={
                    **common_detail,
                    "remote_install_command": build_install_command(artifacts),
                },
            )
    if not command.confirm_install:
        raise EdgeAgentBootstrapError(
            "安装常驻 Board Agent 前必须显式确认",
            code="confirmation_required",
            detail={
                "artifact_sha256": artifacts.artifact_sha256,
                "version": artifacts.version,
            },
        )

    lock = _held_lock or _device_lock(device_id)
    owns_lock = _held_lock is None
    if owns_lock and not lock.acquire(blocking=False):
        return get_bootstrap_status(device_id)
    # ``start_bootstrap_background`` already owns a cross-process lease and
    # passes it through.  Direct synchronous callers have no such outer
    # owner, so acquire a lease for the full SSH/install lifetime as well.
    process_lock_file = _held_process_lock
    owns_process_lock = process_lock_file is None
    if owns_process_lock:
        process_lock_file = _acquire_bootstrap_process_lock(device_id)
        if process_lock_file is None:
            if owns_lock:
                lock.release()
            raise EdgeAgentBootstrapError(
                "该设备正在其他进程执行 Agent 引导，请稍后重试",
                code="bootstrap_in_progress",
                detail={"device_id": device_id},
            )
    try:
        # Re-check and publish the lifecycle marker under the same registry
        # lock used by the scheduler's final readiness/claim step.  This makes
        # the busy check and marker write one atomic ownership transition.
        with _device_mutation_guard(device_id):
            # The background worker can start after a task becomes PROCESSING;
            # that late transition must still prevent quiescing the Agent.
            _assert_device_not_busy(device_id)
            if owns_lock:
                started_at = _utc_iso()
                _persist_state(
                    device_id,
                    require_durable=True,
                    status=STATUS_QUEUED,
                    stage="queue",
                    code="queued",
                    message="Agent 自动引导已排队",
                    agent_version=artifacts.version,
                    artifact_sha256=artifacts.artifact_sha256,
                    remote_root=artifacts.remote_root,
                    remote_version_root=artifacts.remote_version_root,
                    started_at=started_at,
                    completed_at=None,
                    heartbeat_deadline_at=None,
                    dry_run=False,
                    detail=common_detail,
                )
            # The persisted QUEUED marker must be visible before quiescing or
            # touching the remote host. The scheduler reads it and excludes
            # this device from concurrent dispatch passes, including workers
            # in other processes on this host.
            _quiesce_existing_board_agent(device_id)
    except Exception:
        if owns_process_lock:
            _release_bootstrap_process_lock(process_lock_file)
        if owns_lock:
            lock.release()
        raise
    client = None
    try:
        _persist_state(
            device_id,
            status=STATUS_PREFLIGHT,
            stage="ssh_preflight",
            code="running",
            message="正在执行 SSH 权限、Python、systemd 和目录预检",
        )
        client = edge_ssh_client.ssh_connect(
            host=cfg["host"],
            port=cfg["port"],
            username=cfg["user"],
            password=cfg["password"],
            private_key_path=str(cfg.get("private_key_path") or "") or None,
            timeout=cfg["connect_timeout_sec"],
        )
        preflight = _run_remote_preflight(client, remote_root=cfg["remote_root"])
        _persist_state(
            device_id,
            stage="ssh_preflight",
            code="ok",
            message="SSH 预检通过",
            detail={**common_detail, "preflight": preflight},
        )

        if command.require_environment_ready:
            _persist_state(
                device_id,
                status=STATUS_RUNTIME_PROVISION,
                stage="environment_prepare",
                code="running",
                message="正在检查并按需准备 BM1688 运行时",
            )
            client, runtime_report = _runtime_prepare(client, cfg, command)
            _persist_state(
                device_id,
                stage="environment_prepare",
                code="ok",
                message="BM1688 运行环境已通过复检",
                environment_ready=True,
                detail={
                    **common_detail,
                    "preflight": preflight,
                    "runtime": runtime_report,
                },
            )

        _persist_state(
            device_id,
            status=STATUS_UPLOADING,
            stage="artifact_upload",
            code="running",
            message=f"正在上传 Agent 版本 {artifacts.version}",
        )
        _write_and_upload_artifacts(client, artifacts)
        _persist_state(
            device_id,
            status=STATUS_INSTALLING,
            stage="systemd_install",
            code="running",
            message="正在校验程序摘要并安装 systemd 服务",
        )
        # Capture the freshness boundary before the remote command starts.
        # The install script starts systemd; a fast Agent may successfully
        # publish its first heartbeat before SSH returns.  A later baseline
        # would discard that valid pulse and create a needless timeout.
        heartbeat_not_before = _utc_now()
        install_command = build_install_command(artifacts)
        code, out, err = edge_ssh_client.ssh_run(
            client,
            install_command,
            timeout=int(cfg.get("validation_timeout_sec") or 7200),
        )
        if int(code) != 0:
            raise EdgeAgentBootstrapError(
                f"systemd Agent 安装失败 (exit={code}): {_redact(err or out, secrets=(cfg['password'], cfg['token']))}",
                code="agent_install_failed",
                detail={
                    "stdout_tail": _redact(
                        out, secrets=(cfg["password"], cfg["token"])
                    ),
                    "stderr_tail": _redact(
                        err, secrets=(cfg["password"], cfg["token"])
                    ),
                },
            )
        # Switch the control channel before waiting: the heartbeat endpoint
        # rejects devices that are still registered as SSH-only.  The helper
        # marks the device offline/not-ready, so this transition cannot make a
        # freshly installed but silent Agent schedulable.
        _activate_board_agent_channel(device_id)
        _persist_state(
            device_id,
            status=STATUS_STARTING,
            stage="systemd_start",
            code="ok",
            message="systemd 服务已启用并启动，等待板端心跳",
            detail={
                **common_detail,
                "install_stdout_tail": _redact(
                    out, secrets=(cfg["password"], cfg["token"])
                ),
            },
        )

        deadline = _utc_now() + timedelta(
            seconds=max(0, int(command.heartbeat_timeout_sec))
        )
        _persist_state(
            device_id,
            status=STATUS_WAITING_HEARTBEAT,
            stage="heartbeat",
            code="waiting",
            message="Agent 已启动，等待带设备身份的有效心跳",
            heartbeat_deadline_at=deadline.isoformat(),
        )
        if command.wait_for_heartbeat and _wait_for_heartbeat(
            device_id,
            timeout_sec=command.heartbeat_timeout_sec,
            poll_interval_sec=command.heartbeat_poll_interval_sec,
            not_before=heartbeat_not_before,
            sleep_fn=sleep_fn,
        ):
            _persist_agent_environment_state(
                device_id,
                ready=True,
                code="agent_ready",
                message="Board Agent 心跳已确认，设备可调度",
            )
            _persist_state(
                device_id,
                status=STATUS_READY,
                stage="heartbeat",
                code="ready",
                message="Board Agent 心跳已确认，设备可进入任务调度池",
                completed_at=_utc_iso(),
                environment_ready=True,
            )
        elif command.wait_for_heartbeat:
            # Recheck liveness and close the scheduler-facing environment plus
            # lifecycle marker under the same registry lock.  This prevents a
            # late heartbeat from being accepted between two independent
            # timeout writes and then overwritten by the stale worker.
            _finalize_heartbeat_wait(
                device_id,
                heartbeat_not_before=heartbeat_not_before,
                common_detail=common_detail,
            )
        return get_bootstrap_status(device_id)
    except EdgeAgentBootstrapError as exc:
        _persist_state(
            device_id,
            status=STATUS_FAILED,
            stage="failed",
            code=exc.code,
            message=_redact(
                exc, secrets=(cfg.get("password", ""), cfg.get("token", ""))
            ),
            completed_at=_utc_iso(),
            detail={**common_detail, **dict(exc.detail)},
        )
        raise
    except Exception as exc:
        wrapped = EdgeAgentBootstrapError(
            f"Agent 自动引导失败: {_redact(exc, secrets=(cfg.get('password', ''), cfg.get('token', '')))}",
            code="edge_agent_bootstrap_failed",
        )
        _persist_state(
            device_id,
            status=STATUS_FAILED,
            stage="failed",
            code=wrapped.code,
            message=str(wrapped),
            completed_at=_utc_iso(),
            detail=common_detail,
        )
        raise wrapped from exc
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if owns_lock:
            lock.release()
        if owns_process_lock:
            _release_bootstrap_process_lock(process_lock_file)


def start_bootstrap_background(
    command: BootstrapEdgeAgentCommand,
    *,
    _resume: bool = False,
) -> EdgeAgentBootstrapResponse:
    """Queue a non-blocking bootstrap worker and return its persisted state."""

    device_id = _validate_device_id(command.device_id)
    record = registry_io.find_device_record(device_id)
    if record is None:
        raise EdgeAgentBootstrapError("边端设备不存在", code="device_not_found")
    persisted_status = persisted_bootstrap_status(record)
    persisted_run = persisted_status in ACTIVE_BOOTSTRAP_STATUSES
    if _resume and not persisted_run:
        # A startup recovery scan can race a heartbeat or an operator retry.
        # Never turn a newer terminal state back into a fresh installation.
        return get_bootstrap_status(device_id)
    if command.dry_run and persisted_run:
        # A preview must never replace an active installation lifecycle.  The
        # device page can issue a preview while polling, and persisting PREVIEW
        # here would hide QUEUED/INSTALLING/WAITING_HEARTBEAT and allow a second
        # worker to be scheduled for the same device.
        return get_bootstrap_status(device_id)
    # A completed pull-channel installation may be explicitly re-run (for
    # example after changing the Agent version or remote runtime).  Keep the
    # channel in ``board_agent`` while it is quiesced, so the worker must be
    # allowed to validate that existing channel before it can reconnect over
    # SSH.  The normal credential and busy-device checks still apply.
    allow_board_agent_channel = (
        str(record.get("validation_channel") or "ssh").strip().lower() == "board_agent"
    )
    if command.dry_run:
        return bootstrap_edge_agent(command)
    if not command.confirm_install:
        raise EdgeAgentBootstrapError(
            "安装常驻 Board Agent 前必须显式确认", code="confirmation_required"
        )
    # Render first so malformed credentials/paths fail synchronously and the
    # caller gets a useful response instead of a worker that immediately dies.
    # Keep that synchronous failure durable too: otherwise the device page
    # remains at NOT_STARTED even though the operator just attempted setup.
    try:
        if allow_board_agent_channel:
            artifacts = build_agent_bootstrap_artifacts(
                record,
                command,
                allow_board_agent_channel=True,
            )
        else:
            artifacts = build_agent_bootstrap_artifacts(record, command)
    except Exception as exc:
        try:
            persist_bootstrap_failure(device_id, exc, stage="validation")
        except Exception:
            logger.exception(
                "persist synchronous edge agent bootstrap validation failure failed device_id={}",
                device_id,
            )
        raise
    # Serialize the queue-state write with the worker.  Without taking this
    # lock first, a duplicate request could overwrite the active run's
    # ``started_at``/artifact metadata before the worker notices the lock and
    # returns its status.  The per-process lock below is complemented by an
    # OS-level lease so separate web/worker processes cannot both pass this
    # section for the same device.
    lock = _device_lock(device_id)
    if not lock.acquire(blocking=False):
        return get_bootstrap_status(device_id)
    process_lock_file = _acquire_bootstrap_process_lock(device_id)
    if process_lock_file is None:
        lock.release()
        return get_bootstrap_status(device_id)
    resume_terminal = False
    try:
        # Do the busy check, marker write, and quiesce while holding the same
        # registry lock as scheduler claims.  Either the scheduler wins and
        # this call observes PROCESSING, or this call publishes QUEUED before
        # the scheduler can inspect the device.
        with _device_mutation_guard(device_id):
            # Re-read after taking the lock.  The initial validation above is
            # intentionally synchronous for API feedback, but credentials,
            # channel, and lifecycle state may have changed while waiting for
            # the per-device/process leases.
            latest_record = registry_io.find_device_record(device_id)
            if latest_record is None:
                raise EdgeAgentBootstrapError("边端设备不存在", code="device_not_found")
            latest_status = persisted_bootstrap_status(latest_record)
            if _resume and latest_status not in ACTIVE_BOOTSTRAP_STATUSES:
                # A heartbeat or operator retry completed this run while the
                # recovery request was waiting.  Do not start a new install.
                resume_terminal = True
            else:
                record = latest_record
                allow_board_agent_channel = (
                    str(record.get("validation_channel") or "ssh").strip().lower()
                    == "board_agent"
                )
                try:
                    artifacts = build_agent_bootstrap_artifacts(
                        record,
                        command,
                        allow_board_agent_channel=allow_board_agent_channel,
                    )
                except TypeError:
                    # Compatibility with older adapters/test doubles that do
                    # not accept the explicit channel keyword.
                    artifacts = build_agent_bootstrap_artifacts(record, command)
                _assert_device_not_busy(device_id)
                _persist_state(
                    device_id,
                    require_durable=True,
                    status=STATUS_QUEUED,
                    stage="queue",
                    code="queued",
                    message="Agent 自动引导已排队",
                    agent_version=artifacts.version,
                    artifact_sha256=artifacts.artifact_sha256,
                    remote_root=artifacts.remote_root,
                    remote_version_root=artifacts.remote_version_root,
                    dry_run=False,
                    started_at=None,
                    completed_at=None,
                    heartbeat_deadline_at=None,
                )
                # Publish the marker before the worker starts and before an
                # existing Agent is quiesced; otherwise an SSH device whose old
                # environment is still marked ready could be claimed.
                _quiesce_existing_board_agent(device_id)
    except Exception:
        _release_bootstrap_process_lock(process_lock_file)
        lock.release()
        raise

    if resume_terminal:
        _release_bootstrap_process_lock(process_lock_file)
        lock.release()
        return get_bootstrap_status(device_id)

    def _worker() -> None:
        try:
            worker_kwargs: dict[str, Any] = {
                "_held_lock": lock,
                "_held_process_lock": process_lock_file,
            }
            if allow_board_agent_channel:
                worker_kwargs["_allow_board_agent_channel"] = True
            bootstrap_edge_agent(command, **worker_kwargs)
        except Exception:
            logger.exception(
                "edge agent bootstrap worker failed device_id={}", device_id
            )
        finally:
            # ``bootstrap_edge_agent`` deliberately does not release a lock
            # supplied by its caller, including when validation fails before
            # its main try/finally block.
            _release_bootstrap_process_lock(process_lock_file)
            if lock.locked():
                lock.release()

    try:
        thread = threading.Thread(
            target=_worker,
            name=f"edge-agent-bootstrap-{device_id}",
            daemon=True,
        )
        thread.start()
    except Exception:
        _release_bootstrap_process_lock(process_lock_file)
        lock.release()
        raise
    return get_bootstrap_status(device_id)


__all__ = [
    "AGENT_SERVICE_NAME",
    "ACTIVE_BOOTSTRAP_STATUSES",
    "BOOTSTRAP_PROTOCOL_VERSION",
    "EdgeAgentBootstrapArtifacts",
    "EdgeAgentBootstrapError",
    "STATUS_FAILED",
    "STATUS_READY",
    "STATUS_WAITING_HEARTBEAT",
    "bootstrap_edge_agent",
    "build_agent_bootstrap_artifacts",
    "build_install_command",
    "build_preflight_command",
    "build_preflight_script",
    "build_systemd_unit",
    "get_bootstrap_status",
    "parse_preflight_output",
    "persisted_bootstrap_status",
    "persist_bootstrap_failure",
    "bootstrap_in_progress",
    "resume_persisted_bootstrap",
    "resume_persisted_bootstraps",
    "start_bootstrap_background",
    "validate_bootstrap_device",
]
