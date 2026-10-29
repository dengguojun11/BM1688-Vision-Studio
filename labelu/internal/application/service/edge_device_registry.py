"""边端设备注册表服务。"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import status
from loguru import logger

from labelu.internal.application.command.edge_device_registry import (
    CreateEdgeDeviceCommand,
    PatchEdgeDeviceCommand,
    SetActiveEdgeDeviceCommand,
)
from labelu.internal.application.response.edge_device_registry import (
    EdgeDeviceListResponse,
    EdgeDeviceResponse,
)
from labelu.internal.application.response.edge_ssh_config import EdgeSshProbeResponse
from labelu.internal.application.command.edge_agent_bootstrap import (
    BootstrapEdgeAgentCommand,
)
from labelu.internal.common import edge_device_registry as registry_io
from labelu.internal.common import edge_agent_control_plane
from labelu.internal.common.config import settings
from labelu.internal.common.edge_ssh_probe import probe_edge_ssh_target
from labelu.internal.common.edge_ssh_client import ssh_connect
from labelu.internal.application.service import edge_runtime_provision
from labelu.internal.application.service import edge_agent_bootstrap
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.db import session_scope
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob

_DOC_HINT = (
    "边端设备支持多设备 CRUD、健康探测与任务调度。设备配置和加密密码统一存储在 MySQL。"
    "边端验证会从已启用、在线且空闲的设备中按最久未使用优先分配；无空闲设备时任务进入 FIFO 队列。"
)

_ENVIRONMENT_LOCKS_GUARD = threading.Lock()
_ENVIRONMENT_LOCKS: dict[str, threading.Lock] = {}
_SYSTEM_ENVIRONMENT_BOOTSTRAP_LOCK = threading.Lock()
_SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED = False
# A registry mutation lock can be held briefly by another web/worker process
# while a device is being created, edited, or claimed.  Do not strand an
# otherwise valid SSH device at ``NOT_STARTED`` when that happens: retry the
# Agent queue operation in-process with bounded exponential backoff.  Startup
# recovery below is the durable fallback for a process restart.
_AUTO_AGENT_RETRY_BASE_DELAY_SECONDS = 2.0
_AUTO_AGENT_RETRY_MAX_DELAY_SECONDS = 30.0
_AUTO_AGENT_RETRY_LOCK = threading.Lock()
_AUTO_AGENT_RETRY_TIMERS: dict[str, threading.Timer] = {}
_AUTO_AGENT_RETRY_ATTEMPTS: dict[str, int] = {}
_AUTO_AGENT_RETRYABLE_CODES = frozenset(
    {
        "device_mutation_lock_unavailable",
        "bootstrap_state_persist_failed",
        # These failures are produced before any remote work starts when the
        # production control-plane contract is not loaded yet.  Once the
        # operator supplies the static configuration and the process restarts,
        # startup recovery can safely resume the persisted enrollment.
        "production_env_required",
        "control_plane_disabled",
    }
)
# Keep the SSH read-model liveness window identical to the scheduler.  A
# persisted successful probe is only evidence of connectivity for this short
# interval; after it expires the UI must fail closed until a fresh probe runs.
SSH_PROBE_TTL_SECONDS = 120


def _environment_lock(device_id: str) -> threading.Lock:
    key = str(device_id or "").strip()
    with _ENVIRONMENT_LOCKS_GUARD:
        return _ENVIRONMENT_LOCKS.setdefault(key, threading.Lock())


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _assert_yolo_enabled() -> None:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def _not_found(device_id: str) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_404_NOT_FOUND,
        message=f"边端设备不存在: {device_id}",
    )


def _bad_request(message: str) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_400_BAD_REQUEST,
        message=message,
    )


def _device_mutation_lock_unavailable(operation: str) -> LabelUException:
    """Fail closed when another worker is changing the edge registry."""

    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        message=f"边端设备正在执行其他变更，暂不能{operation}；请稍后重试",
    )


def _password_configured(record: dict) -> bool:
    return bool(str(record.get("password_encrypted") or "").strip())


def _private_key_path_configured(record: dict) -> bool:
    return bool(str(record.get("private_key_path_encrypted") or "").strip())


def _uses_board_agent(record: dict | None) -> bool:
    return (
        str((record or {}).get("validation_channel") or "ssh").strip().lower()
        == "board_agent"
    )


def _parse_iso_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _board_agent_heartbeat_is_fresh(
    record: dict,
    *,
    now: datetime | None = None,
) -> bool:
    """Use the same liveness TTL as scheduling for read/probe responses."""

    if str(record.get("board_agent_status") or "").strip().lower() != "online":
        return False
    checked_at = _parse_iso_datetime(record.get("board_agent_last_heartbeat_at"))
    if checked_at is None:
        return False
    ttl_seconds = max(
        10,
        int(getattr(settings, "BOARD_AGENT_HEARTBEAT_TTL_SECONDS", 120) or 120),
    )
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_seconds = (current.astimezone(timezone.utc) - checked_at).total_seconds()
    # A clock-skewed future heartbeat must not keep a device schedulable
    # indefinitely.  The frontend applies the same lower bound when it
    # projects the heartbeat timestamp.
    return 0 <= age_seconds <= ttl_seconds


def ssh_probe_is_fresh(
    record: dict,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether the persisted SSH probe is fresh enough for dispatch.

    The scheduler and queue center use the same contract.  Keeping this small
    helper on the registry service prevents the device-management projection
    from showing a stale ``last_probe_status=ok`` record as available.
    """

    if (
        str(record.get("last_probe_status") or record.get("probe_status") or "")
        .strip()
        .lower()
        != "ok"
    ):
        return False
    checked_at = _parse_iso_datetime(
        record.get("last_probe_checked_at") or record.get("probe_checked_at")
    )
    if checked_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_seconds = (current.astimezone(timezone.utc) - checked_at).total_seconds()
    return 0 <= age_seconds <= SSH_PROBE_TTL_SECONDS


def _has_complete_runtime_config(record: dict) -> bool:
    if not isinstance(record, dict) or not bool(record.get("enabled", True)):
        return False
    cfg = registry_io._record_to_runtime_config(record)
    return bool(
        str(cfg.get("host") or "").strip()
        and str(cfg.get("user") or "").strip()
        and (
            str(cfg.get("password") or "").strip()
            or str(cfg.get("private_key_path") or "").strip()
        )
    )


def _agent_bootstrap_control_plane_enabled() -> bool:
    """Whether automatic SSH enrollment is allowed for this process."""
    return edge_agent_control_plane.control_plane_enabled()


def _agent_control_plane_projection() -> tuple[str, str, bool]:
    """Return non-secret control-plane diagnostics for the admin projection."""

    try:
        config = edge_agent_control_plane.resolve_control_plane()
    except Exception as exc:
        logger.warning("resolve edge Agent control-plane config failed: {}", exc)
        return "unavailable", "边端 Agent 控制面配置读取失败", False
    return (
        str(config.source or "default"),
        str(config.message or ""),
        edge_agent_control_plane.public_base_url_configured(),
    )


def _auto_configure_agent_for_page(
    record: dict, *, updated_by: int | None = None
) -> tuple[bool, str, str]:
    """Provision DB-backed Agent settings for an opted-in page save."""

    result = edge_agent_control_plane.ensure_auto_configured(
        device_host=str(record.get("host") or "").strip() or None,
        updated_by=updated_by,
    )
    if not result.ok:
        logger.warning(
            "automatic edge Agent control-plane setup failed device_id={} code={} message={}",
            str(record.get("id") or ""),
            result.code,
            result.message[:300],
        )
    return result.ok, result.code, result.message


def _persist_page_auto_config_failure(
    device_id: str, *, code: str, message: str
) -> bool:
    """Expose an automatic setup failure through the normal device projection."""

    if not code and not message:
        return False
    state = {
        "status": "FAILED",
        "stage": "control_plane",
        "code": str(code or "control_plane_setup_failed")[:96],
        "message": str(message or "边端 Agent 控制面自动配置失败")[:800],
        "updated_at": _utc_now_iso(),
    }
    state["completed_at"] = state["updated_at"]
    try:
        current = registry_io.find_device_record(device_id)
        report = (
            dict(current.get("environment_report"))
            if isinstance(current, dict)
            and isinstance(current.get("environment_report"), dict)
            else {}
        )
        report["agent_bootstrap"] = dict(state)
        report["environment_ready"] = False
        registry_io.update_device_record(
            device_id,
            agent_bootstrap=state,
            environment_report=report,
            environment_status="failed",
            environment_stage="control_plane",
            environment_code=state["code"],
            environment_message=state["message"],
            environment_checked_at=state["updated_at"],
        )
        return True
    except Exception:
        logger.exception(
            "persist page Agent control-plane failure failed device_id={}", device_id
        )
        return False


