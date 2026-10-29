from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar, cast

from labelu.internal.common.edge_remote_paths import edge_remote_paths
from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    read_group_values,
    write_group_values,
)

DEFAULT_DEVICE_NAME = "默认边端设备"
PASSWORD_SOURCE_ENCRYPTED = "mysql:encrypted"
PRIVATE_KEY_PATH_SOURCE_ENCRYPTED = "mysql:encrypted"
_HEARTBEAT_RETRYABLE_BOOTSTRAP_CODES = frozenset(
    {"heartbeat_timeout", "environment_not_ready", "waiting_heartbeat"}
)
_DEVICE_MUTATION_LOCK_FILENAME = ".edge_device_mutation.lock"
_DEVICE_MUTATION_PROCESS_LOCK = threading.RLock()
_DEVICE_MUTATION_LOCAL = threading.local()
_DEFAULT_DEVICE_MUTATION_LOCK_TIMEOUT_SEC = 5.0


class DeviceMutationLockUnavailableError(RuntimeError):
    """Raised when a registry write cannot acquire the host mutation lock."""


_MutationFunc = TypeVar("_MutationFunc", bound=Callable[..., Any])


def _device_mutation_lock_path() -> Path:
    """Return the lock file shared by all LabelU workers on this host."""

    return Path(str(getattr(settings, "BASE_DATA_DIR", "") or ".")) / (
        _DEVICE_MUTATION_LOCK_FILENAME
    )


def _try_acquire_device_mutation_file_lock(
    *, timeout_sec: float
) -> tuple[object | None, bool]:
    """Acquire the cross-process lock file without catching caller errors.

    The registry is persisted through short-lived SQLAlchemy sessions, so a
    database transaction cannot safely be held across the read/modify/write
    sequence.  A host-local advisory file lock keeps that sequence atomic for
    multiple web/worker processes while remaining available on both Windows
    and POSIX hosts.
    """

    lock_file = None
    acquired = False
    try:
        path = _device_mutation_lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = path.open("a+b")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        while True:
            # Both msvcrt.locking and flock operate at the current offset.
            lock_file.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(0.05, max(0.001, deadline - time.monotonic())))
    except (ImportError, OSError, TypeError, ValueError):
        acquired = False

    if not acquired and lock_file is not None:
        lock_file.close()
        lock_file = None
    return lock_file, acquired


def _release_device_mutation_file_lock(lock_file: object) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except (ImportError, OSError, TypeError, ValueError):
        # Closing the descriptor below still releases an OS lock after a
        # process exit; there is no useful recovery action for an unlock error.
        pass


@contextmanager
def device_mutation_lock(
    *, timeout_sec: float = _DEFAULT_DEVICE_MUTATION_LOCK_TIMEOUT_SEC
) -> Iterator[bool]:
    """Serialize device busy-check plus registry mutation operations.

    ``True`` is yielded only when both the in-process and cross-process locks
    were acquired.  Callers must fail closed when ``False`` is returned.  The
    lock is intentionally host-global: the scheduler already serializes its
    allocation pass, and a single lock avoids multi-device deadlocks while
    protecting active-device switches as well as per-device edits/deletes.
    """

    # Service-layer operations already hold this lock while they perform the
    # busy check and then mutate the registry.  Keep nested low-level writes
    # re-entrant so adding protection at the persistence boundary does not
    # deadlock those callers.  The OS file descriptor remains owned by the
    # outermost context only.
    state = getattr(_DEVICE_MUTATION_LOCAL, "state", None)
    if isinstance(state, dict) and int(state.get("depth", 0) or 0) > 0:
        state["depth"] = int(state.get("depth", 0) or 0) + 1
        try:
            yield bool(state.get("acquired"))
        finally:
            state["depth"] = int(state.get("depth", 1) or 1) - 1
        return

    try:
        timeout = max(0.0, float(timeout_sec))
    except (TypeError, ValueError):
        timeout = _DEFAULT_DEVICE_MUTATION_LOCK_TIMEOUT_SEC

    if timeout <= 0:
        process_acquired = _DEVICE_MUTATION_PROCESS_LOCK.acquire(blocking=False)
    else:
        process_acquired = _DEVICE_MUTATION_PROCESS_LOCK.acquire(timeout=timeout)
    if not process_acquired:
        yield False
        return

    lock_file = None
    file_acquired = False
    try:
        lock_file, file_acquired = _try_acquire_device_mutation_file_lock(
            timeout_sec=timeout
        )
        if not file_acquired:
            # Do not hold the in-process lock while reporting a failed
            # acquisition; another thread may be able to make progress.
            if lock_file is not None:
                try:
                    lock_file.close()  # type: ignore[attr-defined]
                except (OSError, ValueError):
                    pass
            _DEVICE_MUTATION_PROCESS_LOCK.release()
            yield False
            return
        _DEVICE_MUTATION_LOCAL.state = {"depth": 1, "acquired": True}
        yield True
    finally:
        state = getattr(_DEVICE_MUTATION_LOCAL, "state", None)
        # The outer context owns the descriptor.  A defensive check avoids
        # releasing it twice if a caller manually manipulates thread-local
        # state during exception unwinding.
        if isinstance(state, dict) and int(state.get("depth", 0) or 0) > 0:
            try:
                delattr(_DEVICE_MUTATION_LOCAL, "state")
            except AttributeError:
                pass
            if file_acquired and lock_file is not None:
                _release_device_mutation_file_lock(lock_file)
            if lock_file is not None:
                try:
                    lock_file.close()  # type: ignore[attr-defined]
                except (OSError, ValueError):
                    pass
            _DEVICE_MUTATION_PROCESS_LOCK.release()


