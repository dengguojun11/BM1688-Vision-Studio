"""算能盒板端 Agent API：内网设备主动拉取 BM1688 导出并回传验证结果。"""

from __future__ import annotations

import json
import base64
import binascii
import hashlib
import hmac
import ipaddress
import math
import re
import secrets
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_yolo_bm1688_export,
    crud_yolo_board_validation,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.response.board_agent import (
    BoardAgentJobResponse,
    BoardValidationProgressResponse,
    BoardValidationStatusResponse,
)
from labelu.internal.application.command.yolo_edge_validation import (
    THRESHOLD_RATIO_UNIT,
)
from labelu.internal.application.service.yolo_bm1688_export import (
    _export_dir_for_row,
    _list_bmodel_names,
)
from labelu.internal.common.config import settings
from labelu.internal.common import edge_agent_control_plane
from labelu.internal.common import edge_device_registry
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.execution_fencing import (
    ExecutionFencingLost,
    ExecutionLeaseToken,
    claim_execution_lease,
    conditional_execution_update,
    release_execution_lease,
    renew_execution_lease,
)
from labelu.internal.common.edge_validation_evidence import (
    evaluate_edge_validation_evidence,
)
from labelu.internal.common.bm1688_compile_contract import profile_snapshot_digest
from labelu.internal.common.model_manifest_signature import sign_model_manifest_ed25519
from labelu.internal.common.fixed_eval_signature import fixed_eval_content_signature
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.project_scope import (
    ProjectScopeError,
    assert_project_chain,
    task_project_id,
)
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob

UNIFIED_BOARD_AGENT_PROTOCOL_VERSION = "unified-v1"

BOARD_THRESHOLD_CONTRACT_VERSION = "edge_validation_thresholds.v1"