def _agent_bootstrap_in_progress(record: dict | None) -> bool:
    """Use the durable Agent marker as a scheduler/environment-check guard."""

    return edge_agent_bootstrap.bootstrap_in_progress(record)


def _auto_agent_bootstrap_enabled(record: dict | None) -> bool:
    """Return whether a newly configured SSH device should self-enroll.

    The automatic path is deliberately gated by the unified Board Agent
    control-plane switches.  When those switches are off, the legacy SSH
    environment check remains the only side effect of saving a device.
    """

    if not isinstance(record, dict) or not _has_complete_runtime_config(record):
        return False
    if str(record.get("validation_channel") or "ssh").strip().lower() != "ssh":
        return False
    return _agent_bootstrap_control_plane_enabled()


def agent_bootstrap_dispatch_ready(record: dict | None) -> bool:
    """Return whether the configured control plane permits SSH dispatch.

    When automatic enrollment is enabled, an SSH record is only a temporary
    onboarding state.  A stale legacy environment report must not make it
    schedulable before the resident Agent has completed its heartbeat-backed
    transition.  Board-Agent records use their own heartbeat/environment gate
    and therefore are left to the normal pull-channel projection.
    """

    if not isinstance(record, dict):
        return False
    # An explicit failed enrollment is a durable safety signal for every
    # channel.  In particular, a Board Agent may still have a fresh heartbeat
    # and report ``board_agent_environment_ready=true`` after the SSH
    # bootstrap worker failed; those independent liveness fields must not
    # resurrect a device that the bootstrap lifecycle has marked unusable.
    # Keep an absent/empty marker permissive so a manually installed Board
    # Agent (which has no server-side bootstrap record) can be scheduled by
    # its heartbeat and environment report.
    if _agent_bootstrap_status(record) == "FAILED":
        return False
    if not _agent_bootstrap_control_plane_enabled():
        return True
    channel = str(record.get("validation_channel") or "ssh").strip().lower()
    if channel != "ssh":
        return True
    return _agent_bootstrap_status(record) == "READY"


_RUNTIME_CONFIG_FIELDS = (
    "enabled",
    "validation_channel",
    "host",
    "port",
    "user",
    "edge_root",
    "remote_base",
    "sophon_demo_root",
    "docker_container",
    "docker_use_sudo",
    "connect_timeout_sec",
    "validation_timeout_sec",
    "private_key_path_configured",
)


def _agent_bootstrap_status(record: dict | None) -> str:
    """Read the canonical Agent state while supporting older saved records."""

    if not isinstance(record, dict):
        return ""
    report = record.get("environment_report")
    nested = report.get("agent_bootstrap") if isinstance(report, dict) else None
    status = nested.get("status") if isinstance(nested, dict) else None
    top_level = record.get("agent_bootstrap")
    if isinstance(top_level, dict):
        top_level_status = str(top_level.get("status") or "").strip()
        if top_level_status:
            status = top_level_status
    return str(status or "").strip().upper()


def _agent_bootstrap_state_value(record: dict | None, key: str) -> str:
    """Read one lifecycle field from either the canonical or mirror state."""

    if not isinstance(record, dict):
        return ""
    report = record.get("environment_report")
    nested = report.get("agent_bootstrap") if isinstance(report, dict) else None
    value = nested.get(key) if isinstance(nested, dict) else None
    top_level = record.get("agent_bootstrap")
    if isinstance(top_level, dict) and str(top_level.get(key) or "").strip():
        value = top_level.get(key)
    return str(value or "").strip()


def _auto_agent_bootstrap_retryable(record: dict | None) -> bool:
    """Return whether startup may retry automatic enrollment for this record.

    A missing marker is the normal state immediately after a device is saved.
    Coordination/persistence failures and control-plane configuration gates
    are retryable once a lifecycle run has reached ``FAILED``.  Authentication,
    artifact, and remote configuration failures remain operator-actionable
    terminal states.
    """

    status = _agent_bootstrap_status(record)
    if status in {"", "NOT_STARTED"}:
        return True
    return status == "FAILED" and (
        _agent_bootstrap_state_value(record, "code").lower()
        in _AUTO_AGENT_RETRYABLE_CODES
    )


def _clear_auto_agent_bootstrap_retry(device_id: str) -> None:
    """Cancel a pending in-process retry and reset its backoff counter."""

    key = str(device_id or "").strip()
    if not key:
        return
    with _AUTO_AGENT_RETRY_LOCK:
        timer = _AUTO_AGENT_RETRY_TIMERS.pop(key, None)
        _AUTO_AGENT_RETRY_ATTEMPTS.pop(key, None)
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            logger.debug("cancel edge Agent retry timer failed device_id={}", key)


def _run_auto_agent_bootstrap_retry(device_id: str) -> None:
    """Run one deferred queue attempt after a mutation-lock collision."""

    key = str(device_id or "").strip()
    if not key:
        return
    with _AUTO_AGENT_RETRY_LOCK:
        # The timer is removed before running so a fresh lock failure can
        # schedule the next backoff interval without being deduplicated away.
        _AUTO_AGENT_RETRY_TIMERS.pop(key, None)
    try:
        record = registry_io.find_device_record(key)
    except Exception:
        logger.exception("read edge device for deferred Agent retry failed device_id={}", key)
        return
    if not _auto_agent_bootstrap_enabled(record) or not _auto_agent_bootstrap_retryable(
        record
    ):
        _clear_auto_agent_bootstrap_retry(key)
        return
    active_job_id = _active_processing_job_id(key)
    if active_job_id is not None:
        # A task owns the device.  Keep the durable state untouched, but retain
        # the bounded-backoff timer so a long-running task cannot permanently
        # strand an otherwise valid automatic enrollment.  The next attempt
        # re-checks the job ownership before touching SSH or systemd.
        logger.info(
            "defer automatic edge Agent retry for busy device_id={} job_id={}",
            key,
            active_job_id,
        )
        _schedule_auto_agent_bootstrap_retry(key)
        return
    _start_auto_agent_bootstrap(key)


def _schedule_auto_agent_bootstrap_retry(device_id: str) -> bool:
    """Schedule one deduplicated retry after a transient registry collision."""

    key = str(device_id or "").strip()
    if not key:
        return False
    with _AUTO_AGENT_RETRY_LOCK:
        existing = _AUTO_AGENT_RETRY_TIMERS.get(key)
        if existing is not None and existing.is_alive():
            return False
        attempt = int(_AUTO_AGENT_RETRY_ATTEMPTS.get(key, 0) or 0) + 1
        _AUTO_AGENT_RETRY_ATTEMPTS[key] = attempt
        delay = min(
            _AUTO_AGENT_RETRY_BASE_DELAY_SECONDS * (2 ** min(attempt - 1, 8)),
            _AUTO_AGENT_RETRY_MAX_DELAY_SECONDS,
        )
        timer = threading.Timer(
            max(0.0, float(delay)),
            _run_auto_agent_bootstrap_retry,
            args=(key,),
        )
        timer.daemon = True
        _AUTO_AGENT_RETRY_TIMERS[key] = timer
    try:
        timer.start()
    except Exception:
        with _AUTO_AGENT_RETRY_LOCK:
            if _AUTO_AGENT_RETRY_TIMERS.get(key) is timer:
                _AUTO_AGENT_RETRY_TIMERS.pop(key, None)
        logger.exception("schedule automatic edge Agent retry failed device_id={}", key)
        return False
    logger.info(
        "scheduled automatic edge Agent retry device_id={} attempt={} delay_seconds={}",
        key,
        attempt,
        delay,
    )
    return True


def _runtime_config_changed(
    before: dict | None,
    after: dict,
    *,
    password_secret_action: str = "keep",
    private_key_path_secret_action: str = "keep",
) -> bool:
    """Return whether a PATCH changed anything needed by remote execution."""

    if not isinstance(before, dict):
        return True
    for field in _RUNTIME_CONFIG_FIELDS:
        if before.get(field) != after.get(field):
            return True

    # ``set`` and ``clear`` are explicit changes even when a caller happens to
    # submit the same masked value.  For ``keep``, compare only secret state;
    # the ciphertext itself may be re-encrypted by a storage migration.
    action = str(password_secret_action or "keep").strip().lower()
    key_action = str(private_key_path_secret_action or "keep").strip().lower()
    if action in {"set", "clear"} or key_action in {"set", "clear"}:
        return True
    return bool(before.get("password_encrypted")) != bool(after.get("password_encrypted")) or bool(
        before.get("private_key_path_encrypted")
    ) != bool(after.get("private_key_path_encrypted"))


def _legacy_environment_is_ready(record: dict) -> bool:
    report = record.get("environment_report")
    return bool(
        str(record.get("environment_status") or "").strip().lower() == "ready"
        and isinstance(report, dict)
        and report.get("environment_ready") is True
    )