def _locked_registry_mutation(func: _MutationFunc) -> _MutationFunc:
    """Protect a persistence-boundary read/modify/write operation.

    Higher-level services may hold ``device_mutation_lock`` across a busy
    check.  ``device_mutation_lock`` is re-entrant for that same thread, so
    decorating the low-level operations closes direct/background write paths
    without changing the service-level atomicity contract.
    """

    @wraps(func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        with device_mutation_lock() as acquired:
            if not acquired:
                raise DeviceMutationLockUnavailableError(
                    "edge device registry mutation lock unavailable"
                )
            return func(*args, **kwargs)

    return cast(_MutationFunc, wrapped)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_store() -> dict[str, Any]:
    return {"version": 1, "active_device_id": "", "devices": []}


def _default_device_payload() -> dict[str, Any]:
    paths = edge_remote_paths()
    return {
        "enabled": True,
        "host": "",
        "port": 22,
        "user": "linaro",
        "edge_root": paths.root,
        "remote_base": paths.jobs,
        "sophon_demo_root": paths.sophon_demo_root,
        "auto_after_export": True,
        "run_coco_eval": False,
        "connect_timeout_sec": 30,
        "validation_timeout_sec": 7200,
        "docker_container": "bm1688",
        "docker_use_sudo": True,
        "validation_channel": "ssh",
        "board_agent_status": "unknown",
        "board_agent_last_heartbeat_at": None,
        "board_agent_environment_ready": False,
        "board_agent_capabilities": {},
        "board_agent_message": "",
        # Versioned SSH bootstrap state is configuration metadata, not a
        # scheduler lease.  Keeping it in the device JSON avoids a migration
        # while still surviving service restarts and environment probes.
        "agent_bootstrap": {},
        "private_key_path_source_type": "",
        "private_key_path_masked": "",
        "private_key_path_configured": False,
        "private_key_path_runtime_available": False,
        "private_key_path_updated_at": None,
    }


def _sanitize_masked_hint(value: str | None) -> str:
    return str(value or "").strip()[:64]


def _mask_password_hint(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 4:
        return "*" * len(raw)
    return ("*" * (len(raw) - 4)) + raw[-4:]


def _normalize_private_key_path(value: Any) -> str:
    """Normalize a server-side SSH key path without touching the key file.

    The browser submits a path that is resolved on the LabelU server.  Keep
    the value opaque in API responses and reject control characters before it
    can reach Paramiko or a shell command.
    """

    raw = str(value or "").replace("\x00", "").strip()
    if any(ord(ch) < 32 for ch in raw):
        raise ValueError("SSH 私钥路径不能包含控制字符")
    return raw[:1024]


def _resolve_inline_private_key_path_from_record(
    record: Dict[str, Any],
) -> tuple[str, str]:
    encrypted = str(record.get("private_key_path_encrypted") or "").strip()
    if encrypted:
        try:
            from labelu.internal.common.crypto import decrypt_value

            return _normalize_private_key_path(decrypt_value(encrypted)), PRIVATE_KEY_PATH_SOURCE_ENCRYPTED
        except Exception:
            return "", "mysql:decrypt_failed"
    # Accept the pre-encryption shape so old/dev records can be migrated on
    # the next registry read without losing access to the configured key.
    legacy = _normalize_private_key_path(record.get("private_key_path"))
    return legacy, "legacy:inline" if legacy else "mysql:missing"


def resolve_device_private_key_path_with_source(
    record: Dict[str, Any] | None = None,
) -> tuple[str, str]:
    return _resolve_inline_private_key_path_from_record(record or {})


def resolve_device_private_key_path(record: Dict[str, Any] | None = None) -> str:
    return resolve_device_private_key_path_with_source(record)[0]


def _resolve_inline_password_from_record(record: Dict[str, Any]) -> tuple[str, str]:
    encrypted = str(record.get("password_encrypted") or "").strip()
    if not encrypted:
        return "", "mysql:missing"
    try:
        from labelu.internal.common.crypto import decrypt_value

        return decrypt_value(encrypted), PASSWORD_SOURCE_ENCRYPTED
    except Exception:
        return "", "mysql:decrypt_failed"


def _secret_meta_from_record(
    record: Dict[str, Any],
) -> dict[str, Any]:
    password_encrypted = str(record.get("password_encrypted") or "").strip()
    masked_hint = (
        _sanitize_masked_hint(
            record.get("password_masked_hint") or record.get("password_masked")
        )
        if password_encrypted
        else ""
    )
    private_key_path, private_key_source = resolve_device_private_key_path_with_source(record)
    private_key_path_encrypted = str(
        record.get("private_key_path_encrypted") or ""
    ).strip()
    if private_key_path and not private_key_path_encrypted:
        from labelu.internal.common.crypto import encrypt_value

        private_key_path_encrypted = encrypt_value(private_key_path)
    meta = {
        "password_source_type": (
            PASSWORD_SOURCE_ENCRYPTED if password_encrypted else ""
        ),
        "password_masked": masked_hint,
        "password_masked_hint": masked_hint,
        "password_is_configured": bool(password_encrypted),
        "password_updated_at": (
            str(record.get("password_updated_at") or "").strip() or None
        ),
        "private_key_path_source_type": (
            PRIVATE_KEY_PATH_SOURCE_ENCRYPTED if private_key_path_encrypted else ""
        ),
        "private_key_path_masked": "********" if private_key_path_encrypted else "",
        "private_key_path_configured": bool(private_key_path_encrypted),
        "private_key_path_runtime_available": bool(
            private_key_path and Path(os.path.expanduser(private_key_path)).is_file()
        ),
        "private_key_path_updated_at": (
            str(record.get("private_key_path_updated_at") or "").strip() or None
        ),
    }
    if password_encrypted:
        meta["password_encrypted"] = password_encrypted
    if private_key_path_encrypted:
        meta["private_key_path_encrypted"] = private_key_path_encrypted
    return meta


def resolve_device_password_with_source(
    record: Dict[str, Any] | None = None,
) -> tuple[str, str]:
    return _resolve_inline_password_from_record(record or {})


def resolve_device_password(record: Dict[str, Any] | None = None) -> str:
    return resolve_device_password_with_source(record)[0]


def _normalize_device_record(
    record: Dict[str, Any],
) -> dict[str, Any]:
    now = _utc_now_iso()
    defaults = _default_device_payload()
    base = dict(record or {})
    secret_meta = _secret_meta_from_record(base)
    password = resolve_device_password({**base, **secret_meta})
    private_key_path, _private_key_source = resolve_device_private_key_path_with_source(
        base
    )
    creds_ready = bool(
        str(base.get("host") or defaults["host"]).strip()
        and str(base.get("user") or defaults["user"]).strip()
        and (password or private_key_path)
    )
    enabled_default = bool(defaults["enabled"] or creds_ready)
    auto_default = bool(defaults["auto_after_export"] or creds_ready)
    device_id = str(base.get("id") or f"edge_{uuid.uuid4().hex[:12]}").strip()[:64]
    name = (
        str(base.get("name") or DEFAULT_DEVICE_NAME).strip()[:64] or DEFAULT_DEVICE_NAME
    )
    probe_message = str(
        base.get("probe_message") or base.get("last_probe_message") or ""
    ).strip()[:800]
    paths = edge_remote_paths()
    requested_root = str(base.get("edge_root") or "").strip()
    if requested_root and edge_remote_paths(requested_root).root != paths.root:
        raise ValueError(f"edge_root must be {paths.root}")
    normalized = {
        "id": device_id,
        "name": name,
        "enabled": bool(base.get("enabled") if "enabled" in base else enabled_default),
        "host": str(base.get("host") or defaults["host"]).strip()[:256],
        "port": int(base.get("port") or defaults["port"] or 22),
        "user": str(base.get("user") or defaults["user"]).strip()[:128],
        "edge_root": paths.root,
        "remote_base": paths.jobs,
        "sophon_demo_root": paths.sophon_demo_root,
        "auto_after_export": bool(
            base.get("auto_after_export")
            if "auto_after_export" in base
            else auto_default
        ),
        "run_coco_eval": bool(
            base.get("run_coco_eval")
            if "run_coco_eval" in base
            else defaults["run_coco_eval"]
        ),
        "connect_timeout_sec": int(
            base.get("connect_timeout_sec") or defaults["connect_timeout_sec"] or 30
        ),
        "validation_timeout_sec": int(
            base.get("validation_timeout_sec")
            or defaults["validation_timeout_sec"]
            or 7200
        ),
        "docker_container": str(
            base.get("docker_container") or defaults["docker_container"]
        ).strip()[:128],
        "docker_use_sudo": bool(
            base.get("docker_use_sudo")
            if "docker_use_sudo" in base
            else defaults["docker_use_sudo"]
        ),
        "validation_channel": (
            "board_agent"
            if str(base.get("validation_channel") or "").strip().lower()
            in {"board_agent", "board-agent", "主动拉取", "板端主动拉取"}
            else "ssh"
        ),
        "board_agent_status": str(
            base.get("board_agent_status") or defaults["board_agent_status"]
        ).strip()[:32],
        "board_agent_last_heartbeat_at": str(
            base.get("board_agent_last_heartbeat_at")
            or defaults["board_agent_last_heartbeat_at"]
            or ""
        ).strip()
        or None,
        "board_agent_environment_ready": bool(
            base.get("board_agent_environment_ready")
            if "board_agent_environment_ready" in base
            else defaults["board_agent_environment_ready"]
        ),
        "board_agent_capabilities": (
            dict(base.get("board_agent_capabilities"))
            if isinstance(base.get("board_agent_capabilities"), dict)
            else {}
        ),
        "board_agent_message": str(
            base.get("board_agent_message") or defaults["board_agent_message"]
        ).strip()[:800],
        "agent_bootstrap": (
            dict(base.get("agent_bootstrap"))
            if isinstance(base.get("agent_bootstrap"), dict)
            else dict(defaults.get("agent_bootstrap") or {})
        ),
        "probe_status": str(
            base.get("probe_status") or base.get("last_probe_status") or "unknown"
        )[:32],
        "probe_stage": str(
            base.get("probe_stage") or base.get("last_probe_stage") or ""
        )[:64],
        "probe_code": str(base.get("probe_code") or base.get("last_probe_code") or "")[
            :64
        ],
        "probe_message": probe_message,
        "probe_checked_at": str(
            base.get("probe_checked_at") or base.get("last_probe_checked_at") or ""
        ).strip()
        or None,
        "last_probe_status": str(
            base.get("last_probe_status") or base.get("probe_status") or "unknown"
        )[:32],
        "last_probe_stage": str(
            base.get("last_probe_stage") or base.get("probe_stage") or ""
        )[:64],
        "last_probe_code": str(
            base.get("last_probe_code") or base.get("probe_code") or ""
        )[:64],
        "last_probe_message": probe_message,
        "last_probe_checked_at": str(
            base.get("last_probe_checked_at") or base.get("probe_checked_at") or ""
        ).strip()
        or None,
        "last_validation_status": str(base.get("last_validation_status") or "").strip()[
            :64
        ],
        "last_validation_at": str(base.get("last_validation_at") or "").strip() or None,
        "environment_status": str(base.get("environment_status") or "unknown").strip()[
            :32
        ],
        "environment_stage": str(base.get("environment_stage") or "").strip()[:64],
        "environment_code": str(base.get("environment_code") or "").strip()[:64],
        "environment_message": str(base.get("environment_message") or "").strip()[:800],
        "environment_checked_at": str(base.get("environment_checked_at") or "").strip()
        or None,
        "environment_report": (
            dict(base.get("environment_report"))
            if isinstance(base.get("environment_report"), dict)
            else {}
        ),
        "last_used_at": str(base.get("last_used_at") or "").strip() or None,
        "created_at": str(base.get("created_at") or now).strip() or now,
        "updated_at": str(base.get("updated_at") or now).strip() or now,
        **secret_meta,
    }
    normalized.pop("password_env_name", None)
    # Never retain a legacy plaintext path after normalization.  The runtime
    # config resolver above still exposes it to Paramiko when needed.
    normalized.pop("private_key_path", None)
    return normalized


def _record_to_runtime_config(record: Dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_device_record(record)
    password, _source = resolve_device_password_with_source(normalized)
    private_key_path, _private_key_source = resolve_device_private_key_path_with_source(
        normalized
    )
    return {
        "enabled": normalized["enabled"],
        "host": normalized["host"],
        "port": normalized["port"],
        "user": normalized["user"],
        "password": password,
        "private_key_path": private_key_path,
        "edge_root": normalized["edge_root"],
        "remote_base": normalized["remote_base"],
        "sophon_demo_root": normalized["sophon_demo_root"],
        "auto_after_export": normalized["auto_after_export"],
        "run_coco_eval": normalized["run_coco_eval"],
        "connect_timeout_sec": normalized["connect_timeout_sec"],
        "validation_timeout_sec": normalized["validation_timeout_sec"],
        "docker_container": normalized["docker_container"],
        "docker_use_sudo": normalized["docker_use_sudo"],
        "validation_channel": normalized["validation_channel"],
        "board_agent_status": normalized["board_agent_status"],
        "board_agent_last_heartbeat_at": normalized["board_agent_last_heartbeat_at"],
        "board_agent_environment_ready": normalized["board_agent_environment_ready"],
        "board_agent_capabilities": normalized["board_agent_capabilities"],
        "board_agent_message": normalized["board_agent_message"],
        "agent_bootstrap": dict(normalized.get("agent_bootstrap") or {}),
        "probe_status": normalized["probe_status"],
        "probe_stage": normalized["probe_stage"],
        "probe_code": normalized["probe_code"],
        "probe_message": normalized["probe_message"],
        "probe_checked_at": normalized["probe_checked_at"],
        "password_source_type": normalized["password_source_type"],
        "password_masked_hint": normalized["password_masked_hint"],
        "password_masked": normalized["password_masked"],
        "password_is_configured": normalized["password_is_configured"],
        "password_updated_at": normalized["password_updated_at"],
        "private_key_path_source_type": normalized["private_key_path_source_type"],
        "private_key_path_masked": normalized["private_key_path_masked"],
        "private_key_path_configured": normalized["private_key_path_configured"],
        "private_key_path_runtime_available": normalized[
            "private_key_path_runtime_available"
        ],
        "private_key_path_updated_at": normalized["private_key_path_updated_at"],
        "device_id": normalized["id"],
        "device_name": normalized["name"],
    }


def _store_to_db_items(store: dict[str, Any]) -> dict[str, Any]:
    normalized = _restore_store(store)
    return {"device_store": normalized}


def _restore_store(candidate: Any) -> dict[str, Any]:
    store = _default_store()
    if isinstance(candidate, dict):
        store["version"] = 1
        store["active_device_id"] = str(candidate.get("active_device_id") or "").strip()
        devices = []
        for record in list(candidate.get("devices") or []):
            if isinstance(record, dict):
                devices.append(_normalize_device_record(record))
        store["devices"] = devices
    if not str(store.get("active_device_id") or "").strip() and store.get("devices"):
        store["active_device_id"] = str(store["devices"][0]["id"])
    return store


def load_device_store() -> dict[str, Any]:
    db_store = read_group_values("edge_device_registry").get("device_store")
    restored = _restore_store(db_store)
    if isinstance(db_store, dict) and db_store != restored:
        # Normalization is itself a whole-document read/modify/write.  Do not
        # let a stale read repair the registry over a concurrent CRUD or
        # heartbeat update; re-read the current value after acquiring the same
        # host/process lock used by all other registry mutations.
        with device_mutation_lock() as acquired:
            if acquired:
                latest_raw = read_group_values("edge_device_registry").get(
                    "device_store"
                )
                latest_restored = _restore_store(latest_raw)
                if isinstance(latest_raw, dict) and latest_raw != latest_restored:
                    write_group_values(
                        "edge_device_registry",
                        {"device_store": latest_restored},
                        value_source="db",
                        change_reason="normalize edge device registry mysql contract",
                    )
                return latest_restored
    return restored


@_locked_registry_mutation
def _write_device_store(store: dict[str, Any]) -> None:
    payload = _restore_store(store)
    write_group_values(
        "edge_device_registry",
        _store_to_db_items(payload),
        value_source="db",
        change_reason="save edge device registry",
    )


def list_device_records() -> list[dict[str, Any]]:
    return list(load_device_store().get("devices") or [])


def find_device_record(device_id: str) -> Optional[dict[str, Any]]:
    target = str(device_id or "").strip()
    if not target:
        return None
    for record in list_device_records():
        if str(record.get("id") or "") == target:
            return record
    return None


def get_active_device_id() -> str:
    return str(load_device_store().get("active_device_id") or "").strip()


def get_active_device_record() -> Optional[dict[str, Any]]:
    return find_device_record(get_active_device_id())


@_locked_registry_mutation
def set_active_device_id(device_id: str) -> dict[str, Any]:
    target = str(device_id or "").strip()
    store = load_device_store()
    if not find_device_record(target):
        raise ValueError(f"边端设备不存在: {target}")
    store["active_device_id"] = target
    _write_device_store(store)
    return store


def _apply_password_secret_change(
    record: dict[str, Any],
    *,
    action: str = "keep",
    password: Optional[str] = None,
) -> dict[str, Any]:
    if action == "keep":
        return record
    if action == "clear":
        record["password_source_type"] = ""
        record["password_masked"] = ""
        record["password_masked_hint"] = ""
        record["password_is_configured"] = False
        record["password_updated_at"] = _utc_now_iso()
        record.pop("password", None)
        record.pop("password_encrypted", None)
        return record

    inline_password = str(password or "")
    if not inline_password:
        raise ValueError("保存设备时必须填写 SSH 密码")
    from labelu.internal.common.crypto import encrypt_value

    record["password_source_type"] = PASSWORD_SOURCE_ENCRYPTED
    record["password_encrypted"] = encrypt_value(inline_password)
    record["password_masked"] = _mask_password_hint(inline_password)
    record["password_masked_hint"] = record["password_masked"]
    record["password_is_configured"] = True
    record["password_updated_at"] = _utc_now_iso()
    record.pop("password", None)
    record.pop("password_env_name", None)
    return record


def _apply_private_key_path_secret_change(
    record: dict[str, Any],
    *,
    action: str = "keep",
    private_key_path: Optional[str] = None,
) -> dict[str, Any]:
    action = str(action or "keep").strip().lower()
    if action == "keep":
        return record
    if action == "clear":
        record["private_key_path_source_type"] = ""
        record["private_key_path_masked"] = ""
        record["private_key_path_configured"] = False
        record["private_key_path_runtime_available"] = False
        record["private_key_path_updated_at"] = _utc_now_iso()
        record.pop("private_key_path", None)
        record.pop("private_key_path_encrypted", None)
        return record

    inline_path = _normalize_private_key_path(private_key_path)
    if not inline_path:
        raise ValueError("设置 SSH 私钥时必须填写私钥路径")
    from labelu.internal.common.crypto import encrypt_value

    record["private_key_path_source_type"] = PRIVATE_KEY_PATH_SOURCE_ENCRYPTED
    record["private_key_path_encrypted"] = encrypt_value(inline_path)
    record["private_key_path_masked"] = "********"
    record["private_key_path_configured"] = True
    record["private_key_path_runtime_available"] = bool(
        Path(os.path.expanduser(inline_path)).is_file()
    )
    record["private_key_path_updated_at"] = _utc_now_iso()
    record.pop("private_key_path", None)
    return record


@_locked_registry_mutation
def create_device_record(
    *,
    name: Optional[str],
    host: Optional[str] = None,
    user: str,
    port: int = 22,
    password: Optional[str] = None,
    private_key_path: Optional[str] = None,
    edge_root: Optional[str] = None,
    remote_base: Optional[str] = None,
    sophon_demo_root: Optional[str] = None,
    auto_after_export: bool = True,
    run_coco_eval: bool = False,
    connect_timeout_sec: int = 30,
    validation_timeout_sec: int = 7200,
    docker_container: str = "",
    docker_use_sudo: bool = True,
    validation_channel: str = "ssh",
    enabled: bool = True,
    set_active: bool = True,
) -> dict[str, Any]:
    now = _utc_now_iso()
    record = _normalize_device_record(
        {
            "id": f"edge_{uuid.uuid4().hex[:12]}",
            "name": str(name or "").strip() or str(host or "").strip() or DEFAULT_DEVICE_NAME,
            "host": host,
            "port": port,
            "user": user,
            "edge_root": edge_root,
            "remote_base": remote_base,
            "sophon_demo_root": sophon_demo_root,
            "auto_after_export": auto_after_export,
            "run_coco_eval": run_coco_eval,
            "connect_timeout_sec": connect_timeout_sec,
            "validation_timeout_sec": validation_timeout_sec,
            "docker_container": docker_container,
            "docker_use_sudo": docker_use_sudo,
            "validation_channel": validation_channel,
            "enabled": enabled,
            "created_at": now,
            "updated_at": now,
        }
    )
    if str(validation_channel or "ssh").strip().lower() == "ssh":
        if str(password or "").strip():
            _apply_password_secret_change(record, action="set", password=password)
        if str(private_key_path or "").strip():
            _apply_private_key_path_secret_change(
                record, action="set", private_key_path=private_key_path
            )
        if not resolve_device_password(record) and not resolve_device_private_key_path(record):
            raise ValueError("SSH 通道必须配置 SSH 密码或私钥路径")
    store = load_device_store()
    devices = list(store.get("devices") or [])
    devices.append(_normalize_device_record(record))
    store["devices"] = devices
    if set_active or not str(store.get("active_device_id") or "").strip():
        store["active_device_id"] = record["id"]
    _write_device_store(store)
    return _normalize_device_record(record)


@_locked_registry_mutation
def update_device_record(
    device_id: str,
    *,
    name: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password_secret_action: str = "keep",
    password: Optional[str] = None,
    private_key_path_secret_action: str = "keep",
    private_key_path: Optional[str] = None,
    edge_root: Optional[str] = None,
    remote_base: Optional[str] = None,
    sophon_demo_root: Optional[str] = None,
    auto_after_export: Optional[bool] = None,
    run_coco_eval: Optional[bool] = None,
    connect_timeout_sec: Optional[int] = None,
    validation_timeout_sec: Optional[int] = None,
    docker_container: Optional[str] = None,
    docker_use_sudo: Optional[bool] = None,
    validation_channel: Optional[str] = None,
    enabled: Optional[bool] = None,
    probe_status: Optional[str] = None,
    probe_stage: Optional[str] = None,
    probe_code: Optional[str] = None,
    probe_message: Optional[str] = None,
    probe_checked_at: Optional[str] = None,
    last_validation_status: Optional[str] = None,
    last_validation_at: Optional[str] = None,
    environment_status: Optional[str] = None,
    environment_stage: Optional[str] = None,
    environment_code: Optional[str] = None,
    environment_message: Optional[str] = None,
    environment_checked_at: Optional[str] = None,
    environment_report: Optional[dict[str, Any]] = None,
    last_used_at: Optional[str] = None,
    board_agent_status: Optional[str] = None,
    board_agent_last_heartbeat_at: Optional[str] = None,
    board_agent_environment_ready: Optional[bool] = None,
    board_agent_capabilities: Optional[dict[str, Any]] = None,
    board_agent_message: Optional[str] = None,
    agent_bootstrap: Optional[dict[str, Any]] = None,
    preserve_password_on_board_agent: bool = False,
    preserve_private_key_on_board_agent: bool = False,
) -> dict[str, Any]:
    store = load_device_store()
    devices = list(store.get("devices") or [])
    found = None
    for idx, record in enumerate(devices):
        if str(record.get("id") or "") != str(device_id):
            continue
        mutable = dict(record)
        previous_channel = (
            str(mutable.get("validation_channel") or "ssh").strip().lower()
        )
        channel_requested = validation_channel is not None
        if name is not None and str(name).strip():
            mutable["name"] = str(name).strip()[:64]
        if host is not None:
            mutable["host"] = str(host).strip()[:256]
        if port is not None:
            mutable["port"] = int(port)
        if user is not None:
            mutable["user"] = str(user).strip()[:128]
        if edge_root is not None:
            mutable["edge_root"] = str(edge_root).strip()[:1024]
        if remote_base is not None:
            mutable["remote_base"] = str(remote_base).strip()[:1024]
        if sophon_demo_root is not None:
            mutable["sophon_demo_root"] = str(sophon_demo_root).strip()[:1024]
        if auto_after_export is not None:
            mutable["auto_after_export"] = bool(auto_after_export)
        if run_coco_eval is not None:
            mutable["run_coco_eval"] = bool(run_coco_eval)
        if connect_timeout_sec is not None:
            mutable["connect_timeout_sec"] = int(connect_timeout_sec)
        if validation_timeout_sec is not None:
            mutable["validation_timeout_sec"] = int(validation_timeout_sec)
        if docker_container is not None:
            mutable["docker_container"] = str(docker_container).strip()[:128]
        if docker_use_sudo is not None:
            mutable["docker_use_sudo"] = bool(docker_use_sudo)
        if validation_channel is not None:
            channel = str(validation_channel).strip().lower()
            mutable["validation_channel"] = (
                "board_agent"
                if channel in {"board_agent", "board-agent", "主动拉取", "板端主动拉取"}
                else "ssh"
            )
        if enabled is not None:
            mutable["enabled"] = bool(enabled)
        channel = str(mutable.get("validation_channel") or "ssh").strip().lower()
        if channel == "board_agent":
            # A completed Board Agent device does not need an SSH secret, but
            # the SSH credential is still required while an installation is
            # transitioning from the public SSH channel to the resident
            # Agent.  Lifecycle writers also update this record repeatedly
            # while waiting for the first heartbeat.  Do not clear the
            # credential on those transitional/same-channel writes; the
            # bootstrap service explicitly clears it only after READY.
            password_action = str(password_secret_action or "keep").strip().lower()
            switched_channel = channel_requested and previous_channel != "board_agent"
            if password_action in {"clear", "set"}:
                _apply_password_secret_change(mutable, action="clear")
            elif switched_channel and not preserve_password_on_board_agent:
                _apply_password_secret_change(mutable, action="clear")
            key_action = str(private_key_path_secret_action or "keep").strip().lower()
            if key_action in {"clear", "set"}:
                _apply_private_key_path_secret_change(mutable, action="clear")
            elif switched_channel and not preserve_private_key_on_board_agent:
                _apply_private_key_path_secret_change(mutable, action="clear")
        else:
            if (
                password_secret_action == "keep"
                and not str(mutable.get("password_encrypted") or "").strip()
                and not str(password or "").strip()
                and private_key_path_secret_action == "keep"
                and not str(mutable.get("private_key_path_encrypted") or "").strip()
                and not str(private_key_path or "").strip()
            ):
                raise ValueError("SSH 通道必须配置 SSH 密码或私钥路径")
            _apply_password_secret_change(
                mutable,
                action=password_secret_action,
                password=password,
            )
            _apply_private_key_path_secret_change(
                mutable,
                action=private_key_path_secret_action,
                private_key_path=private_key_path,
            )
        if probe_status is not None:
            mutable["probe_status"] = str(probe_status)[:32]
            mutable["last_probe_status"] = mutable["probe_status"]
        if probe_stage is not None:
            mutable["probe_stage"] = str(probe_stage)[:64]
            mutable["last_probe_stage"] = mutable["probe_stage"]
        if probe_code is not None:
            mutable["probe_code"] = str(probe_code)[:64]
            mutable["last_probe_code"] = mutable["probe_code"]
        if probe_message is not None:
            mutable["probe_message"] = str(probe_message)[:800]
            mutable["last_probe_message"] = mutable["probe_message"]
        if probe_checked_at is not None:
            mutable["probe_checked_at"] = probe_checked_at
            mutable["last_probe_checked_at"] = probe_checked_at
        if last_validation_status is not None:
            mutable["last_validation_status"] = str(last_validation_status)[:64]
        if last_validation_at is not None:
            mutable["last_validation_at"] = last_validation_at
        if environment_status is not None:
            mutable["environment_status"] = str(environment_status)[:32]
        if environment_stage is not None:
            mutable["environment_stage"] = str(environment_stage)[:64]
        if environment_code is not None:
            mutable["environment_code"] = str(environment_code)[:64]
        if environment_message is not None:
            mutable["environment_message"] = str(environment_message)[:800]
        if environment_checked_at is not None:
            mutable["environment_checked_at"] = environment_checked_at
        if environment_report is not None:
            mutable["environment_report"] = dict(environment_report)
        if last_used_at is not None:
            mutable["last_used_at"] = last_used_at
        if board_agent_status is not None:
            mutable["board_agent_status"] = str(board_agent_status).strip()[:32]
        if board_agent_last_heartbeat_at is not None:
            mutable["board_agent_last_heartbeat_at"] = board_agent_last_heartbeat_at
        if board_agent_environment_ready is not None:
            mutable["board_agent_environment_ready"] = bool(board_agent_environment_ready)
        if board_agent_capabilities is not None:
            mutable["board_agent_capabilities"] = dict(board_agent_capabilities)
        if board_agent_message is not None:
            mutable["board_agent_message"] = str(board_agent_message).strip()[:800]
        if agent_bootstrap is not None:
            mutable["agent_bootstrap"] = dict(agent_bootstrap)
        mutable["updated_at"] = _utc_now_iso()
        devices[idx] = _normalize_device_record(mutable)
        found = devices[idx]
        break
    if found is None:
        raise ValueError(f"边端设备不存在: {device_id}")
    store["devices"] = devices
    _write_device_store(store)
    return found


@_locked_registry_mutation
def record_board_agent_heartbeat(
    device_id: str,
    *,
    environment_ready: bool,
    capabilities: Optional[dict[str, Any]] = None,
    message: Optional[str] = None,
    heartbeat_at: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Persist a board-agent liveness snapshot in the device registry.

    The registry remains the device configuration source of truth.  This
    snapshot is deliberately separate from the retired project/OTA Agent
    control plane and is consumed only by BM1688 validation scheduling.
    """
    record = find_device_record(device_id)
    if record is None:
        return None
    if str(record.get("validation_channel") or "ssh").strip().lower() != "board_agent":
        raise ValueError("只有板端主动拉取设备可以上报板端心跳")
    checked_at = heartbeat_at or _utc_now_iso()
    changes: dict[str, Any] = {
        "board_agent_status": "online",
        "board_agent_last_heartbeat_at": checked_at,
        "board_agent_environment_ready": environment_ready,
        "board_agent_capabilities": capabilities or {},
        "board_agent_message": message or "板端心跳已接收",
    }
    # A process restart can leave the durable bootstrap marker at
    # WAITING_HEARTBEAT, or a bounded heartbeat wait can finish as a
    # retryable FAILED state, before the resident service publishes its first
    # pulse.  A verified ready heartbeat is the authoritative completion
    # signal for those exact heartbeat states.  Restricting completion to the
    # explicit timeout codes prevents an unrelated SSH/install failure from
    # being made READY by an old or replacement Agent pulse.
    state = record.get("agent_bootstrap")
    if not isinstance(state, dict):
        report = record.get("environment_report")
        nested = report.get("agent_bootstrap") if isinstance(report, dict) else None
        state = nested if isinstance(nested, dict) else {}
    state_status = str(state.get("status") or "").strip().upper()
    state_code = str(state.get("code") or "").strip().lower()
    try:
        ssh_password = resolve_device_password(dict(record))
    except Exception:
        ssh_password = ""
    try:
        ssh_private_key_path = resolve_device_private_key_path(dict(record))
    except Exception:
        ssh_private_key_path = ""
    has_ssh_secret = bool(
        str(ssh_password or record.get("password") or "").strip()
        or str(ssh_private_key_path or "").strip()
    )
    can_complete_from_heartbeat = (
        state_status in {"STARTING", "WAITING_HEARTBEAT"}
        and (not has_ssh_secret or state_code in _HEARTBEAT_RETRYABLE_BOOTSTRAP_CODES)
    ) or (
        state_status == "FAILED" and state_code in _HEARTBEAT_RETRYABLE_BOOTSTRAP_CODES
    )
    if environment_ready and can_complete_from_heartbeat:
        completed_at = _utc_now_iso()
        completed_state = {
            **dict(state),
            "status": "READY",
            "stage": "heartbeat",
            "code": "ready",
            "message": "Board Agent 心跳已确认，设备可进入任务调度池",
            "completed_at": completed_at,
            "heartbeat_deadline_at": None,
            "environment_ready": True,
            "updated_at": completed_at,
        }
        report = dict(record.get("environment_report") or {})
        report["agent_bootstrap"] = dict(completed_state)
        changes.update(
            {
                "agent_bootstrap": completed_state,
                "environment_report": report,
                "environment_status": "ready",
                "environment_stage": "agent_heartbeat",
                "environment_code": "ready",
                "environment_message": completed_state["message"],
                "environment_checked_at": checked_at,
            }
        )
    return update_device_record(
        device_id,
        password_secret_action=(
            "clear" if environment_ready and can_complete_from_heartbeat else "keep"
        ),
        private_key_path_secret_action=(
            "clear" if environment_ready and can_complete_from_heartbeat else "keep"
        ),
        **changes,
    )


@_locked_registry_mutation
def delete_device_record(device_id: str) -> None:
    target = str(device_id or "").strip()
    store = load_device_store()
    current_devices = list(store.get("devices") or [])
    if (
        str(store.get("active_device_id") or "").strip() == target
        and len(current_devices) > 1
    ):
        raise ValueError("当前启用设备不能直接删除，请先切换到其他设备")
    devices = [item for item in current_devices if str(item.get("id") or "") != target]
    if len(devices) == len(current_devices):
        raise ValueError(f"边端设备不存在: {device_id}")
    store["devices"] = devices
    if str(store.get("active_device_id") or "").strip() == target:
        store["active_device_id"] = str(devices[0]["id"]) if devices else ""
    _write_device_store(store)


def load_active_device_config() -> dict[str, Any]:
    active = get_active_device_record()
    if active is not None:
        return _record_to_runtime_config(active)
    return {
        **_default_device_payload(),
        "enabled": False,
        "password": "",
        "password_source_type": "",
        "password_masked_hint": "",
        "password_masked": "",
        "password_is_configured": False,
        "password_updated_at": None,
        "private_key_path": "",
        "private_key_path_source_type": "",
        "private_key_path_masked": "",
        "private_key_path_configured": False,
        "private_key_path_runtime_available": False,
        "private_key_path_updated_at": None,
        "device_id": "",
        "device_name": "",
        "probe_status": "unknown",
        "probe_stage": "",
        "probe_code": "",
        "probe_message": "",
        "probe_checked_at": None,
    }


def load_device_config(device_id: str) -> dict[str, Any]:
    record = find_device_record(device_id)
    if record is None:
        return {}
    return _record_to_runtime_config(record)


def mark_device_used(
    device_id: str,
    *,
    used_at: str | None = None,
) -> Optional[dict[str, Any]]:
    if find_device_record(device_id) is None:
        return None
    return update_device_record(device_id, last_used_at=used_at or _utc_now_iso())


def mark_active_device_used(*, used_at: str | None = None) -> Optional[dict[str, Any]]:
    active = get_active_device_record()
    if active is None:
        return None
    return mark_device_used(active["id"], used_at=used_at)


def mark_device_validation(
    device_id: str,
    status: str,
    *,
    validated_at: str | None = None,
) -> Optional[dict[str, Any]]:
    if find_device_record(device_id) is None:
        return None
    return update_device_record(
        device_id,
        last_validation_status=str(status or "").strip()[:64],
        last_validation_at=validated_at or _utc_now_iso(),
    )


def mark_active_device_validation(
    status: str,
    *,
    validated_at: str | None = None,
) -> Optional[dict[str, Any]]:
    active = get_active_device_record()
    if active is None:
        return None
    return mark_device_validation(active["id"], status, validated_at=validated_at)


def save_active_device_config(data: Dict[str, Any]) -> dict[str, Any]:
    active = get_active_device_record()
    payload = dict(data or {})
    password_secret_action = str(
        payload.pop("_password_secret_action", "") or ""
    ).strip().lower()
    if password_secret_action not in {"keep", "set", "clear"}:
        password_secret_action = "set" if payload.get("password") else "keep"
    private_key_path_secret_action = str(
        payload.pop("_private_key_path_secret_action", "") or ""
    ).strip().lower()
    if private_key_path_secret_action not in {"keep", "set", "clear"}:
        private_key_path_secret_action = (
            "set" if payload.get("private_key_path") else "keep"
        )
    if active is None:
        created = create_device_record(
            name=str(
                payload.get("device_name") or payload.get("name") or DEFAULT_DEVICE_NAME
            ),
            host=str(payload.get("host") or "").strip(),
            user=str(payload.get("user") or "linaro").strip(),
            port=int(payload.get("port") or 22),
            password=str(payload.get("password") or ""),
            private_key_path=str(payload.get("private_key_path") or ""),
            edge_root=payload.get("edge_root"),
            remote_base=payload.get("remote_base"),
            sophon_demo_root=payload.get("sophon_demo_root"),
            auto_after_export=bool(payload.get("auto_after_export", True)),
            run_coco_eval=bool(payload.get("run_coco_eval")),
            connect_timeout_sec=int(payload.get("connect_timeout_sec") or 30),
            validation_timeout_sec=int(payload.get("validation_timeout_sec") or 7200),
            docker_container=str(payload.get("docker_container") or ""),
            docker_use_sudo=bool(payload.get("docker_use_sudo", True)),
            enabled=bool(payload.get("enabled", True)),
            set_active=True,
        )
        if any(
            key in payload
            for key in (
                "probe_status",
                "probe_stage",
                "probe_code",
                "probe_message",
                "probe_checked_at",
            )
        ):
            created = update_device_record(
                created["id"],
                probe_status=payload.get("probe_status"),
                probe_stage=payload.get("probe_stage"),
                probe_code=payload.get("probe_code"),
                probe_message=payload.get("probe_message"),
                probe_checked_at=payload.get("probe_checked_at"),
            )
        return _record_to_runtime_config(created)
    updated = update_device_record(
        active["id"],
        name=payload.get("device_name") or payload.get("name"),
        host=payload.get("host"),
        port=payload.get("port"),
        user=payload.get("user"),
        password_secret_action=password_secret_action,
        password=payload.get("password"),
        private_key_path_secret_action=private_key_path_secret_action,
        private_key_path=payload.get("private_key_path"),
        edge_root=payload.get("edge_root"),
        remote_base=payload.get("remote_base"),
        sophon_demo_root=payload.get("sophon_demo_root"),
        auto_after_export=payload.get("auto_after_export"),
        run_coco_eval=payload.get("run_coco_eval"),
        connect_timeout_sec=payload.get("connect_timeout_sec"),
        validation_timeout_sec=payload.get("validation_timeout_sec"),
        docker_container=payload.get("docker_container"),
        docker_use_sudo=payload.get("docker_use_sudo"),
        enabled=payload.get("enabled"),
        probe_status=payload.get("probe_status"),
        probe_stage=payload.get("probe_stage"),
        probe_code=payload.get("probe_code"),
        probe_message=payload.get("probe_message"),
        probe_checked_at=payload.get("probe_checked_at"),
    )
    return _record_to_runtime_config(updated)