def _json_object(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _artifact_profile_expectation(export_row: Any) -> Dict[str, Any]:
    """从导出清单生成边端证据门禁期望值。"""
    manifest = _json_object(getattr(export_row, "artifact_manifest_json", None))
    profile = manifest.get("bm1688_profile")
    if not isinstance(profile, dict):
        profile = manifest.get("profile_snapshot")
    expected = {}
    if isinstance(profile, dict):
        digest = profile_snapshot_digest(profile)
        if digest:
            expected["profile_digest"] = digest
    execution_signature = str(manifest.get("execution_signature") or "").strip()
    if execution_signature:
        expected["execution_generation"] = execution_signature
        expected["require_execution_generation_state"] = True
    compiler_block = manifest.get("compiler")
    compiler_config = (
        compiler_block.get("config")
        if isinstance(compiler_block, dict)
        else manifest.get("compiler_config")
    )
    if isinstance(compiler_config, dict) and isinstance(compiler_config.get("quality_rule"), dict):
        expected["quality_rule"] = compiler_config["quality_rule"]
    return expected


def _threshold_bucket(raw: Dict[str, Any]) -> Dict[str, Any]:
    nested_contract = raw.get("threshold_contract")
    if isinstance(nested_contract, dict) and nested_contract:
        raw = nested_contract
    for key in ("effective", "recommended", "current"):
        value = raw.get(key)
        if isinstance(value, dict) and value:
            return dict(value)
    return dict(raw)


def _threshold_candidate(
    label: str, raw: Any
) -> tuple[str, Dict[str, Any], Dict[str, Any]] | None:
    """Return a usable threshold bucket and its outer metadata."""
    obj = _json_object(raw)
    if not obj:
        return None
    bucket = _threshold_bucket(obj)
    if not any(key in bucket for key in ("conf_thres", "nms_thres", "match_iou_thres")):
        return None
    return label, obj, bucket


def _threshold_payload_with_metadata(raw: Any) -> Dict[str, Any]:
    obj = _json_object(raw)
    if not obj:
        return {}
    bucket = _threshold_bucket(obj)
    return {**obj, **bucket}


def build_board_threshold_contract(
    export_row: Any,
    fallback: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the validation-only threshold payload exposed to board agents."""
    raw = _json_object(getattr(export_row, "validation_thresholds_json", None))
    binding = _json_object(getattr(export_row, "threshold_binding_json", None))
    candidates = [
        _threshold_candidate("fallback", fallback),
        _threshold_candidate("validation", raw),
        _threshold_candidate(
            "binding.effective_validation_thresholds",
            binding.get("effective_validation_thresholds"),
        ),
        _threshold_candidate("binding.validation", binding.get("validation")),
        _threshold_candidate(
            "binding.validation_thresholds", binding.get("validation_thresholds")
        ),
    ]
    selected_meta: Dict[str, Any] = {}
    selected: Dict[str, Any] = {}
    selected_label = "system_default"
    for candidate in candidates:
        if candidate is not None:
            selected_label, selected_meta, selected = candidate
            break

    unit_values = [
        selected.get("threshold_unit"),
        selected.get("unit"),
        selected_meta.get("threshold_unit"),
        selected_meta.get("unit"),
        raw.get("threshold_unit"),
        raw.get("unit"),
        binding.get("threshold_unit"),
        binding.get("unit"),
    ]
    explicit_units = [
        str(value).strip() for value in unit_values if value not in (None, "")
    ]
    invalid_unit = next(
        (value for value in explicit_units if value != THRESHOLD_RATIO_UNIT),
        None,
    )

    defaults = {
        "conf_thres": float(
            getattr(settings, "YOLO_SOP_EVAL_CONF_THRES", 0.35) or 0.35
        ),
        "nms_thres": 0.7,
        "match_iou_thres": float(
            getattr(settings, "YOLO_SOP_EVAL_IOU_THRES", 0.5) or 0.5
        ),
    }
    # The API contract accepts ratio values across the full 0..1 domain. The
    # narrower ranges are recommendations for the UI/AI advisor, not a reason
    # to silently replace a caller's explicit threshold before board execution.
    ratio_ranges = {key: (0.0, 1.0) for key in defaults}
    recommended_ranges = {
        "conf_thres": (0.15, 0.60),
        "nms_thres": (0.30, 0.80),
        "match_iou_thres": (0.40, 0.70),
    }
    effective: Dict[str, float] = {}
    defaulted_fields: List[str] = []
    invalid_fields: List[str] = []
    policy_warning_fields: List[str] = []
    for key, default in defaults.items():
        raw_value = selected.get(key)
        if invalid_unit:
            value = default
            defaulted_fields.append(key)
            invalid_fields.append(key)
        else:
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                value = default
                defaulted_fields.append(key)
            minimum, maximum = ratio_ranges[key]
            if not math.isfinite(value) or value < minimum or value > maximum:
                value = default
                if key not in defaulted_fields:
                    defaulted_fields.append(key)
                invalid_fields.append(key)
            else:
                recommended_min, recommended_max = recommended_ranges[key]
                if value < recommended_min or value > recommended_max:
                    policy_warning_fields.append(key)
        effective[key] = value

    source = (
        str(
            selected.get("source")
            or selected_meta.get("source")
            or raw.get("source")
            or binding.get("source")
            or selected_label
        ).strip()
        or "system_default"
    )
    contract: Dict[str, Any] = {
        "schema_version": BOARD_THRESHOLD_CONTRACT_VERSION,
        "threshold_unit": THRESHOLD_RATIO_UNIT,
        "source": source,
        "effective": dict(effective),
        **effective,
        "defaulted_fields": sorted(set(defaulted_fields)),
        "invalid_fields": sorted(set(invalid_fields)),
        "policy_warning_fields": sorted(set(policy_warning_fields)),
    }
    if invalid_unit:
        contract["invalid_unit"] = invalid_unit
    requested = selected.get("requested")
    if isinstance(requested, dict):
        contract["requested"] = dict(requested)
    return contract


def _read_fixed_eval_manifest(board_eval: Path) -> Dict[str, Any]:
    manifest_path = board_eval / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _fixed_eval_manifest_valid(board_eval: Path) -> bool:
    """Validate the sealed image/label snapshot before reusing it."""
    manifest = _read_fixed_eval_manifest(board_eval)
    if str(manifest.get("schema_version") or "") != "fixed_eval.v1":
        return False
    declared = str(manifest.get("manifest_sha256") or "").strip()
    if not declared or not isinstance(manifest.get("files"), list):
        return False
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    actual = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if actual != declared:
        return False
    try:
        image_count = int(manifest.get("image_count") or 0)
        labeled_count = int(manifest.get("labeled_image_count") or 0)
    except (TypeError, ValueError):
        return False
    if image_count <= 0 or labeled_count <= 0 or labeled_count > image_count:
        return False
    image_paths = {
        str(item.get("path") or "")
        for item in manifest["files"]
        if isinstance(item, dict)
        and str(item.get("path") or "").startswith("images/")
    }
    if len(image_paths) != image_count:
        return False
    for item in manifest["files"]:
        if not isinstance(item, dict):
            return False
        relative = str(item.get("path") or "").strip()
        if (
            not relative
            or relative.startswith(("/", "\\"))
            or "\\" in relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            return False
        path = board_eval / relative
        if not path.is_file():
            return False
        try:
            if int(item.get("size")) != path.stat().st_size:
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != str(
                item.get("sha256") or ""
            ):
                return False
        except (OSError, TypeError, ValueError):
            return False
    declared_content_signature = str(
        manifest.get("fixed_eval_signature") or ""
    ).strip()
    if declared_content_signature and declared_content_signature != fixed_eval_content_signature(manifest):
        return False
    return True


def _board_eval_pack_ready(board_eval: Path) -> bool:
    """Return whether the immutable board-evaluation snapshot is present."""
    return (
        (board_eval / "images").is_dir()
        and _fixed_eval_manifest_valid(board_eval)
    )


def _write_run_config_if_changed(path: Path, payload: Dict[str, Any]) -> bool:
    """Write the agent config only when its serialized content changed."""
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        if path.read_text(encoding="utf-8") == serialized:
            return False
    except (OSError, UnicodeError):
        pass
    path.write_text(serialized, encoding="utf-8")
    return True


def _public_base_url_error(message: str) -> LabelUException:
    """Build a stable configuration error for a board-agent callback URL."""
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        message=message,
    )


def _normalize_public_base_url(value: Any) -> str:
    """Validate a URL that the remote board must be able to call back to.

    ``HOST``/``PORT`` are intentionally not used as a fallback here. They
    commonly describe a bind address (``localhost`` or ``0.0.0.0``), not an
    address reachable from a separate edge device.
    """
    raw = str(value or "").replace("\r", "").replace("\n", "").strip().rstrip("/")
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise _public_base_url_error("边端回连地址的主机或端口无效") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise _public_base_url_error(
            "未配置有效的边端回连地址；请设置 BOARD_AGENT_PUBLIC_BASE_URL，"
            "或设置可被边端访问的 MEDIA_HOST（必须是 http/https 地址）"
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise _public_base_url_error("边端回连地址不得包含账号、密码、查询参数或片段")
    if any(ord(ch) < 32 for ch in raw):
        raise _public_base_url_error("边端回连地址包含控制字符")
    try:
        hostname = str(parsed.hostname or "").strip().rstrip(".").lower()
        # Accessing ``port`` validates malformed bracketed/numbered hosts and
        # keeps the API response deterministic instead of leaking ValueError.
        _ = parsed.port
    except ValueError as exc:
        raise _public_base_url_error("边端回连地址的主机或端口无效") from exc
    if not hostname:
        raise _public_base_url_error("边端回连地址必须包含可访问主机名或 IP")

    blocked = hostname in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "::"}
    if not blocked:
        try:
            address = ipaddress.ip_address(hostname.split("%", 1)[0])
            blocked = bool(address.is_loopback or address.is_unspecified)
            if not blocked and getattr(address, "ipv4_mapped", None) is not None:
                mapped = address.ipv4_mapped
                blocked = bool(mapped and (mapped.is_loopback or mapped.is_unspecified))
        except ValueError:
            # DNS names are valid here; actual DNS/routing reachability belongs
            # to the Agent preflight and heartbeat checks.
            pass
    if blocked:
        raise _public_base_url_error(
            "边端回连地址不能使用 localhost、127.0.0.1、0.0.0.0 或 ::1；"
            "请配置边端可访问的 BOARD_AGENT_PUBLIC_BASE_URL 或 MEDIA_HOST"
        )
    return raw


def _public_base_url() -> str:
    base = edge_agent_control_plane.public_base_url()
    if not base:
        raise _public_base_url_error(
            "未配置边端可访问的回连地址；请设置 BOARD_AGENT_PUBLIC_BASE_URL，"
            "或设置可被边端访问的 MEDIA_HOST"
        )
    return _normalize_public_base_url(base)


def assert_board_agent_enabled() -> None:
    if not edge_agent_control_plane.board_agent_enabled():
        raise LabelUException(
            code=ErrorCode.CODE_57022_BOARD_AGENT_DISABLED,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


def verify_board_agent_token(token: str) -> None:
    assert_board_agent_enabled()
    expected = edge_agent_control_plane.board_agent_token()
    if not expected:
        raise LabelUException(
            code=ErrorCode.CODE_57022_BOARD_AGENT_DISABLED,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="未配置 BOARD_AGENT_TOKEN",
        )
    supplied = (token or "").strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise LabelUException(
            code=ErrorCode.CODE_57023_BOARD_AGENT_UNAUTHORIZED,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


def unified_board_agent_enabled() -> bool:
    """Whether the switchable, unified edge-validation path is active."""
    return bool(
        edge_agent_control_plane.board_agent_enabled()
        and edge_agent_control_plane.primary_enabled()
    )


def _registered_agent_dispatch_ready(agent_id: str) -> bool | None:
    """Check the durable device projection before claiming an old lease.

    ``None`` is retained for rolling upgrades and old synthetic jobs whose
    device id predates the registry.  Once a device is registered, however,
    the registry is authoritative: a disabled/non-board device, failed Agent
    bootstrap, stale heartbeat, or unready runtime must not consume a
    ``PROCESSING`` lease left by an earlier scheduler pass.
    """

    try:
        record = edge_device_registry.find_device_record(agent_id)
    except Exception:
        logger.exception("读取板端设备注册表失败 agent_id={}", agent_id)
        return False
    if record is None:
        return None
    if not bool(record.get("enabled", True)):
        return False
    if str(record.get("validation_channel") or "ssh").strip().lower() != "board_agent":
        return False
    # Import lazily to avoid making the board-agent service part of the
    # registry module's import graph during application startup.
    try:
        from labelu.internal.application.service import (
            edge_device_registry as edge_device_registry_service,
        )

        if not edge_device_registry_service.agent_bootstrap_dispatch_ready(record):
            return False
        if not edge_device_registry_service._board_agent_heartbeat_is_fresh(record):
            return False
    except Exception:
        logger.exception("校验板端设备就绪状态失败 agent_id={}", agent_id)
        return False
    return bool(record.get("board_agent_environment_ready"))


def _task_token_secret() -> str:
    return edge_agent_control_plane.task_token_secret()


def _task_token_error(message: str = "板端验证任务令牌无效") -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_57023_BOARD_AGENT_UNAUTHORIZED,
        status_code=status.HTTP_401_UNAUTHORIZED,
        message=message,
    )


def _encode_unified_task_token(
    *, job_id: int, agent_id: str, fencing_token: int, expires_at: datetime
) -> str:
    """Create a short-lived, stateless token bound to one execution lease."""
    secret = _task_token_secret()
    if not secret:
        raise LabelUException(
            code=ErrorCode.CODE_57022_BOARD_AGENT_DISABLED,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="未配置板端验证任务令牌密钥",
        )
    payload = {
        "job_id": int(job_id),
        "agent_id": str(agent_id).strip(),
        "fencing_token": int(fencing_token),
        "expires_at": int(expires_at.timestamp()),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{body}.{signature}"


def _decode_unified_task_token(token: str) -> Dict[str, Any]:
    secret = _task_token_secret()
    supplied = str(token or "").strip()
    if not secret or not supplied or "." not in supplied:
        raise _task_token_error()
    body, signature = supplied.rsplit(".", 1)
    expected = hmac.new(
        secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise _task_token_error()
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError, binascii.Error):
        raise _task_token_error() from None
    if not isinstance(payload, dict):
        raise _task_token_error()
    try:
        payload = {
            "job_id": int(payload["job_id"]),
            "agent_id": str(payload["agent_id"]).strip(),
            "fencing_token": int(payload["fencing_token"]),
            "expires_at": int(payload["expires_at"]),
        }
    except (KeyError, TypeError, ValueError):
        raise _task_token_error() from None
    if not payload["agent_id"] or payload["expires_at"] <= int(time.time()):
        raise _task_token_error("板端验证任务令牌已过期")
    return payload


def _unified_execution_token(row) -> ExecutionLeaseToken:
    owner = str(getattr(row, "execution_lease_owner", None) or "").strip()
    expires_at = getattr(row, "execution_lease_expires_at", None)
    fencing_token = int(getattr(row, "execution_fencing_token", 0) or 0)
    if not owner or expires_at is None or fencing_token <= 0:
        raise _task_token_error("板端验证任务尚未取得有效执行租约")
    return ExecutionLeaseToken(
        model=YoloEdgeValidationJob,
        record_id=int(row.id),
        owner=owner,
        fencing_token=fencing_token,
        lease_expires_at=expires_at,
    )


def _unified_report_payload(row) -> Dict[str, Any]:
    try:
        result = json.loads(str(getattr(row, "result_json", None) or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        result = {}
    return result if isinstance(result, dict) else {}


def _unified_board_report_hash(
    *, status_value: str, report: Optional[Dict[str, Any]], error_message: Optional[str]
) -> str:
    raw = json.dumps(
        {
            "status": str(status_value or "").upper(),
            "report": report if isinstance(report, dict) else None,
            "error_message": str(error_message or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _unified_token_matches_row(
    row,
    *,
    agent_id: str,
    task_token: str,
    allow_terminal_duplicate: bool = False,
    report_hash: str | None = None,
) -> Dict[str, Any]:
    payload = _decode_unified_task_token(task_token)
    normalized_agent = str(agent_id or "").strip()
    if payload["job_id"] != int(row.id) or payload["agent_id"] != normalized_agent:
        raise _task_token_error("板端验证任务与设备身份不匹配")
    if str(getattr(row, "edge_device_id", None) or "").strip() != normalized_agent:
        raise _task_token_error("板端设备身份与任务分配不匹配")
    result = _unified_report_payload(row)
    metadata = result.get("board_agent") if isinstance(result, dict) else None
    if (
        allow_terminal_duplicate
        and str(getattr(row, "status", "") or "").upper()
        in {"COMPLETED", "PARTIAL_COMPLETED", "FAILED"}
        and isinstance(metadata, dict)
        and metadata.get("task_token_sha256")
        == hashlib.sha256(task_token.encode("utf-8")).hexdigest()
        and (not report_hash or metadata.get("report_sha256") == report_hash)
    ):
        return payload
    if str(getattr(row, "status", "") or "").upper() != "PROCESSING":
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="板端验证任务当前不接受设备回报",
        )
    expected_owner = f"board-agent:{normalized_agent}"
    if (
        str(getattr(row, "execution_lease_owner", None) or "") != expected_owner
        or int(getattr(row, "execution_fencing_token", 0) or 0)
        != payload["fencing_token"]
    ):
        raise _task_token_error("板端验证任务已被其他执行器接管")
    expires_at = getattr(row, "execution_lease_expires_at", None)
    if expires_at is None or expires_at <= datetime.now():
        raise _task_token_error("板端验证任务执行租约已过期")
    return payload


def _unified_status_response(
    db: Session,
    row,
    *,
    include_report: bool = False,
    task_token: str | None = None,
    agent_id: str | None = None,
) -> BoardValidationStatusResponse:
    result = _unified_report_payload(row)
    report = result.get("board_agent_report")
    if not isinstance(report, dict):
        report = result.get("report") if isinstance(result.get("report"), dict) else None
    progress = None
    try:
        progress_payload = json.loads(str(getattr(row, "progress_json", None) or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        progress_payload = {}
    if isinstance(progress_payload, dict) and progress_payload:
        history = progress_payload.get("history")
        progress = BoardValidationProgressResponse(
            stage=str(progress_payload.get("stage") or ""),
            message=str(progress_payload.get("message") or ""),
            detail=(
                progress_payload.get("detail")
                if isinstance(progress_payload.get("detail"), dict)
                else None
            ),
            updated_at=str(progress_payload.get("updated_at") or "") or None,
            history=history[-25:] if isinstance(history, list) else [],
        )
    export_row = crud_yolo_bm1688_export.get(db, int(row.export_id))
    status_value = str(getattr(row, "status", "") or "")
    edge_status = "running" if status_value in {"PENDING", "PROCESSING"} else "unknown"
    if status_value == "FAILED":
        edge_status = "fail"
    elif status_value in {"COMPLETED", "PARTIAL_COMPLETED"}:
        edge_status = edge_result_status_from_report(
            {"result": {"board_agent_report": report or {}}}
        )
    return BoardValidationStatusResponse(
        validation_id=int(row.id),
        export_id=int(row.export_id),
        status=status_value,
        error_message=getattr(row, "error_message", None),
        workflow_stage="edge_validation",
        edge_result_status=edge_status,
        threshold_contract=build_board_threshold_contract(export_row),
        report_summary=getattr(row, "report_summary", None),
        execution_device_host=(
            str(getattr(row, "edge_host", None) or "").strip() or None
        ),
        completed_at=getattr(row, "completed_at", None),
        progress=progress,
        report=report if include_report else None,
        agent_id=agent_id,
        task_token=task_token,
    )


def claim_next_unified_job(
    db: Session, *, agent_id: str
) -> Optional[BoardAgentJobResponse]:
    """Claim one scheduler-assigned unified job for the identified board."""
    if not unified_board_agent_enabled():
        return None
    normalized_agent = str(agent_id or "").strip()
    if not normalized_agent:
        raise _task_token_error("缺少板端设备身份")
    # Validate before taking a lease. A malformed or loopback callback URL
    # must not consume a queued job that the Agent cannot download/report.
    base = _public_base_url()
    owner = f"board-agent:{normalized_agent}"
    ttl = max(60, int(getattr(settings, "BOARD_AGENT_TASK_TOKEN_TTL_SECONDS", 900)))
    # Only keep scalar candidate identities from the read transaction.  The
    # registry mutation lock is shared with heartbeat/CRUD/scheduler paths;
    # holding an open database read transaction while acquiring it reverses
    # the normal registry -> database lock order and can retain a stale ORM
    # row during a concurrent scheduler claim.
    candidates = (
        db.query(
            YoloEdgeValidationJob.id,
            YoloEdgeValidationJob.export_id,
        )
        .filter(
            YoloEdgeValidationJob.status == "PROCESSING",
            YoloEdgeValidationJob.edge_device_id == normalized_agent,
        )
        .order_by(
            YoloEdgeValidationJob.assigned_at.asc(),
            YoloEdgeValidationJob.id.asc(),
        )
        .limit(20)
        .all()
    )
    db.rollback()
    for candidate_id, candidate_export_id in candidates:
        # Heartbeats and device CRUD persist through the same registry
        # document.  Serialize the readiness read with the lease claim so an
        # offline/failed device cannot win a race against a late heartbeat or
        # a management update.  ``None`` keeps compatibility with old jobs
        # created before the registry was introduced; known records are strict.
        with edge_device_registry.device_mutation_lock() as lock_acquired:
            if not lock_acquired:
                continue
            dispatch_ready = _registered_agent_dispatch_ready(normalized_agent)
            if dispatch_ready is False:
                continue
            # Re-read the candidate after the lock is acquired.  The initial
            # list is only a hint; another worker may have completed or
            # reassigned it while this Agent was between polls.
            row = (
                db.query(YoloEdgeValidationJob)
                .filter(
                    YoloEdgeValidationJob.id == int(candidate_id),
                    YoloEdgeValidationJob.status == "PROCESSING",
                    YoloEdgeValidationJob.edge_device_id == normalized_agent,
                )
                .first()
            )
            if row is None:
                continue
            validation_id = int(row.id)
            export_id = int(row.export_id or candidate_export_id)
            task_id = int(row.task_id)
            row_train_job_id = int(getattr(row, "yolo_train_job_id", 0) or 0)
            row_status = str(row.status)
            token = claim_execution_lease(
                db,
                YoloEdgeValidationJob,
                validation_id,
                owner=owner,
                lease_seconds=ttl,
            )
        if token is None:
            continue
        export_row = crud_yolo_bm1688_export.get(db, export_id)
        if export_row is None or str(export_row.status or "") != "COMPLETED":
            release_execution_lease(db, token)
            continue
        task_token = _encode_unified_task_token(
            job_id=validation_id,
            agent_id=normalized_agent,
            fencing_token=token.fencing_token,
            expires_at=token.lease_expires_at,
        )
        progress = {
            "stage": "board_agent_claimed",
            "message": "板端主动拉取已领取统一边端验证任务",
            "detail": {"agent_id": normalized_agent},
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "history": [],
        }
        conditional_execution_update(
            db,
            token,
            {
                "progress_json": json.dumps(progress, ensure_ascii=False),
                "report_summary": "板端主动拉取已领取任务",
                "updated_at": datetime.now(),
            },
        )
        train_job_id = int(
            getattr(export_row, "yolo_train_job_id", 0)
            or row_train_job_id
            or 0
        )
        job = crud_yolo_train_job.get(db, train_job_id) if train_job_id else None
        imgsz = int(job.imgsz or 640) if job else int(export_row.imgsz or 640)
        api = settings.API_V1_STR.rstrip("/")
        return BoardAgentJobResponse(
            validation_id=validation_id,
            export_id=export_id,
            task_id=task_id,
            yolo_train_job_id=train_job_id,
            status=row_status,
            imgsz=imgsz,
            bmodel_files=_list_bmodel_names(export_id),
            models_zip_url=f"{base}{api}/board-agent/jobs/{validation_id}/models.zip",
            report_url=f"{base}{api}/board-agent/jobs/{validation_id}/report",
            progress_url=f"{base}{api}/board-agent/jobs/{validation_id}/progress",
            threshold_contract=build_board_threshold_contract(export_row),
            agent_id=normalized_agent,
            task_token=task_token,
            protocol_version=UNIFIED_BOARD_AGENT_PROTOCOL_VERSION,
        )
    return None


def record_board_agent_heartbeat(
    *,
    agent_id: str,
    environment_ready: bool,
    capabilities: Optional[Dict[str, Any]] = None,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """Record liveness for a configured board-agent device.

    This uses the existing device registry rather than the retired project/OTA
    Agent control plane.  A heartbeat from an unknown or SSH device is rejected
    so an arbitrary token holder cannot make a different device schedulable.
    """
    normalized_agent = str(agent_id or "").strip()
    if not normalized_agent:
        raise _task_token_error("缺少板端设备身份")
    record = edge_device_registry.find_device_record(normalized_agent)
    if record is None:
        raise LabelUException(
            code=ErrorCode.CODE_57024_BOARD_VALIDATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="板端设备未登记",
        )
    try:
        updated = edge_device_registry.record_board_agent_heartbeat(
            normalized_agent,
            environment_ready=bool(environment_ready),
            capabilities=capabilities or {},
            message=message,
        )
    except edge_device_registry.DeviceMutationLockUnavailableError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="设备状态正在更新，请稍后重试心跳",
        ) from exc
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message=str(exc),
        ) from exc
    if updated is None:
        raise LabelUException(
            code=ErrorCode.CODE_57024_BOARD_VALIDATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="板端设备未登记",
        )
    return {
        "agent_id": normalized_agent,
        "status": "ONLINE",
        "environment_ready": bool(updated.get("board_agent_environment_ready")),
        "last_heartbeat_at": updated.get("board_agent_last_heartbeat_at"),
        "capabilities": updated.get("board_agent_capabilities") or {},
        "message": updated.get("board_agent_message") or "",
    }


def get_unified_validation_for_agent(
    db: Session,
    *,
    validation_id: int,
    agent_id: str,
    task_token: str,
    allow_terminal_duplicate: bool = False,
    report_hash: str | None = None,
):
    row = db.query(YoloEdgeValidationJob).filter(YoloEdgeValidationJob.id == int(validation_id)).first()
    if row is None:
        raise LabelUException(
            code=ErrorCode.CODE_57024_BOARD_VALIDATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _unified_token_matches_row(
        row,
        agent_id=agent_id,
        task_token=task_token,
        allow_terminal_duplicate=allow_terminal_duplicate,
        report_hash=report_hash,
    )
    return row


def build_unified_models_zip_path(
    db: Session, *, validation_id: int, agent_id: str, task_token: str
) -> Path:
    row = get_unified_validation_for_agent(
        db, validation_id=validation_id, agent_id=agent_id, task_token=task_token
    )
    return build_models_zip_path(int(row.export_id), db=db)


def submit_unified_progress(
    db: Session,
    *,
    validation_id: int,
    agent_id: str,
    task_token: str,
    stage: str,
    message: str,
    detail: Optional[Dict[str, Any]] = None,
) -> BoardValidationStatusResponse:
    row = get_unified_validation_for_agent(
        db, validation_id=validation_id, agent_id=agent_id, task_token=task_token
    )
    execution_token = _unified_execution_token(row)
    renewed = renew_execution_lease(
        db,
        execution_token,
        lease_seconds=max(
            60, int(getattr(settings, "BOARD_AGENT_TASK_TOKEN_TTL_SECONDS", 900))
        ),
    )
    if renewed is None:
        raise _task_token_error("板端验证任务执行租约已被接管")
    try:
        current = json.loads(str(getattr(row, "progress_json", None) or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        current = {}
    history = current.get("history") if isinstance(current, dict) and isinstance(current.get("history"), list) else []
    entry = {"stage": str(stage or "")[:64], "message": str(message or "")[:500], "detail": detail or {}, "at": datetime.now().isoformat(timespec="seconds")}
    history = (history + [entry])[-25:]
    progress = {"stage": entry["stage"], "message": entry["message"], "detail": entry["detail"], "updated_at": datetime.now().isoformat(timespec="seconds"), "history": history}
    conditional_execution_update(
        db,
        renewed,
        {"progress_json": json.dumps(progress, ensure_ascii=False), "updated_at": datetime.now(), "lease_expires_at": renewed.lease_expires_at},
    )
    fresh = crud_yolo_edge_validation_job.get(db, int(validation_id))
    refreshed_token = _encode_unified_task_token(
        job_id=int(validation_id), agent_id=agent_id, fencing_token=renewed.fencing_token, expires_at=renewed.lease_expires_at
    )
    return _unified_status_response(db, fresh, task_token=refreshed_token, agent_id=agent_id)


def submit_unified_report(
    db: Session,
    *,
    validation_id: int,
    agent_id: str,
    task_token: str,
    status_value: str,
    report: Optional[Dict[str, Any]],
    error_message: Optional[str],
) -> BoardValidationStatusResponse:
    st = str(status_value or "").strip().upper()
    if st not in {"COMPLETED", "FAILED"}:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="status 须为 COMPLETED 或 FAILED",
        )
    report_hash = _unified_board_report_hash(
        status_value=st, report=report, error_message=error_message
    )
    row = get_unified_validation_for_agent(
        db,
        validation_id=validation_id,
        agent_id=agent_id,
        task_token=task_token,
        allow_terminal_duplicate=True,
        report_hash=report_hash,
    )
    if str(getattr(row, "status", "") or "").upper() in {"COMPLETED", "PARTIAL_COMPLETED", "FAILED"}:
        return _unified_status_response(db, row, include_report=True, agent_id=agent_id)
    execution_token = _unified_execution_token(row)
    report_payload = dict(report) if isinstance(report, dict) else None
    export_row = crud_yolo_bm1688_export.get(db, int(row.export_id))
    evidence_gate = None
    if st == "COMPLETED":
        evidence_gate = evaluate_edge_validation_evidence(
            report_payload,
            expected=_artifact_profile_expectation(export_row),
        )
        st = "COMPLETED" if evidence_gate["publishable"] else "PARTIAL_COMPLETED"
        if not evidence_gate["publishable"] and not error_message:
            error_message = "边端证据未齐全：" + ",".join(evidence_gate.get("reason_codes") or ["evidence_pending"])
    if export_row is not None and report_payload is not None:
        report_payload.pop("threshold_contract_check", None)
        report_payload = {
            "threshold_contract_check": compare_board_threshold_contract(build_board_threshold_contract(export_row), report_payload),
            **report_payload,
        }
    if evidence_gate is not None:
        if report_payload is not None:
            report_payload.pop("evidence_gate", None)
        report_payload = {"evidence_gate": evidence_gate, **(report_payload or {})}
    result = _unified_report_payload(row)
    result["board_agent_report"] = report_payload or {}
    result["board_agent"] = {
        "agent_id": str(agent_id),
        "fencing_token": int(execution_token.fencing_token),
        "task_token_sha256": hashlib.sha256(task_token.encode("utf-8")).hexdigest(),
        "report_sha256": report_hash,
        "reported_status": st,
    }
    updates = {
        "status": st,
        "error_message": (error_message or "")[:8000] or None,
        "completed_at": datetime.now(),
        "result_json": json.dumps(result, ensure_ascii=False)[:60000],
        "report_summary": "板端主动拉取已回传验证结果" if st != "FAILED" else "板端主动拉取验证失败",
        "updated_at": datetime.now(),
    }
    if not conditional_execution_update(db, execution_token, updates):
        raise _task_token_error("板端验证任务已被其他执行器接管")
    try:
        release_execution_lease(db, execution_token)
    except Exception:
        pass
    fresh = crud_yolo_edge_validation_job.get(db, int(validation_id))
    return _unified_status_response(db, fresh, include_report=True, agent_id=agent_id)


def purge_board_validation_artifacts(db: Session, *, export_id: int) -> None:
    """删除该导出下全部板端验证 DB 记录与磁盘打包产物（重新验证前调用）。"""
    from labelu.internal.application.service.board_eval_pack import (
        clear_board_eval_pack_for_export,
    )

    clear_board_eval_pack_for_export(export_id)
    crud_yolo_board_validation.delete_all_for_export(db, export_id=export_id)


def _assert_board_lineage(
    db: Session, *, export_id: int, task_id: int, yolo_train_job_id: int
) -> None:
    """Validate the explicit export -> task/train-job parent chain."""

    export = (
        db.query(YoloBm1688Export).filter(YoloBm1688Export.id == int(export_id)).first()
    )
    task = db.query(Task).filter(Task.id == int(task_id)).first()
    train_job = (
        db.query(YoloTrainJob).filter(YoloTrainJob.id == int(yolo_train_job_id)).first()
    )
    if export is None or task is None or train_job is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="板端验证父链记录不存在",
        )
    # The schema marks these foreign keys as non-null, but legacy SQLite data
    # may still contain malformed rows. Treat a missing parent as a lineage
    # conflict instead of allowing ``int(None)`` to become an HTTP 500.
    if (
        export.task_id is None
        or export.yolo_train_job_id is None
        or train_job.task_id is None
        or int(export.task_id) != int(task.id)
        or int(export.yolo_train_job_id) != int(train_job.id)
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="板端验证与导出/训练作业的父链不一致",
        )
    if int(train_job.task_id) != int(task.id):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="训练作业与任务的父链不一致",
        )
    try:
        assert_project_chain(
            [task_project_id(db, task.id), getattr(train_job, "project_id", None)],
            context="board validation lineage",
        )
    except ProjectScopeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="板端验证父链项目归属不一致",
        ) from exc


def enqueue_board_validation_for_export(
    db: Session,
    *,
    export_id: int,
    task_id: int,
    yolo_train_job_id: int,
    export_status: str,
) -> Optional[int]:
    _assert_board_lineage(
        db,
        export_id=export_id,
        task_id=task_id,
        yolo_train_job_id=yolo_train_job_id,
    )
    if not edge_agent_control_plane.board_agent_enabled():
        return None
    if export_status != "COMPLETED":
        return None
    if crud_yolo_board_validation.has_active_for_export(db, export_id=export_id):
        return None
    row = crud_yolo_board_validation.create_for_export(
        db,
        export_id=export_id,
        task_id=task_id,
        yolo_train_job_id=yolo_train_job_id,
    )
    return row.id


def request_board_validation_for_export(
    db: Session,
    *,
    export_id: int,
    task_id: int,
    yolo_train_job_id: int,
    export_status: str,
) -> int:
    """
    用户手动发起/重新板端验证：进行中则拒绝；否则清空旧记录与打包文件后新建任务。
    """
    assert_board_agent_enabled()
    _assert_board_lineage(
        db,
        export_id=export_id,
        task_id=task_id,
        yolo_train_job_id=yolo_train_job_id,
    )
    if export_status != "COMPLETED":
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="导出未完成，无法创建板端验证任务",
        )
    if crud_yolo_board_validation.has_active_for_export(db, export_id=export_id):
        raise LabelUException(
            code=ErrorCode.CODE_57025_BOARD_VALIDATION_BUSY,
            status_code=status.HTTP_409_CONFLICT,
        )
    latest = crud_yolo_board_validation.get_latest_for_export(db, export_id=export_id)
    if latest is not None:
        purge_board_validation_artifacts(db, export_id=export_id)
    else:
        from labelu.internal.application.service.board_eval_pack import (
            clear_board_eval_pack_for_export,
        )

        clear_board_eval_pack_for_export(export_id)
    row = crud_yolo_board_validation.create_for_export(
        db,
        export_id=export_id,
        task_id=task_id,
        yolo_train_job_id=yolo_train_job_id,
    )
    return row.id


def build_models_zip_path(export_id: int, db: Session | None = None) -> Path:
    from labelu.internal.application.service.board_eval_pack import (
        pack_board_eval_images_for_export,
    )
    from labelu.internal.adapter.persistence import crud_task
    from labelu.internal.common.db import session_scope

    root = _export_dir_for_row(export_id)
    models_root = root / "models"
    board_eval = root / "board_eval"
    if not models_root.is_dir():
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message="导出目录缺少 models/",
        )
    if db is not None:
        export_row = crud_yolo_bm1688_export.get(db, export_id)
        if export_row and not _board_eval_pack_ready(board_eval):
            task = crud_task.get(db=db, task_id=export_row.task_id)
            pack_board_eval_images_for_export(
                db,
                export_id=export_id,
                task_id=export_row.task_id,
                yolo_train_job_id=export_row.yolo_train_job_id,
                imgsz=int(export_row.imgsz or 640),
                task_config_json=task.config if task else None,
            )
    else:
        with session_scope() as scoped:
            export_row = crud_yolo_bm1688_export.get(scoped, export_id)
            if export_row and not _board_eval_pack_ready(board_eval):
                task = crud_task.get(db=scoped, task_id=export_row.task_id)
                pack_board_eval_images_for_export(
                    scoped,
                    export_id=export_id,
                    task_id=export_row.task_id,
                    yolo_train_job_id=export_row.yolo_train_job_id,
                    imgsz=int(export_row.imgsz or 640),
                    task_config_json=task.config if task else None,
            )
    board_eval_manifest = _read_fixed_eval_manifest(board_eval)
    if _fixed_eval_manifest_valid(board_eval):
        root_manifest_path = root / "manifest.json"
        serialized_manifest = json.dumps(
            board_eval_manifest, ensure_ascii=False, indent=2
        )
        try:
            current_manifest = root_manifest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            current_manifest = ""
        if current_manifest != serialized_manifest:
            root_manifest_path.write_text(serialized_manifest, encoding="utf-8")
    else:
        board_eval_manifest = {}
    threshold_contract = build_board_threshold_contract(export_row)
    profile_expectation = _artifact_profile_expectation(export_row)
    artifact_manifest = _json_object(
        getattr(export_row, "artifact_manifest_json", None)
    )
    artifact_signature = str(
        artifact_manifest.get("execution_signature")
        or getattr(export_row, "execution_signature", None)
        or ""
    ).strip() or None
    profile = artifact_manifest.get("bm1688_profile")
    if not isinstance(profile, dict):
        profile = artifact_manifest.get("profile_snapshot")
    profile_digest = (
        profile_snapshot_digest(profile) if isinstance(profile, dict) else None
    )
    run_config_path = root / "run_config.json"
    run_config = {
        "schema_version": BOARD_THRESHOLD_CONTRACT_VERSION,
        "export_id": int(export_id),
        "model_identity": {
            # 发布前只能绑定本地导出 ID；发布后由 published_model_id/AICM
            # ID 回填，不能把本地导出 ID 冒充 AICM ID。
            "model_id": str(
                getattr(export_row, "published_model_id", None) or export_id
            ),
            "export_id": int(export_id),
            "aicm_model_id": getattr(export_row, "published_model_id", None),
            "model_id_source": (
                "aicm_model_id"
                if getattr(export_row, "published_model_id", None)
                else "export_id_pre_publish"
            ),
        },
        "artifact_execution_signature": artifact_signature,
        "bm1688_profile": profile,
        "profile_digest": profile_digest,
        "edge_execution_contract": {
            "compile_location": "server",
            "device_action": "load_and_infer",
            "device_must_not_compile": True,
        },
        "required_bmodel_precisions": ["fp32", "fp16", "int8"],
        # 把服务器生成的不可变制品清单一并带入边端包，设备验签后再逐项
        # 校验 BModel 文件；清单中绝对路径只用于服务器审计，边端按 relative_path。
        "artifact_manifest": artifact_manifest or None,
        "edge_manifest_key_id": str(
            getattr(settings, "EDGE_MANIFEST_ED25519_KEY_ID", "edge-v1") or "edge-v1"
        ),
        "edge_signature_required": bool(
            getattr(settings, "EDGE_MANIFEST_ED25519_REQUIRE", False)
            or str(getattr(settings, "APP_ENV", "") or "").lower() == "production"
        ),
        "execution_generation": str(
            artifact_manifest.get("execution_signature")
            or artifact_manifest.get("manifest_id")
            or f"export:{int(export_id)}"
        ),
        # 设备侧不能按摘要字符串猜测新旧世代；签发时间作为当前清单
        # 的可比较顺序，随签名清单一起下发并持久化到设备状态。
        "execution_generation_scope": (
            "aicm:{}".format(getattr(export_row, "published_model_id", None))
            if getattr(export_row, "published_model_id", None)
            else f"export:{int(export_id)}"
        ),
        "execution_generation_issued_at": artifact_manifest.get("created_at"),
        "imgsz": int(export_row.imgsz or 640) if export_row else 640,
        "threshold_contract": threshold_contract,
        "threshold_unit": threshold_contract["threshold_unit"],
        "conf_thres": threshold_contract["conf_thres"],
        "nms_thres": threshold_contract["nms_thres"],
        "match_iou_thres": threshold_contract["match_iou_thres"],
        "eval_image_count": int(board_eval_manifest.get("image_count") or 0),
        "labeled_image_count": int(
            board_eval_manifest.get("labeled_image_count") or 0
        ),
        "fixed_eval_signature": str(
            board_eval_manifest.get("fixed_eval_signature")
            or board_eval_manifest.get("manifest_sha256")
            or ""
        ),
        "require_fixed_eval_binding": True,
        "fixed_eval_schema_version": "fixed_eval.v1",
        "quality_rule": profile_expectation.get("quality_rule"),
    }
    edge_private_key = str(
        getattr(settings, "EDGE_MANIFEST_ED25519_PRIVATE_KEY", "") or ""
    ).strip()
    if edge_private_key:
        run_config = sign_model_manifest_ed25519(
            run_config,
            private_key=edge_private_key,
            key_id=run_config["edge_manifest_key_id"],
        )
    elif run_config["edge_signature_required"]:
        run_config["edge_manifest_signature_status"] = "MISSING_KEY"
    _write_run_config_if_changed(run_config_path, run_config)

    zip_path = root / "board_models.zip"
    pack_roots = [models_root]
    if board_eval.is_dir():
        pack_roots.append(board_eval)
    src_mtime = max(
        (
            p.stat().st_mtime
            for sub in pack_roots
            for p in sub.rglob("*")
            if p.is_file()
        ),
        default=0,
    )
    root_manifest_path = root / "manifest.json"
    src_mtime = max(src_mtime, run_config_path.stat().st_mtime)
    if root_manifest_path.is_file():
        src_mtime = max(src_mtime, root_manifest_path.stat().st_mtime)
    if zip_path.is_file() and zip_path.stat().st_mtime >= src_mtime:
        return zip_path
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(run_config_path, run_config_path.relative_to(root).as_posix())
        if root_manifest_path.is_file():
            zf.write(root_manifest_path, root_manifest_path.relative_to(root).as_posix())
        for sub in pack_roots:
            for fp in sub.rglob("*"):
                if fp.is_file():
                    arc = fp.relative_to(root).as_posix()
                    zf.write(fp, arc)
    return zip_path


def claim_next_job(
    db: Session, *, agent_id: str | None = None
) -> Optional[BoardAgentJobResponse]:
    # Validate before marking a legacy job as PROCESSING for the same reason
    # as the unified path above: configuration errors must be retryable.
    base = _public_base_url()
    normalized_agent = str(agent_id or "").strip()
    if normalized_agent:
        # The legacy table has no device assignment, but a caller that
        # identifies itself as a registered Board Agent must still satisfy the
        # durable registry gate.  Keep the readiness read and legacy claim in
        # one lock so a management/heartbeat update cannot race the check.
        with edge_device_registry.device_mutation_lock() as lock_acquired:
            if not lock_acquired:
                return None
            dispatch_ready = _registered_agent_dispatch_ready(normalized_agent)
            if dispatch_ready is False:
                return None
            with begin_transaction(db):
                row = crud_yolo_board_validation.claim_next_pending(db)
    else:
        # Older manually installed agents did not send an identity.  Preserve
        # the historical compatibility path for those callers.
        with begin_transaction(db):
            row = crud_yolo_board_validation.claim_next_pending(db)
    if not row:
        return None
    export_row = crud_yolo_bm1688_export.get(db, row.export_id)
    if not export_row or export_row.status != "COMPLETED":
        with begin_transaction(db):
            crud_yolo_board_validation.update_row(
                db,
                row,
                status="FAILED",
                error_message="关联 BM1688 导出未完成或不存在",
                completed_at=datetime.now(),
            )
        return None
    job = crud_yolo_train_job.get(db, row.yolo_train_job_id)
    imgsz = int(job.imgsz or 640) if job else int(export_row.imgsz or 640)
    api = settings.API_V1_STR.rstrip("/")
    zip_url = f"{base}{api}/board-agent/jobs/{row.id}/models.zip"
    report_url = f"{base}{api}/board-agent/jobs/{row.id}/report"
    progress_url = f"{base}{api}/board-agent/jobs/{row.id}/progress"
    return BoardAgentJobResponse(
        validation_id=row.id,
        export_id=row.export_id,
        task_id=row.task_id,
        yolo_train_job_id=row.yolo_train_job_id,
        status=row.status,
        imgsz=imgsz,
        bmodel_files=_list_bmodel_names(row.export_id),
        models_zip_url=zip_url,
        report_url=report_url,
        progress_url=progress_url,
        threshold_contract=build_board_threshold_contract(export_row),
    )


def _report_threshold_payload(report: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the first explicit edge threshold payload from a board report."""
    if not isinstance(report, dict):
        return {}
    queue: List[Dict[str, Any]] = [report]
    seen: set[int] = set()
    while queue:
        node = queue.pop(0)
        if id(node) in seen:
            continue
        seen.add(id(node))
        for key in (
            "thresholds_applied",
            "threshold_contract",
            "thresholds",
        ):
            value = node.get(key)
            if isinstance(value, dict):
                bucket = _threshold_payload_with_metadata(value)
                if any(
                    name in bucket
                    for name in ("conf_thres", "nms_thres", "match_iou_thres")
                ):
                    return bucket
        inference = node.get("inference_applied")
        evaluation = node.get("evaluation_match_config")
        if isinstance(inference, dict) or isinstance(evaluation, dict):
            combined: Dict[str, Any] = {}
            if isinstance(inference, dict):
                combined.update(inference)
            if isinstance(evaluation, dict):
                combined.update(evaluation)
            if any(
                name in combined
                for name in ("conf_thres", "nms_thres", "match_iou_thres")
            ):
                return combined
        for key in ("result", "execution", "analysis", "ingest"):
            value = node.get(key)
            if isinstance(value, dict):
                queue.append(value)
    return {}


def compare_board_threshold_contract(
    expected: Dict[str, Any], report: Dict[str, Any]
) -> Dict[str, Any]:
    """Compare agent-reported thresholds with the server-side contract."""
    expected_payload = _threshold_payload_with_metadata(expected)
    actual_payload = _report_threshold_payload(report)
    expected_view = {
        "threshold_unit": expected_payload.get("threshold_unit"),
        "conf_thres": expected_payload.get("conf_thres"),
        "nms_thres": expected_payload.get("nms_thres"),
        "match_iou_thres": expected_payload.get("match_iou_thres"),
    }
    actual_view = {
        "threshold_unit": actual_payload.get("threshold_unit")
        or actual_payload.get("unit"),
        "conf_thres": actual_payload.get("conf_thres"),
        "nms_thres": actual_payload.get("nms_thres"),
        "match_iou_thres": actual_payload.get("match_iou_thres"),
    }
    if not actual_payload:
        return {
            "status": "missing",
            "expected": expected_view,
            "actual": None,
        }
    if not actual_view["threshold_unit"]:
        return {
            "status": "missing",
            "expected": expected_view,
            "actual": actual_view,
            "reason": "missing_threshold_unit",
        }
    if actual_view["threshold_unit"] != THRESHOLD_RATIO_UNIT:
        return {
            "status": "mismatch",
            "expected": expected_view,
            "actual": actual_view,
            "reason": "unsupported_threshold_unit",
        }
    for key in ("conf_thres", "nms_thres", "match_iou_thres"):
        try:
            actual_value = float(actual_view[key])
            expected_value = float(expected_view[key])
        except (TypeError, ValueError):
            return {
                "status": "missing",
                "expected": expected_view,
                "actual": actual_view,
                "reason": "missing_" + key,
            }
        if not math.isclose(actual_value, expected_value, rel_tol=0.0, abs_tol=1e-6):
            return {
                "status": "mismatch",
                "expected": expected_view,
                "actual": actual_view,
                "reason": key,
            }
    return {
        "status": "matched",
        "expected": expected_view,
        "actual": actual_view,
    }


def get_validation_for_agent(db: Session, validation_id: int):
    row = crud_yolo_board_validation.get(db, validation_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57024_BOARD_VALIDATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return row


def submit_report(
    db: Session,
    *,
    validation_id: int,
    status_value: str,
    report: Optional[Dict[str, Any]],
    error_message: Optional[str],
) -> BoardValidationStatusResponse:
    row = get_validation_for_agent(db, validation_id)
    st = (status_value or "").strip().upper()
    if st not in ("COMPLETED", "FAILED"):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="status 须为 COMPLETED 或 FAILED",
        )
    report_payload = dict(report) if isinstance(report, dict) else None
    evidence_gate = None
    if st == "COMPLETED":
        # A zero exit code is only execution evidence.  A board report is not
        # allowed to become COMPLETED until the immutable model/eval/device/
        # performance/fencing evidence is present.  This keeps old agents
        # readable while preventing their incomplete result from publishing.
        evidence_gate = evaluate_edge_validation_evidence(
            report_payload,
            expected=_artifact_profile_expectation(
                crud_yolo_bm1688_export.get(db, int(row.export_id))
            ),
        )
        st = "COMPLETED" if evidence_gate["publishable"] else "PARTIAL_COMPLETED"
        if not evidence_gate["publishable"] and not error_message:
            error_message = "边端证据未齐全：" + ",".join(
                evidence_gate.get("reason_codes") or ["evidence_pending"]
            )
    if report_payload is not None:
        export_row = crud_yolo_bm1688_export.get(db, row.export_id)
        if export_row is not None:
            contract_check = compare_board_threshold_contract(
                build_board_threshold_contract(export_row), report_payload
            )
            # Keep the integrity result at the beginning so the existing size
            # cap cannot discard it from an unusually large agent report.
            report_payload.pop("threshold_contract_check", None)
            report_payload = {
                "threshold_contract_check": contract_check,
                **report_payload,
            }
        if evidence_gate is not None:
            # Put the gate first so it survives the historical 500 KB report
            # cap even when an agent sends verbose stdout/stderr.
            report_payload.pop("evidence_gate", None)
            report_payload = {
                "evidence_gate": evidence_gate,
                **report_payload,
            }
    elif evidence_gate is not None:
        # Keep a machine-readable reason even when an agent omitted its report.
        report_payload = {"evidence_gate": evidence_gate}
    report_text = None
    summary = None
    if report_payload is not None:
        report_text = json.dumps(report_payload, ensure_ascii=False)[:500_000]
        summary = str(report_payload.get("summary") or report_payload.get("passed"))[
            :500
        ]
    with begin_transaction(db):
        crud_yolo_board_validation.update_row(
            db,
            row,
            status=st,
            report_json=report_text,
            error_message=(error_message or "")[:8000] or None,
            completed_at=datetime.now(),
        )
    fresh = crud_yolo_board_validation.get(db, validation_id)
    expected_contract = build_board_threshold_contract(
        crud_yolo_bm1688_export.get(db, row.export_id)
    )
    resp = _row_to_status_response(
        fresh, include_report=True, threshold_contract=expected_contract
    )
    if summary and not resp.report_summary:
        resp.report_summary = summary
    return resp


_SOPHON_DEMO_STAGE_PATTERNS = (
    ("decode_ms_avg", "decode"),
    ("preprocess_ms_avg", "preprocess"),
    ("infer_ms_avg", "inference"),
    ("postprocess_ms_avg", "postprocess"),
)

_BMCV_DETECTION_STEP_RE = re.compile(
    r"^bmcv_(?:fp32|fp16|int8)_detection$", re.IGNORECASE
)


def _parse_sophon_demo_stage_timing(text: str) -> Dict[str, Any]:
    """Parse the per-image stage averages printed by sophon-demo."""
    parsed: Dict[str, Any] = {}
    for metric_key, log_key in _SOPHON_DEMO_STAGE_PATTERNS:
        matches = re.findall(
            rf"\b{log_key}_time\s*\(\s*ms\s*\)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)",
            text or "",
            flags=re.IGNORECASE,
        )
        if matches:
            parsed[metric_key] = round(float(matches[-1]), 3)
    if all(key in parsed for key, _ in _SOPHON_DEMO_STAGE_PATTERNS):
        parsed["pipeline_ms_sum"] = round(
            sum(float(parsed[key]) for key, _ in _SOPHON_DEMO_STAGE_PATTERNS),
            3,
        )
    if parsed:
        parsed["timing_source"] = "sophon_demo_stage_summary"
        parsed["timing_scope"] = "per_image_stage_average"
    return parsed


def normalize_board_report_metrics(report: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill structured timing for historical board reports without rewriting them."""
    if not isinstance(report, dict):
        return report
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
        report["metrics"] = metrics
    infer_rows = metrics.get("infer_runs")
    if not isinstance(infer_rows, list):
        infer_rows = []
        metrics["infer_runs"] = infer_rows

    def find_or_create(step_name: str, bmodel: Any, returncode: Any) -> Dict[str, Any]:
        for row in infer_rows:
            if not isinstance(row, dict):
                continue
            if bmodel and row.get("bmodel") == bmodel:
                return row
            if step_name and row.get("tag") == step_name:
                return row
        row = {
            "tag": step_name or "infer",
            "bmodel": bmodel,
            "ok": returncode == 0,
        }
        infer_rows.append(row)
        return row

    candidates = list(report.get("steps") or [])
    bmcv_infer_runs = report.get("bmcv_infer_runs")
    if isinstance(bmcv_infer_runs, list):
        candidates.extend(bmcv_infer_runs)
    bmcv_infer = report.get("bmcv_infer")
    if isinstance(bmcv_infer, dict):
        candidates.append(bmcv_infer)
    for step in candidates:
        if not isinstance(step, dict):
            continue
        step_name = str(step.get("step") or "")
        if not (
            step_name.startswith("infer") or _BMCV_DETECTION_STEP_RE.match(step_name)
        ):
            continue
        timing = _parse_sophon_demo_stage_timing(
            f"{step.get('stdout') or ''}\n{step.get('stderr') or ''}"
        )
        existing_metrics = step.get("metrics")
        if isinstance(existing_metrics, dict):
            timing = {**timing, **existing_metrics}
        if not timing:
            continue
        target = find_or_create(step_name, step.get("bmodel"), step.get("returncode"))
        for key, value in timing.items():
            if target.get(key) is None:
                target[key] = value
    return report


def _parse_report_json(
    row,
) -> tuple[
    Optional[str], Optional[BoardValidationProgressResponse], Optional[Dict[str, Any]]
]:
    """返回 (report_summary, progress, final_report)。"""
    if not row.report_json:
        return None, None, None
    try:
        data = json.loads(row.report_json)
    except json.JSONDecodeError:
        return None, None, None
    if not isinstance(data, dict):
        return None, None, None
    if data.get("type") == "progress":
        hist = data.get("history") or []
        if not isinstance(hist, list):
            hist = []
        return (
            str(data.get("message") or "")[:500] or None,
            BoardValidationProgressResponse(
                stage=str(data.get("stage") or ""),
                message=str(data.get("message") or ""),
                detail=(
                    data.get("detail") if isinstance(data.get("detail"), dict) else None
                ),
                updated_at=data.get("updated_at"),
                history=hist[-20:],
            ),
            None,
        )
    summary = str(data.get("summary") or "")[:500] or None
    return summary, None, normalize_board_report_metrics(data)


def edge_result_status_from_report(report: Dict[str, Any]) -> str:
    """Derive quality-result state without treating job completion as a pass."""
    if not isinstance(report, dict) or not report:
        return "unknown"

    evidence_gate = report.get("evidence_gate")
    if isinstance(evidence_gate, dict):
        evidence_status = str(evidence_gate.get("status") or "").strip().lower()
        if evidence_status == "fail":
            return "fail"
        if evidence_status != "pass" or evidence_gate.get("publishable") is not True:
            return "result_pending"

    contract_check = report.get("threshold_contract_check")
    if isinstance(contract_check, dict):
        contract_status = str(contract_check.get("status") or "").strip().lower()
        if contract_status == "mismatch":
            return "fail"
        if contract_status == "missing":
            return "result_pending"

    result = report.get("result") if isinstance(report.get("result"), dict) else {}
    execution_nodes = [
        candidate
        for candidate in (
            report.get("execution"),
            result.get("execution_summary"),
            result.get("execution"),
        )
        if isinstance(candidate, dict)
    ]

    # A completed process is not a quality verdict.  Inspect the comparison
    # and analysis nodes first; this is the shape emitted by the manual edge
    # evaluator (for example result.analysis.status == "fail").
    quality_nodes: list[Dict[str, Any]] = []
    for parent in (report, result):
        if not isinstance(parent, dict):
            continue
        for key in (
            "quality_gate",
            "comparison",
            "analysis",
            "eval_summary",
            "evaluation",
        ):
            node = parent.get(key)
            if isinstance(node, dict):
                quality_nodes.append(node)
                nested_gate = node.get("quality_gate")
                if isinstance(nested_gate, dict):
                    quality_nodes.append(nested_gate)

    for candidate in quality_nodes:
        if candidate.get("passed") is False:
            return "result_pending" if candidate.get("skipped") else "fail"
        status_value = str(candidate.get("status") or "").strip().lower()
        if status_value in {"fail", "failed", "error"}:
            return "fail"

    for candidate in execution_nodes:
        status_value = str(candidate.get("status") or "").strip().lower()
        if status_value in {"fail", "failed", "error"}:
            return "fail"
        if candidate.get("returncode") not in (None, 0):
            return "fail"
        if candidate.get("board_passed") is False:
            return "fail"

    skipped = any(
        candidate.get("board_skipped") is True
        or str(candidate.get("status") or "").strip().lower() == "skipped"
        for candidate in execution_nodes
    )

    for candidate in quality_nodes:
        status_value = str(candidate.get("status") or "").strip().lower()
        if status_value in {"warn", "warning", "review"}:
            return "review"
        if candidate.get("passed") is True or status_value in {"ok", "pass", "passed"}:
            if not skipped:
                return "pass"

    # Explicit legacy top-level verdicts remain authoritative.  Do not use
    # result.execution.status="ok" as a substitute for one.
    for candidate in (report, result):
        if not isinstance(candidate, dict):
            continue
        if candidate.get("passed") is False:
            return "result_pending" if candidate.get("skipped") else "fail"
        if candidate.get("passed") is True:
            return "pass"
        status_value = str(candidate.get("status") or "").strip().lower()
        if status_value in {"fail", "failed", "error"}:
            return "fail"
        if status_value in {"ok", "pass", "passed"} and not execution_nodes:
            return "pass"

    return "result_pending" if execution_nodes or skipped else "unknown"


def submit_progress(
    db: Session,
    *,
    validation_id: int,
    stage: str,
    message: str,
    detail: Optional[Dict[str, Any]] = None,
) -> BoardValidationStatusResponse:
    row = get_validation_for_agent(db, validation_id)
    if row.status not in ("PENDING", "PROCESSING"):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="仅进行中的验证可上报进度",
        )
    history: List[Dict[str, Any]] = []
    _, prev_prog, _ = _parse_report_json(row)
    if prev_prog and prev_prog.history:
        history = list(prev_prog.history)
    entry = {
        "stage": stage,
        "message": message,
        "detail": detail or {},
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    history.append(entry)
    history = history[-25:]
    payload = {
        "type": "progress",
        "stage": stage,
        "message": message,
        "detail": detail or {},
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "history": history,
    }
    with begin_transaction(db):
        crud_yolo_board_validation.update_row(
            db,
            row,
            report_json=json.dumps(payload, ensure_ascii=False)[:500_000],
        )
    fresh = crud_yolo_board_validation.get(db, validation_id)
    expected_contract = build_board_threshold_contract(
        crud_yolo_bm1688_export.get(db, row.export_id)
    )
    return _row_to_status_response(
        fresh, include_report=False, threshold_contract=expected_contract
    )


def _row_to_status_response(
    row,
    *,
    include_report: bool,
    quality_report: Optional[Dict[str, Any]] = None,
    threshold_contract: Optional[Dict[str, Any]] = None,
) -> BoardValidationStatusResponse:
    summary, progress, report = _parse_report_json(row)
    comparison = None
    if include_report and report and quality_report:
        from labelu.internal.application.service.board_validation_compare import (
            build_train_board_comparison,
        )

        comparison = build_train_board_comparison(
            quality_report=quality_report, board_report=report
        )
    edge_result_status = "unknown"
    status_value = str(getattr(row, "status", "") or "").upper()
    if status_value in {"PENDING", "PROCESSING"}:
        edge_result_status = "running"
    elif status_value == "FAILED":
        edge_result_status = "fail"
    elif status_value in {"COMPLETED", "PARTIAL_COMPLETED"}:
        if isinstance(report, dict) and report:
            edge_result_status = edge_result_status_from_report(report)
        else:
            edge_result_status = "result_pending"
    return BoardValidationStatusResponse(
        validation_id=row.id,
        export_id=row.export_id,
        status=row.status,
        error_message=row.error_message,
        workflow_stage="edge_validation",
        edge_result_status=edge_result_status,
        threshold_contract=threshold_contract or {},
        report_summary=summary,
        execution_device_host=execution_device_host_from_report(report),
        completed_at=row.completed_at,
        progress=progress,
        report=report if include_report else None,
        train_board_comparison=comparison,
    )


def execution_device_host_from_report(report: Any) -> Optional[str]:
    """Return the device that actually executed this validation run."""
    if not isinstance(report, dict):
        return None

    result = report.get("result") if isinstance(report.get("result"), dict) else {}
    candidates = [
        report.get("execution_device_host"),
        report.get("remote_host"),
        (
            (report.get("execution") or {}).get("remote_host")
            if isinstance(report.get("execution"), dict)
            else None
        ),
        (
            (result.get("execution") or {}).get("remote_host")
            if isinstance(result.get("execution"), dict)
            else None
        ),
        (
            (result.get("execution_summary") or {}).get("remote_host")
            if isinstance(result.get("execution_summary"), dict)
            else None
        ),
        (
            (report.get("ssh_summary") or {}).get("remote_host")
            if isinstance(report.get("ssh_summary"), dict)
            else None
        ),
    ]
    for candidate in candidates:
        host = str(candidate or "").strip()
        if host:
            return host
    return None


def get_status_for_export(
    db: Session,
    *,
    export_id: int,
    include_report: bool = False,
    quality_report: Optional[Dict[str, Any]] = None,
) -> Optional[BoardValidationStatusResponse]:
    row = crud_yolo_board_validation.get_latest_for_export(db, export_id=export_id)
    if not row:
        return None
    export_row = crud_yolo_bm1688_export.get(db, export_id)
    return _row_to_status_response(
        row,
        include_report=include_report,
        quality_report=quality_report,
        threshold_contract=build_board_threshold_contract(export_row),
    )