def _should_reconcile_after_update(
    before: dict | None,
    after: dict,
    *,
    password_secret_action: str = "keep",
    private_key_path_secret_action: str = "keep",
) -> bool:
    """Decide whether saving a device warrants remote setup work.

    Metadata and dispatch-strategy edits are intentionally side-effect free
    once an Agent is READY. A first setup, a runtime change, a failed/pending
    Agent, or a switch back to SSH still gets the normal reconciliation path;
    Board-Agent records remain pull-channel managed even if legacy SSH fields
    are retained for migration or display.
    """

    before_channel = str((before or {}).get("validation_channel") or "ssh")
    after_channel = str(after.get("validation_channel") or "ssh")
    if (
        before_channel.strip().lower() != "ssh"
        and after_channel.strip().lower() == "ssh"
    ):
        return True

    # Board-Agent devices are pull-channel managed. Older records can retain
    # encrypted SSH credentials from a previous channel, but those fields do
    # not make a metadata/runtime PATCH an instruction to run the legacy SSH
    # environment worker. Only an explicit switch back to ``ssh`` above
    # should re-enter that reconciliation path.
    if after_channel.strip().lower() == "board_agent":
        return False

    if _runtime_config_changed(
        before,
        after,
        password_secret_action=password_secret_action,
        private_key_path_secret_action=private_key_path_secret_action,
    ):
        return True

    if _auto_agent_bootstrap_enabled(after):
        return _agent_bootstrap_status(after) != "READY"
    return _has_complete_runtime_config(after) and not _legacy_environment_is_ready(
        after
    )


_ACTIVE_JOB_IMMUTABLE_FIELDS = (
    "enabled",
    "validation_channel",
    "host",
    "port",
    "user",
    "edge_root",
    "remote_base",
    "sophon_demo_root",
    "docker_container",
    "docker_use_sudo",
    "connect_timeout_sec",
    "validation_timeout_sec",
)


def _active_job_runtime_patch_changed(
    before: dict | None,
    cmd: PatchEdgeDeviceCommand,
) -> bool:
    """Check a PATCH without writing it, for the active-job safety gate."""

    if not isinstance(before, dict):
        return True
    if (
        str(cmd.password_secret_action or "keep").strip().lower() in {"set", "clear"}
        or str(cmd.private_key_path_secret_action or "keep").strip().lower()
        in {"set", "clear"}
        or cmd.clear_private_key_path is True
        or str(cmd.private_key_path or "").strip()
    ):
        return True

    candidate = dict(before)
    supplied_fields: list[str] = []
    for field in _ACTIVE_JOB_IMMUTABLE_FIELDS:
        value = getattr(cmd, field, None)
        if value is not None:
            candidate[field] = value
            supplied_fields.append(field)
    if not supplied_fields:
        return False
    try:
        normalized = registry_io._normalize_device_record(candidate)
    except Exception:
        # Let the normal update path return its validation error.  If it would
        # alter a live job, fail closed rather than guessing that it is safe.
        return True
    return any(before.get(field) != normalized.get(field) for field in supplied_fields)


def _active_processing_job_id(device_id: str) -> int | None:
    """Return the running job occupying a device, including legacy affinity."""

    try:
        active_device_id = registry_io.get_active_device_id()
        return _busy_job_map(active_device_id).get(str(device_id or "").strip())
    except Exception:
        logger.exception(
            "read active edge job before device update failed device_id={}",
            device_id,
        )
        # The registry write is independent from the job DB.  Do not permit a
        # potentially unsafe remote-config change when the guard cannot read
        # the current execution state.
        return -1


def _assert_no_active_job_operation(device_id: str, *, operation: str) -> None:
    """Reject registry mutations that could change a live task's device.

    The registry is also the compatibility source for jobs that predate an
    explicit ``edge_device_id``.  In that case ``_active_processing_job_id``
    resolves the job through the current active-device affinity, so callers
    must perform this check before deleting or switching a device.
    """

    active_job_id = _active_processing_job_id(device_id)
    if active_job_id is None:
        return
    if active_job_id == -1:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=f"无法确认边端任务状态，暂不能{operation}",
        )
    raise LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_409_CONFLICT,
        message=(
            f"边端设备正在执行任务 #{active_job_id}，暂不能{operation}；"
            "任务结束后再重试"
        ),
    )


def _assert_no_active_job_runtime_change(
    device_id: str,
    before: dict | None,
    cmd: PatchEdgeDeviceCommand,
) -> None:
    if not _active_job_runtime_patch_changed(before, cmd):
        return
    _assert_no_active_job_operation(
        device_id,
        operation="修改运行中设备的连接配置",
    )


def assert_no_active_job_for_environment_check(device_id: str) -> None:
    """Reject a manual environment check while the device runs a job.

    Environment checks are not read-only: the worker may clean remote logs or
    install/replace the BM1688 runtime.  Keep the public guard separate from
    ``start_environment_check`` so automatic reconciliation can safely skip a
    busy device without turning an otherwise harmless metadata update into a
    failed request.
    """

    _assert_no_active_job_operation(device_id, operation="启动环境检查")


def assert_no_active_job_for_legacy_ssh_config(device_id: str) -> None:
    """Protect the retired single-device SSH facade from live config edits."""

    _assert_no_active_job_operation(device_id, operation="修改运行中设备的连接配置")


def _start_auto_agent_bootstrap(device_id: str) -> bool:
    """Queue a non-blocking SSH Agent installation and report failures safely."""

    try:
        edge_agent_bootstrap.start_bootstrap_background(
            BootstrapEdgeAgentCommand(
                device_id=str(device_id),
                confirm_install=True,
                wait_for_heartbeat=True,
                provision_runtime=True,
                require_environment_ready=True,
            )
        )
        _clear_auto_agent_bootstrap_retry(device_id)
        return True
    except edge_agent_bootstrap.EdgeAgentBootstrapError as exc:
        if exc.code in _AUTO_AGENT_RETRYABLE_CODES:
            # Lock contention and a failed durable-state write are transient
            # coordination/storage results, not bootstrap validation failures.
            # Do not overwrite an active QUEUED/INSTALLING marker with FAILED
            # while another worker owns the mutation window; retry after the
            # competing write has released the registry.
            logger.info(
                "defer automatic edge agent bootstrap after transient queue failure device_id={} code={}",
                device_id,
                exc.code,
            )
            _schedule_auto_agent_bootstrap_retry(device_id)
            return False
        # Artifact/config validation runs synchronously before the worker is
        # queued.  Persist that failure so the device page can explain why it
        # remains unavailable instead of showing only a legacy environment
        # check result.
        try:
            failure = edge_agent_bootstrap.persist_bootstrap_failure(
                device_id,
                exc,
                stage="validation",
            )
            safe_message = failure.message
        except Exception:
            logger.exception(
                "persist synchronous edge agent bootstrap failure failed device_id={}",
                device_id,
            )
            safe_message = "同步预检失败（失败状态保存失败）"
        logger.warning(
            "automatic edge agent bootstrap validation failed device_id={} code={} message={}",
            device_id,
            exc.code,
            safe_message[:300],
        )
        _clear_auto_agent_bootstrap_retry(device_id)
        return False
    except Exception as exc:
        # Keep the automatic path fail-closed even for an unexpected queueing
        # error.  The legacy environment checker is deliberately not a
        # fallback because it can publish a stale successful SSH report.
        try:
            failure = edge_agent_bootstrap.persist_bootstrap_failure(
                device_id,
                exc,
                stage="queue",
            )
            safe_message = failure.message
        except Exception:
            logger.exception(
                "persist unexpected edge agent bootstrap failure failed device_id={}",
                device_id,
            )
            safe_message = "Agent 自动引导异常（失败状态保存失败）"
        logger.warning(
            "queue automatic edge agent bootstrap failed device_id={} code={} message={}",
            device_id,
            getattr(exc, "code", exc.__class__.__name__),
            safe_message[:300],
        )
        _clear_auto_agent_bootstrap_retry(device_id)
        return False


def _merge_persisted_agent_projection(
    record: dict[str, Any], device_id: str
) -> dict[str, Any]:
    """Refresh only lifecycle fields after queueing an Agent run.

    A caller may have just patched connection metadata, while a compatibility
    adapter or a concurrent registry read can still return an older full
    record.  Copying the whole snapshot would lose the caller's updated host
    or paths in the HTTP response.  Merge the durable Agent/environment fields
    only; the updated record remains authoritative for configuration fields.
    """

    try:
        persisted = registry_io.find_device_record(device_id)
    except Exception:
        return record
    if not isinstance(persisted, dict):
        return record
    for field in (
        "agent_bootstrap",
        "environment_report",
        "environment_status",
        "environment_stage",
        "environment_code",
        "environment_message",
        "environment_checked_at",
    ):
        if field in persisted:
            record[field] = persisted[field]
    return record


def _device_to_view(
    record: dict,
    *,
    active_device_id: str,
    busy_job_id: int | None = None,
    include_sensitive: bool = True,
) -> EdgeDeviceResponse:
    enabled = bool(record.get("enabled", True))
    board_agent = _uses_board_agent(record)
    probe_ok = (
        _board_agent_heartbeat_is_fresh(record)
        if board_agent
        else ssh_probe_is_fresh(record)
    )
    environment_report = (
        dict(record.get("environment_report"))
        if isinstance(record.get("environment_report"), dict)
        else {}
    )
    agent_bootstrap: dict = {}
    if isinstance(environment_report.get("agent_bootstrap"), dict):
        agent_bootstrap.update(environment_report["agent_bootstrap"])
    if isinstance(record.get("agent_bootstrap"), dict):
        # The top-level field is canonical, while the nested value remains a
        # compatibility mirror for older clients.
        agent_bootstrap.update(record["agent_bootstrap"])
    bootstrap_active = _agent_bootstrap_in_progress(record)
    control_plane_source, control_plane_message, public_url_configured = (
        _agent_control_plane_projection()
    )
    environment_ready = (
        bool(record.get("board_agent_environment_ready"))
        if board_agent
        else str(record.get("environment_status") or "unknown") == "ready"
        and environment_report.get("environment_ready") is True
    )
    # The legacy SSH readiness fields are not sufficient while the automatic
    # Agent control plane owns this device.  Keep the projection and scheduler
    # on the same fail-closed contract.
    if not agent_bootstrap_dispatch_ready(record):
        environment_ready = False
    if not enabled:
        scheduling_status = "disabled"
    elif busy_job_id is not None:
        scheduling_status = "busy"
    elif bootstrap_active:
        # A stale successful SSH probe/environment report must not make a
        # device appear dispatchable while the Agent installer is replacing
        # its runtime or waiting for the first heartbeat.
        scheduling_status = "agent_bootstrap_in_progress"
    elif not environment_ready:
        scheduling_status = "environment_not_ready"
    elif probe_ok:
        scheduling_status = "idle"
    else:
        scheduling_status = "offline"
    # The registry is also used by scheduling and non-admin status pages.  Do
    # the projection at the service boundary so every GET caller gets the
    # same allow-list, and never accidentally serializes the raw registry
    # record.  Diagnostic strings are intentionally omitted for regular users;
    # they may contain remote paths, command output, or credential-source
    # details.  The finite status/stage fields remain available for dispatch
    # state and progress rendering.
    if not include_sensitive:
        environment_report = None

    return EdgeDeviceResponse(
        id=str(record.get("id") or ""),
        name=str(record.get("name") or ""),
        is_active=str(record.get("id") or "") == str(active_device_id or ""),
        enabled=enabled,
        host=str(record.get("host") or "") if include_sensitive else None,
        port=int(record.get("port") or 22) if include_sensitive else None,
        user=str(record.get("user") or "linaro") if include_sensitive else None,
        password_source_type=(
            str(record.get("password_source_type") or "") if include_sensitive else None
        ),
        password_masked_hint=(
            str(record.get("password_masked_hint") or "") if include_sensitive else None
        ),
        password_configured=_password_configured(record) if include_sensitive else None,
        password_runtime_available=(
            bool(registry_io.resolve_device_password(record))
            if include_sensitive
            else None
        ),
        password_updated_at=(
            record.get("password_updated_at") if include_sensitive else None
        ),
        private_key_path_source_type=(
            str(record.get("private_key_path_source_type") or "")
            if include_sensitive
            else None
        ),
        private_key_path_masked=(
            str(record.get("private_key_path_masked") or "")
            if include_sensitive
            else None
        ),
        private_key_path_configured=(
            bool(record.get("private_key_path_configured"))
            if include_sensitive
            else None
        ),
        private_key_path_runtime_available=(
            bool(record.get("private_key_path_runtime_available"))
            if include_sensitive
            else None
        ),
        private_key_path_updated_at=(
            record.get("private_key_path_updated_at") if include_sensitive else None
        ),
        edge_root=(
            str(record.get("edge_root") or "/data/labelu_edge")
            if include_sensitive
            else None
        ),
        remote_base=(
            str(record.get("remote_base") or "/data/labelu_edge/jobs")
            if include_sensitive
            else None
        ),
        sophon_demo_root=(
            str(record.get("sophon_demo_root") or "") if include_sensitive else None
        ),
        auto_after_export=bool(record.get("auto_after_export")),
        run_coco_eval=bool(record.get("run_coco_eval")),
        connect_timeout_sec=int(record.get("connect_timeout_sec") or 30),
        validation_timeout_sec=int(record.get("validation_timeout_sec") or 7200),
        docker_container=str(record.get("docker_container") or ""),
        docker_use_sudo=bool(record.get("docker_use_sudo", True)),
        validation_channel=str(record.get("validation_channel") or "ssh"),
        board_agent_status=str(record.get("board_agent_status") or "unknown"),
        board_agent_last_heartbeat_at=record.get("board_agent_last_heartbeat_at"),
        board_agent_environment_ready=bool(record.get("board_agent_environment_ready")),
        board_agent_capabilities=(
            (
                dict(record.get("board_agent_capabilities"))
                if isinstance(record.get("board_agent_capabilities"), dict)
                else {}
            )
            if include_sensitive
            else None
        ),
        board_agent_message=(
            str(record.get("board_agent_message") or "") if include_sensitive else None
        ),
        agent_bootstrap_status=str(agent_bootstrap.get("status") or "NOT_STARTED"),
        agent_bootstrap_control_plane_enabled=_agent_bootstrap_control_plane_enabled(),
        agent_control_plane_source=control_plane_source,
        agent_control_plane_message=control_plane_message or None,
        agent_control_plane_public_base_url_configured=public_url_configured,
        agent_bootstrap_stage=str(agent_bootstrap.get("stage") or ""),
        agent_bootstrap_code=(
            str(agent_bootstrap.get("code") or "") if include_sensitive else None
        ),
        agent_bootstrap_message=(
            str(agent_bootstrap.get("message") or "") if include_sensitive else None
        ),
        agent_bootstrap_version=(
            str(agent_bootstrap.get("agent_version") or "")
            if include_sensitive
            else None
        ),
        agent_bootstrap_updated_at=(
            agent_bootstrap.get("updated_at") if include_sensitive else None
        ),
        probe_status=str(record.get("probe_status") or "unknown"),
        probe_stage=str(record.get("probe_stage") or ""),
        probe_code=str(record.get("probe_code") or "") if include_sensitive else None,
        probe_message=(
            str(record.get("probe_message") or "") if include_sensitive else None
        ),
        probe_checked_at=record.get("probe_checked_at"),
        last_probe_status=str(record.get("last_probe_status") or "unknown"),
        last_probe_stage=str(record.get("last_probe_stage") or ""),
        last_probe_code=(
            str(record.get("last_probe_code") or "") if include_sensitive else None
        ),
        last_probe_message=(
            str(record.get("last_probe_message") or "") if include_sensitive else None
        ),
        last_probe_checked_at=record.get("last_probe_checked_at"),
        last_validation_status=str(record.get("last_validation_status") or ""),
        last_validation_at=record.get("last_validation_at"),
        environment_status=str(record.get("environment_status") or "unknown"),
        environment_stage=str(record.get("environment_stage") or ""),
        environment_code=(
            str(record.get("environment_code") or "") if include_sensitive else None
        ),
        environment_message=(
            str(record.get("environment_message") or "") if include_sensitive else None
        ),
        environment_checked_at=record.get("environment_checked_at"),
        environment_report=environment_report,
        last_used_at=record.get("last_used_at"),
        scheduling_status=scheduling_status,
        is_busy=busy_job_id is not None,
        active_job_id=busy_job_id,
        available_for_dispatch=(
            enabled
            and environment_ready
            and probe_ok
            and busy_job_id is None
            and not bootstrap_active
        ),
        created_at=record.get("created_at"),
        updated_at=record.get("updated_at"),
    )


def _busy_job_map(active_device_id: str) -> dict[str, int]:
    with session_scope() as db:
        rows = (
            db.query(YoloEdgeValidationJob.id, YoloEdgeValidationJob.edge_device_id)
            .filter(YoloEdgeValidationJob.status == "PROCESSING")
            .order_by(YoloEdgeValidationJob.id.asc())
            .all()
        )
    result: dict[str, int] = {}
    for job_id, device_id in rows:
        resolved_id = str(device_id or "").strip() or str(active_device_id or "")
        if resolved_id and resolved_id not in result:
            result[resolved_id] = int(job_id)
    return result


def _edge_device_matches_query(record: dict[str, Any], query: str | None) -> bool:
    """Match user-facing device fields without exposing secret values.

    The registry is a small configuration document rather than a relational
    table, so filtering is intentionally performed after loading the single
    document.  Keep this helper limited to stable identifiers and connection
    metadata; encrypted credential fields must never become searchable.
    """

    normalized = str(query or "").strip().casefold()
    if not normalized:
        return True
    searchable = " ".join(
        str(record.get(field) or "")
        for field in (
            "id",
            "name",
            "host",
            "user",
            "validation_channel",
            "board_agent_status",
            "environment_status",
        )
    )
    return normalized in searchable.casefold()


def _edge_device_matches_status(view: EdgeDeviceResponse, status_filter: str | None) -> bool:
    """Apply the existing UI status tabs on the server-side page."""

    normalized = str(status_filter or "all").strip().casefold()
    if not normalized or normalized == "all":
        return True
    if normalized == "ready":
        return view.available_for_dispatch is True
    if normalized == "blocked":
        return view.available_for_dispatch is not True
    if normalized == "active":
        return view.is_active is True
    if normalized == "disabled":
        return view.enabled is False
    # The router validates known values.  Treat an unknown value as no filter
    # for direct service callers and rolling upgrades instead of returning an
    # unexpectedly empty device list.
    return True


def _list_response(
    *,
    include_sensitive: bool = True,
    query: str | None = None,
    page: int | None = None,
    size: int | None = None,
    status_filter: str | None = None,
) -> EdgeDeviceListResponse:
    store = registry_io.load_device_store()
    active_device_id = str(store.get("active_device_id") or "")
    busy_jobs = _busy_job_map(active_device_id)
    devices = []
    for record in list(store.get("devices") or []):
        if not isinstance(record, dict) or not _edge_device_matches_query(record, query):
            continue
        view = _device_to_view(
            record,
            active_device_id=active_device_id,
            busy_job_id=busy_jobs.get(str(record.get("id") or "")),
            include_sensitive=include_sensitive,
        )
        if _edge_device_matches_status(view, status_filter):
            devices.append(view)

    total = len(devices)
    # ``None`` means legacy/unpaged mode.  The route only supplies both values
    # for a paged request, but keeping the guard here makes direct callers safe.
    if page is not None and size is not None:
        offset = page * size
        devices = devices[offset : offset + size]
    return EdgeDeviceListResponse(
        active_device_id=active_device_id,
        devices=devices,
        total=total,
        page=page,
        size=size if size is not None else len(devices),
        doc_hint=_DOC_HINT,
    )


def list_edge_devices(
    *,
    include_sensitive: bool = True,
    query: str | None = None,
    page: int | None = None,
    size: int | None = None,
    status_filter: str | None = None,
) -> EdgeDeviceListResponse:
    _assert_yolo_enabled()
    return _list_response(
        include_sensitive=include_sensitive,
        query=query,
        page=page,
        size=size,
        status_filter=status_filter,
    )


def get_edge_device(
    device_id: str, *, include_sensitive: bool = True
) -> EdgeDeviceResponse:
    _assert_yolo_enabled()
    record = registry_io.find_device_record(device_id)
    if record is None:
        raise _not_found(device_id)
    active_device_id = registry_io.get_active_device_id()
    return _device_to_view(
        record,
        active_device_id=active_device_id,
        busy_job_id=_busy_job_map(active_device_id).get(str(device_id)),
        include_sensitive=include_sensitive,
    )


def create_edge_device(cmd: CreateEdgeDeviceCommand) -> EdgeDeviceResponse:
    _assert_yolo_enabled()
    # Keep the registry write and the follow-up readiness marker in one
    # mutation window.  Otherwise the scheduler can observe a newly-created
    # device as ready before Agent bootstrap or the legacy environment check
    # has published its queued state.
    with registry_io.device_mutation_lock() as lock_acquired:
        if not lock_acquired:
            raise _device_mutation_lock_unavailable("新增边端设备")
        try:
            record = registry_io.create_device_record(
                name=cmd.name,
                enabled=cmd.enabled,
                host=cmd.host,
                port=cmd.port,
                user=cmd.user,
                password=cmd.password,
                private_key_path=cmd.private_key_path,
                edge_root=cmd.edge_root,
                remote_base=cmd.remote_base,
                sophon_demo_root=cmd.sophon_demo_root,
                auto_after_export=cmd.auto_after_export,
                run_coco_eval=cmd.run_coco_eval,
                connect_timeout_sec=cmd.connect_timeout_sec,
                validation_timeout_sec=cmd.validation_timeout_sec,
                docker_container=cmd.docker_container,
                docker_use_sudo=cmd.docker_use_sudo,
                validation_channel=cmd.validation_channel,
                set_active=cmd.set_active,
            )
        except registry_io.DeviceMutationLockUnavailableError as exc:
            raise _device_mutation_lock_unavailable("新增边端设备") from exc
        except ValueError as exc:
            raise _bad_request(str(exc)) from exc
        device_id = str(record.get("id") or "")
        auto_setup_ok = True
        if cmd.auto_configure_agent:
            auto_setup_ok, auto_setup_code, auto_setup_message = _auto_configure_agent_for_page(
                record
            )
            if not auto_setup_ok:
                _persist_page_auto_config_failure(
                    device_id,
                    code=auto_setup_code,
                    message=auto_setup_message,
                )
                record = _merge_persisted_agent_projection(record, device_id)
        if auto_setup_ok and _auto_agent_bootstrap_enabled(record):
            # The Agent lifecycle is the source of truth whenever its control
            # plane is enabled.  Do not fall back to the legacy SSH checker
            # when queueing or preflight fails: that checker can turn a stale
            # ``environment_ready`` report back into a dispatchable device
            # while the Agent is actually unavailable.
            _start_auto_agent_bootstrap(device_id)
            # The queueing call persists the durable lifecycle marker before
            # returning.  Re-read it so the create response immediately
            # reflects QUEUED (or a synchronous FAILED preflight) instead of
            # the pre-bootstrap NOT_STARTED snapshot.
            record = _merge_persisted_agent_projection(record, device_id)
        elif (
            auto_setup_ok
            and not cmd.auto_configure_agent
            and not _uses_board_agent(record)
            and _has_complete_runtime_config(record)
        ):
            start_environment_check(device_id, provision=True)
        active_device_id = registry_io.get_active_device_id()
    return _device_to_view(record, active_device_id=active_device_id)


def update_edge_device(
    device_id: str, cmd: PatchEdgeDeviceCommand
) -> EdgeDeviceResponse:
    _assert_yolo_enabled()
    with registry_io.device_mutation_lock() as lock_acquired:
        if not lock_acquired:
            raise _device_mutation_lock_unavailable("修改边端设备")
        before_record = registry_io.find_device_record(device_id)
        _assert_no_active_job_runtime_change(device_id, before_record, cmd)
        try:
            record = registry_io.update_device_record(
                device_id,
                name=cmd.name,
                enabled=cmd.enabled,
                host=cmd.host,
                port=cmd.port,
                user=cmd.user,
                password_secret_action=cmd.password_secret_action,
                password=cmd.password,
                private_key_path_secret_action=(
                    "clear"
                    if cmd.clear_private_key_path is True
                    else (
                        "set"
                        if str(cmd.private_key_path or "").strip()
                        and cmd.private_key_path_secret_action == "keep"
                        else cmd.private_key_path_secret_action
                    )
                ),
                private_key_path=cmd.private_key_path,
                edge_root=cmd.edge_root,
                remote_base=cmd.remote_base,
                sophon_demo_root=cmd.sophon_demo_root,
                auto_after_export=cmd.auto_after_export,
                run_coco_eval=cmd.run_coco_eval,
                connect_timeout_sec=cmd.connect_timeout_sec,
                validation_timeout_sec=cmd.validation_timeout_sec,
                docker_container=cmd.docker_container,
                docker_use_sudo=cmd.docker_use_sudo,
                validation_channel=cmd.validation_channel,
            )
        except ValueError as exc:
            message = str(exc)
            if "不存在" in message:
                raise _not_found(device_id) from exc
            raise _bad_request(message) from exc
        resolved_device_id = str(record.get("id") or device_id)
        auto_setup_ok = True
        if cmd.auto_configure_agent:
            auto_setup_ok, auto_setup_code, auto_setup_message = _auto_configure_agent_for_page(
                record
            )
            if not auto_setup_ok:
                _persist_page_auto_config_failure(
                    resolved_device_id,
                    code=auto_setup_code,
                    message=auto_setup_message,
                )
                record = _merge_persisted_agent_projection(record, resolved_device_id)
        # ``auto_configure_agent`` provisions the control plane, but it should
        # not by itself reinstall an already READY Agent when an operator only
        # edits a name or dispatch policy.  Runtime changes and non-READY
        # lifecycle states are covered by the reconciliation predicate.
        should_reconcile = _should_reconcile_after_update(
            before_record,
            record,
            password_secret_action=cmd.password_secret_action,
            private_key_path_secret_action=(
                "clear"
                if cmd.clear_private_key_path is True
                else (
                    "set"
                    if str(cmd.private_key_path or "").strip()
                    and cmd.private_key_path_secret_action == "keep"
                    else cmd.private_key_path_secret_action
                )
            ),
        )
        if should_reconcile and auto_setup_ok:
            if _auto_agent_bootstrap_enabled(record):
                # Keep the automatic Agent gate fail-closed.  A failed or
                # deferred enrollment must remain visible as an Agent state;
                # the compatibility SSH environment worker is not allowed to
                # overwrite it or make the device schedulable.
                _start_auto_agent_bootstrap(resolved_device_id)
                # Surface the persisted Agent stage in the same PATCH
                # response; otherwise the UI briefly renders the stale
                # pre-update lifecycle marker until its next poll.
                record = _merge_persisted_agent_projection(record, resolved_device_id)
            elif not _uses_board_agent(record) and _has_complete_runtime_config(record):
                start_environment_check(resolved_device_id, provision=True)
        active_device_id = registry_io.get_active_device_id()
    return _device_to_view(record, active_device_id=active_device_id)


def delete_edge_device(device_id: str) -> EdgeDeviceListResponse:
    _assert_yolo_enabled()
    with registry_io.device_mutation_lock() as lock_acquired:
        if not lock_acquired:
            raise _device_mutation_lock_unavailable("删除设备")
        if registry_io.find_device_record(device_id) is None:
            raise _not_found(device_id)
        _assert_no_active_job_operation(device_id, operation="删除设备")
        try:
            registry_io.delete_device_record(device_id)
        except ValueError as exc:
            message = str(exc)
            if "不存在" in message:
                raise _not_found(device_id) from exc
            raise _bad_request(message) from exc
    return _list_response()


def set_active_edge_device(cmd: SetActiveEdgeDeviceCommand) -> EdgeDeviceResponse:
    _assert_yolo_enabled()
    target_device_id = str(cmd.device_id or "").strip()
    with registry_io.device_mutation_lock() as lock_acquired:
        if not lock_acquired:
            raise _device_mutation_lock_unavailable("切换当前边端设备")
        if registry_io.find_device_record(target_device_id) is None:
            raise _not_found(target_device_id)
        # Switching the active affinity can redirect legacy jobs which do not
        # carry an explicit device id.  Protect both sides of the switch: the
        # current device may own such a legacy job, while the target may have
        # an explicit job already running.
        current_device_id = str(registry_io.get_active_device_id() or "").strip()
        for guarded_device_id in dict.fromkeys(
            value for value in (current_device_id, target_device_id) if value
        ):
            _assert_no_active_job_operation(
                guarded_device_id,
                operation="切换当前边端设备",
            )
        try:
            registry_io.set_active_device_id(target_device_id)
        except ValueError as exc:
            raise _not_found(target_device_id) from exc
        record = registry_io.get_active_device_record()
        if record is None:
            raise _not_found(target_device_id)
    return _device_to_view(record, active_device_id=target_device_id)


def probe_edge_device(device_id: str) -> EdgeSshProbeResponse:
    _assert_yolo_enabled()
    record = registry_io.find_device_record(device_id)
    if record is None:
        raise _not_found(device_id)
    # A successful probe immediately queues the environment provisioner; refuse
    # the whole operation while a validation job is using the device so the
    # probe cannot race a runtime install or cleanup.
    _assert_no_active_job_operation(device_id, operation="探测边端设备")
    if _uses_board_agent(record):
        status_value = "ok" if _board_agent_heartbeat_is_fresh(record) else "fail"
        message = str(record.get("board_agent_message") or "板端心跳尚未上报")
        return EdgeSshProbeResponse(
            status=status_value,
            stage="board_agent_heartbeat",
            code=(
                "board_agent_online" if status_value == "ok" else "board_agent_offline"
            ),
            message=message,
            checked_at=record.get("board_agent_last_heartbeat_at"),
        )
    probe = probe_edge_ssh_target(registry_io._record_to_runtime_config(record))
    try:
        registry_io.update_device_record(
            device_id,
            probe_status=probe.get("status"),
            probe_stage=probe.get("stage"),
            probe_code=probe.get("code"),
            probe_message=probe.get("message"),
            probe_checked_at=probe.get("checked_at"),
        )
    except registry_io.DeviceMutationLockUnavailableError as exc:
        raise _device_mutation_lock_unavailable("保存边端探测结果") from exc
    if probe.get("status") == "ok":
        try:
            start_environment_check(device_id, provision=True)
        except registry_io.DeviceMutationLockUnavailableError as exc:
            # ``start_environment_check`` is also called by background scans
            # and older test adapters.  Keep this boundary fail-closed even
            # when a caller bypasses the service-level mapping below.
            raise _device_mutation_lock_unavailable("启动环境检查") from exc
    return EdgeSshProbeResponse(**probe)


def _probe_snapshot(record: dict[str, Any] | None) -> tuple[str, str, str, str]:
    """Return the probe fields used to prevent stale background overwrites."""
    raw = record if isinstance(record, dict) else {}
    return (
        str(raw.get("probe_status") or ""),
        str(raw.get("probe_stage") or ""),
        str(raw.get("probe_code") or ""),
        str(raw.get("probe_checked_at") or ""),
    )


def _environment_report_summary(report: dict | None) -> dict:
    raw = report if isinstance(report, dict) else {}
    keys = (
        "format",
        "architecture",
        "os",
        "os_version",
        "container",
        "sophon_demo_root",
        "architecture_ok",
        "bm1688_device_ok",
        "libsophon_ok",
        "docker_ok",
        "docker_image_ok",
        "container_ok",
        "python_runtime_ok",
        "sophon_demo_ok",
        "validator_ok",
        "noninteractive_root_ok",
        "free_mb",
        "free_inodes",
        "tmp_free_mb",
        "tmp_free_inodes",
        "docker_root",
        "runtime_path",
        "runtime_mount",
        "runtime_source",
        "tmp_mount",
        "tmp_source",
        "docker_mount",
        "docker_source",
        "data_mount",
        "data_source",
        "docker_free_mb",
        "docker_free_inodes",
        "data_free_mb",
        "data_free_inodes",
        "minimum_free_mb",
        "minimum_tmp_free_mb",
        "minimum_docker_free_mb",
        "minimum_data_free_mb",
        "minimum_free_inodes",
        "environment_ready",
        "fatal_reasons",
        "repairable_reasons",
        "provisionable",
        "storage_cleanup",
        "manual_intervention_required",
    )
    summary = {key: raw[key] for key in keys if key in raw}
    # ``agent_bootstrap`` is a compatibility mirror written by the Agent
    # lifecycle service.  Preserve it when a legacy environment check updates
    # the report, otherwise a synchronous bootstrap failure would disappear
    # immediately after the fallback check starts.
    if isinstance(raw.get("agent_bootstrap"), dict):
        summary["agent_bootstrap"] = dict(raw["agent_bootstrap"])
    return summary


def _environment_exception_state(exc: Exception) -> tuple[str, str, str]:
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    error_code = str(getattr(exc, "code", "") or "")
    if error_code == "manual_cleanup_required":
        return (
            "blocked",
            "manual_cleanup_required",
            "边端安全日志已自动清理并完成复检，但空间或 inode 仍不足，需要人工检查；该设备保持禁止调度。"
            f" 原始错误: {message[:500]}",
        )
    if (
        "no space left on device" in lowered
        or "磁盘空间不足" in message
        or "space_insufficient" in lowered
    ):
        return (
            "blocked",
            "disk_space_insufficient",
            "边端磁盘空间不足，无法完成 BM1688 环境安装或执行；请清理 /tmp、Docker 数据目录和运行时目录后重新体检。"
            f" 原始错误: {message[:500]}",
        )
    return "failed", "environment_check_failed", message[:800]


def _update_device_if_present(device_id: str, **changes) -> bool:
    try:
        registry_io.update_device_record(device_id, **changes)
    except ValueError:
        if registry_io.find_device_record(device_id) is None:
            return False
        raise
    return True


def _persist_environment_state(
    device_id: str,
    *,
    status_value: str,
    stage: str = "",
    code: str = "",
    message: str = "",
    report: dict | None = None,
    checked_at: str | None = None,
) -> bool:
    report_payload = dict(report) if isinstance(report, dict) else {}
    if not isinstance(report_payload.get("agent_bootstrap"), dict):
        # Environment checks often pass a fresh runtime report (or ``{}`` on
        # an SSH failure).  Carry forward the lifecycle mirror so the
        # fallback check cannot erase a visible Agent FAILED state.
        try:
            current_record = registry_io.find_device_record(device_id)
        except Exception:
            current_record = None
        current_report = (
            current_record.get("environment_report")
            if isinstance(current_record, dict)
            else None
        )
        current_agent_state = (
            current_report.get("agent_bootstrap")
            if isinstance(current_report, dict)
            else None
        )
        if isinstance(current_agent_state, dict):
            report_payload["agent_bootstrap"] = dict(current_agent_state)
    return _update_device_if_present(
        device_id,
        environment_status=status_value,
        environment_stage=stage,
        environment_code=code,
        environment_message=message,
        environment_report=_environment_report_summary(report_payload),
        environment_checked_at=checked_at or _utc_now_iso(),
    )


def _run_environment_check_sync(device_id: str, *, provision: bool = True) -> None:
    lock = _environment_lock(device_id)
    if not lock.acquire(blocking=False):
        return
    client = None
    last_report: dict = {}
    try:
        record = registry_io.find_device_record(device_id)
        if record is None:
            return
        if _auto_agent_bootstrap_enabled(record):
            # Automatic Agent enrollment owns readiness for this SSH device.
            # A queued legacy check could otherwise publish a successful
            # environment report after Agent preflight/heartbeat failed.
            logger.info(
                "skip legacy edge environment check while automatic Agent gate is enabled device_id={}",
                device_id,
            )
            return
        active_job_id = _active_processing_job_id(device_id)
        if active_job_id is not None:
            busy_code = (
                "device_busy_check_failed" if active_job_id == -1 else "device_busy"
            )
            busy_message = (
                "无法确认边端任务状态，环境检查已停止"
                if active_job_id == -1
                else f"边端设备正在执行任务 #{active_job_id}，环境检查已停止"
            )
            _persist_environment_state(
                device_id,
                status_value="blocked",
                stage="device_busy",
                code=busy_code,
                message=busy_message,
                report=record.get("environment_report"),
            )
            return
        probe_snapshot = _probe_snapshot(record)
        cfg = registry_io._record_to_runtime_config(record)
        ssh_probe = probe_edge_ssh_target(cfg)
        # A manual probe may finish while this background check is in SSH.
        # Do not let the older background result replace the manual result;
        # environment status below remains independently auditable.
        current_record = registry_io.find_device_record(device_id)
        if _probe_snapshot(current_record) == probe_snapshot:
            if not _update_device_if_present(
                device_id,
                probe_status=ssh_probe.get("status"),
                probe_stage=ssh_probe.get("stage"),
                probe_code=ssh_probe.get("code"),
                probe_message=ssh_probe.get("message"),
                probe_checked_at=ssh_probe.get("checked_at"),
            ):
                return
        else:
            logger.info(
                "skip stale background edge probe result device_id={}", device_id
            )
        if ssh_probe.get("status") != "ok":
            _persist_environment_state(
                device_id,
                status_value="failed",
                stage="ssh_probe",
                code=str(ssh_probe.get("code") or "ssh_probe_failed"),
                message=str(ssh_probe.get("message") or "SSH 连接探测失败"),
                report={},
                checked_at=ssh_probe.get("checked_at") or _utc_now_iso(),
            )
            return
        if not _persist_environment_state(
            device_id,
            status_value="checking",
            stage="environment_probe",
            code="running",
            message="正在检查 BM1688 边端运行环境",
            report={},
            checked_at=_utc_now_iso(),
        ):
            return
        # Recheck after the SSH probe/state write.  A scheduler claim may have
        # happened while the probe was in flight; never connect for a runtime
        # mutation once that claim is visible.
        active_job_id = _active_processing_job_id(device_id)
        if active_job_id is not None:
            busy_code = (
                "device_busy_check_failed" if active_job_id == -1 else "device_busy"
            )
            busy_message = (
                "无法确认边端任务状态，环境检查已停止"
                if active_job_id == -1
                else f"边端设备正在执行任务 #{active_job_id}，环境检查已停止"
            )
            _persist_environment_state(
                device_id,
                status_value="blocked",
                stage="device_busy",
                code=busy_code,
                message=busy_message,
                report=record.get("environment_report"),
            )
            return
        client = ssh_connect(
            host=str(cfg.get("host") or ""),
            port=int(cfg.get("port") or 22),
            username=str(cfg.get("user") or ""),
            password=str(cfg.get("password") or ""),
            private_key_path=str(cfg.get("private_key_path") or "") or None,
            timeout=int(cfg.get("connect_timeout_sec") or 30),
        )
        before = edge_runtime_provision.probe_edge_runtime(
            client,
            configured_demo_root=str(cfg.get("sophon_demo_root") or ""),
            docker_container=str(cfg.get("docker_container") or "bm1688"),
        )
        last_report = before
        if before.get("environment_ready"):
            _persist_environment_state(
                device_id,
                status_value="ready",
                stage="environment_verify",
                code="ok",
                message="BM1688 边端验证环境已就绪",
                report=before,
            )
            return
        fatal = list(before.get("fatal_reasons") or [])
        storage_blocked = edge_runtime_provision.has_storage_pressure(before)
        if (fatal and not storage_blocked) or not provision:
            disk_blocked = any(
                "space_insufficient" in str(reason)
                or "inodes_insufficient" in str(reason)
                for reason in fatal
            )
            _persist_environment_state(
                device_id,
                status_value="blocked" if fatal else "needs_install",
                stage="environment_probe",
                code=(
                    "disk_space_insufficient"
                    if disk_blocked
                    else (
                        "hardware_prerequisite_missing"
                        if fatal
                        else "runtime_components_missing"
                    )
                ),
                message=(
                    "边端磁盘空间或 inode 不足，请清理 /tmp、Docker 数据目录和运行时目录后重新体检: "
                    + ", ".join(fatal)
                    if disk_blocked
                    else (
                        "存在不可自动修复的硬件或系统前置条件: " + ", ".join(fatal)
                        if fatal
                        else "边端运行时组件缺失，等待预安装"
                    )
                ),
                report=before,
            )
            return

        if not _persist_environment_state(
            device_id,
            status_value="cleaning" if storage_blocked else "installing",
            stage="storage_cleanup" if storage_blocked else "environment_provision",
            code="cleaning_logs" if storage_blocked else "provisioning",
            message=(
                "空间不足，正在按告警文件系统清理过期轮转日志"
                if storage_blocked
                else "正在预安装 BM1688 边端运行时"
            ),
            report=before,
        ):
            return

        def reconnect():
            return ssh_connect(
                host=str(cfg.get("host") or ""),
                port=int(cfg.get("port") or 22),
                username=str(cfg.get("user") or ""),
                password=str(cfg.get("password") or ""),
                private_key_path=str(cfg.get("private_key_path") or "") or None,
                timeout=int(cfg.get("connect_timeout_sec") or 30),
            )

        # Runtime provisioning can delete caches and replace binaries.  Perform
        # one final claim check immediately before entering that critical
        # section; a job that starts during the read-only probe wins.
        active_job_id = _active_processing_job_id(device_id)
        if active_job_id is not None:
            busy_code = (
                "device_busy_check_failed" if active_job_id == -1 else "device_busy"
            )
            busy_message = (
                "无法确认边端任务状态，环境检查已停止"
                if active_job_id == -1
                else f"边端设备正在执行任务 #{active_job_id}，环境检查已停止"
            )
            _persist_environment_state(
                device_id,
                status_value="blocked",
                stage="device_busy",
                code=busy_code,
                message=busy_message,
                report=last_report,
            )
            return

        client, result = edge_runtime_provision.ensure_edge_runtime(
            client,
            configured_demo_root=str(cfg.get("sophon_demo_root") or ""),
            docker_container=str(cfg.get("docker_container") or "bm1688"),
            reconnect=reconnect,
        )
        after = result.get("after") if isinstance(result, dict) else {}
        last_report = after or result
        ready = bool(isinstance(after, dict) and after.get("environment_ready"))
        cleanup_done = bool(
            isinstance(result, dict) and isinstance(result.get("storage_cleanup"), dict)
        )
        provisioned = bool(isinstance(result, dict) and result.get("provisioned"))
        _persist_environment_state(
            device_id,
            status_value="ready" if ready else "failed",
            stage="environment_verify",
            code="ok" if ready else "verification_failed",
            message=(
                (
                    "过期轮转日志自动清理后，BM1688 环境复检通过"
                    if cleanup_done and not provisioned
                    else "BM1688 边端运行时预安装并复检通过"
                )
                if ready
                else "环境修复后复检仍未通过"
            ),
            report=last_report,
        )
    except registry_io.DeviceMutationLockUnavailableError as exc:
        # A registry mutation lock is a transient coordination result.  Do
        # not let it escape a daemon thread (which would leave an unhandled
        # traceback and no retryable state transition); the next system scan
        # or explicit request can retry after the competing writer releases
        # the lock.
        logger.info(
            "defer edge device environment check while mutation lock is busy device_id={} error={}",
            device_id,
            str(exc)[:200],
        )
        return
    except Exception as exc:
        if registry_io.find_device_record(device_id) is None:
            return
        error_report = getattr(exc, "report", None)
        if isinstance(error_report, dict) and error_report:
            last_report = error_report
        failure_status, failure_code, failure_message = _environment_exception_state(
            exc
        )
        if failure_status == "blocked":
            logger.warning(
                "edge device environment check blocked device_id={} code={} message={}",
                device_id,
                failure_code,
                failure_message,
            )
        else:
            logger.exception(
                "edge device environment check failed device_id={}", device_id
            )
        try:
            _persist_environment_state(
                device_id,
                status_value=failure_status,
                stage=(
                    "manual_intervention"
                    if failure_code == "manual_cleanup_required"
                    else "environment_provision"
                ),
                code=failure_code,
                message=failure_message,
                report=last_report,
            )
        except registry_io.DeviceMutationLockUnavailableError as persist_exc:
            # The original failure is already recorded in the log.  A second
            # lock failure while publishing it must not kill the worker thread
            # or replace a transient contention with a misleading 500.
            logger.info(
                "defer edge device environment failure state while mutation lock is busy device_id={} error={}",
                device_id,
                str(persist_exc)[:200],
            )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        lock.release()


def start_environment_check(
    device_id: str, *, provision: bool = True
) -> EdgeDeviceResponse:
    _assert_yolo_enabled()
    record = registry_io.find_device_record(device_id)
    if record is None:
        raise _not_found(device_id)
    # Automatic reconciliation may run after a harmless metadata update while
    # a task is already processing.  Skip that work and leave the task's
    # runtime untouched; the next scheduler/system scan can retry it.
    active_job_id = _active_processing_job_id(device_id)
    if active_job_id is not None:
        logger.info(
            "skip edge environment check for busy device_id={} job_id={}",
            device_id,
            active_job_id,
        )
        return _device_to_view(
            record,
            active_device_id=registry_io.get_active_device_id(),
            busy_job_id=(None if active_job_id == -1 else active_job_id),
        )
    if _uses_board_agent(record):
        # A resident Board Agent reports its own environment and heartbeat;
        # the SSH environment worker must never overwrite that projection.
        return _device_to_view(
            record,
            active_device_id=registry_io.get_active_device_id(),
            busy_job_id=None,
        )
    if _auto_agent_bootstrap_enabled(record):
        # The automatic Agent lifecycle is the only readiness authority for a
        # fully configured SSH device while the control plane is enabled.
        # Return the current projection without starting the legacy worker.
        logger.info(
            "skip legacy edge environment check because automatic Agent gate is enabled device_id={}",
            device_id,
        )
        return _device_to_view(
            record,
            active_device_id=registry_io.get_active_device_id(),
            busy_job_id=None,
        )
    try:
        _persist_environment_state(
            device_id,
            status_value="checking",
            stage="environment_probe",
            code="queued",
            message="环境检查已排队",
            report=record.get("environment_report"),
            checked_at=_utc_now_iso(),
        )
    except registry_io.DeviceMutationLockUnavailableError as exc:
        raise _device_mutation_lock_unavailable("排队环境检查") from exc
    thread = threading.Thread(
        target=_run_environment_check_sync,
        args=(str(device_id),),
        kwargs={"provision": provision},
        name=f"edge-environment-check-{device_id}",
        daemon=True,
    )
    thread.start()
    active_id = registry_io.get_active_device_id()
    current = registry_io.find_device_record(device_id) or record
    return _device_to_view(current, active_device_id=active_id)


def start_all_environment_checks(
    *, provision: bool = True, only_pending: bool = False
) -> EdgeDeviceListResponse:
    _assert_yolo_enabled()
    scan_and_start_configured_environment_checks(
        provision=provision,
        only_pending=only_pending,
    )
    return _list_response()


def recover_persisted_agent_bootstraps() -> int:
    """Resume durable Agent runs before the legacy environment scan.

    Automatic enrollment is gated by the same control-plane switches as the
    create/update path.  When it is disabled, leave the persisted marker alone
    so an operator can re-enable the feature and resume it later; importantly,
    do not let a legacy SSH check overwrite that lifecycle state.
    """

    if not _agent_bootstrap_control_plane_enabled():
        return 0
    resumed = 0
    try:
        resumed = edge_agent_bootstrap.resume_persisted_bootstraps()
    except Exception:
        logger.exception("persisted edge agent bootstrap recovery scan failed")
    # A process can stop after a device has been written but before the
    # background queue marker is durable (or while another worker owns the
    # registry mutation lock).  Such a record has no ACTIVE marker for the
    # legacy recovery routine to find.  Reconcile only complete SSH records in
    # the explicit retryable states; terminal validation failures stay visible
    # for operator action.
    try:
        records = registry_io.list_device_records()
    except Exception:
        logger.exception("list edge devices for Agent onboarding recovery failed")
        return resumed
    for record in records:
        if not isinstance(record, dict) or not _auto_agent_bootstrap_enabled(record):
            continue
        if not _auto_agent_bootstrap_retryable(record):
            continue
        device_id = str(record.get("id") or "").strip()
        if not device_id:
            continue
        try:
            active_job_id = _active_processing_job_id(device_id)
        except Exception:
            logger.exception(
                "read active edge job for Agent onboarding recovery failed device_id={}",
                device_id,
            )
            continue
        if active_job_id is not None:
            logger.info(
                "skip automatic Agent onboarding recovery for busy device_id={} job_id={}",
                device_id,
                active_job_id,
            )
            continue
        if _start_auto_agent_bootstrap(device_id):
            resumed += 1
    return resumed


def scan_and_start_configured_environment_checks(
    *, provision: bool = True, only_pending: bool = False
) -> int:
    """Queue checks for configured devices without requiring a browser session."""
    if not settings.YOLO_TRAIN_ENABLED:
        return 0
    queued = 0
    for record in registry_io.list_device_records():
        if not isinstance(record, dict):
            continue
        # Once the pull channel is selected, SSH environment checks are no
        # longer the source of truth.  Heartbeats and the Agent bootstrap
        # lifecycle own readiness for both READY and offline devices.
        if _uses_board_agent(record):
            continue
        # An automatically enrolled SSH device must finish (or explicitly
        # fail) the Agent lifecycle before the compatibility environment worker
        # is allowed to mutate its status.  Recovery runs immediately before
        # this scan at process startup.
        if _agent_bootstrap_in_progress(record) or (
            _auto_agent_bootstrap_enabled(record)
            and _agent_bootstrap_status(record) != "READY"
        ):
            logger.info(
                "skip legacy edge environment check while Agent enrollment is pending device_id={} status={}",
                record.get("id"),
                _agent_bootstrap_status(record) or "NOT_STARTED",
            )
            continue
        environment_status = (
            str(record.get("environment_status") or "unknown")
            if isinstance(record, dict)
            else "unknown"
        )
        if _has_complete_runtime_config(record) and (
            not only_pending
            or environment_status in {"unknown", "needs_install", "failed"}
        ):
            active_job_id = _active_processing_job_id(str(record.get("id") or ""))
            if active_job_id is not None:
                logger.info(
                    "skip queued edge environment check for busy device_id={} job_id={}",
                    record.get("id"),
                    active_job_id,
                )
                continue
            try:
                start_environment_check(
                    str(record.get("id") or ""), provision=provision
                )
                queued += 1
            except Exception:
                logger.exception(
                    "queue edge environment check failed device_id={}", record.get("id")
                )
    return queued


def schedule_system_environment_checks() -> None:
    """Run one system-level scan at service startup, independent of the UI."""
    global _SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED
    # Agent recovery is its own control-plane concern.  It must still run when
    # the legacy YOLO environment-check worker is disabled, while a completely
    # disabled control plane should leave the startup marker untouched so a
    # later configuration change can schedule recovery in the same process.
    if not settings.YOLO_TRAIN_ENABLED and not _agent_bootstrap_control_plane_enabled():
        return
    with _SYSTEM_ENVIRONMENT_BOOTSTRAP_LOCK:
        if _SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED:
            return
        _SYSTEM_ENVIRONMENT_BOOTSTRAP_STARTED = True
    disable_background_watchdogs = (
        os.environ.get("LABELU_DISABLE_BACKGROUND_WATCHDOGS", "")
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )

    def _bootstrap() -> None:
        try:
            resumed = recover_persisted_agent_bootstraps()
            queued = 0
            if settings.YOLO_TRAIN_ENABLED and not disable_background_watchdogs:
                queued = scan_and_start_configured_environment_checks(
                    provision=True,
                    only_pending=False,
                )
            logger.info(
                "edge environment system bootstrap recovered {} Agent run(s), queued {} configured device(s)",
                resumed,
                queued,
            )
        except Exception:
            logger.exception("edge environment system bootstrap failed")

    threading.Thread(
        target=_bootstrap,
        name="edge-environment-system-bootstrap",
        daemon=True,
    ).start()
