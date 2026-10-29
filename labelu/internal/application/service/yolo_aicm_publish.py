"""将本系统训练完成且已编译 BM1688 bmodel 的模型发布到 AICM AI 控制后台。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import requests
from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_annotation_taxonomy,
    crud_project,
    crud_strategy_task_integration,
    crud_task,
    crud_yolo_bm1688_export,
    crud_yolo_board_validation,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.response.board_agent import (
    BoardValidationProgressResponse,
    BoardValidationStatusResponse,
)
from labelu.internal.application.command.yolo_aicm_publish import (
    AicmPublishRequestCommand,
    CreateAicmPublishEnvironmentCommand,
    PatchAicmPublishConfigCommand,
    SetActiveAicmPublishEnvironmentCommand,
    UpdateAicmPublishEnvironmentCommand,
)
from labelu.internal.application.command.yolo_edge_validation import (
    normalize_edge_threshold_payload,
)
from labelu.internal.application.response.yolo_aicm_publish import (
    AicmPublishBmodelOption,
    AicmPublishConfigResponse,
    AicmPublishEnvironmentResponse,
    AicmPublishEnvironmentsBundleResponse,
    AicmRuntimeTemplateCandidateResponse,
    AicmPublishHistoryRowResponse,
    AicmPublishJobResponse,
    AicmPublishResultResponse,
    AicmPublishStepItem,
    AicmPublishableRowResponse,
)
from labelu.internal.application.service import board_agent
from labelu.internal.common.aicm_publish_job import (
    PublishJobTracker,
    create_job,
    list_jobs,
    load_job,
)
from labelu.internal.common.strategy_task_marker import strategy_task_marker
from labelu.internal.common.bm1688_compile_contract import profile_snapshot_digest
from labelu.internal.common.edge_validation_evidence import evaluate_edge_validation_evidence
from labelu.internal.common.model_manifest_signature import (
    verify_manifest_artifacts,
    verify_model_manifest,
)
from labelu.internal.application.response.yolo_train import YoloValidationPassAssessment
from labelu.internal.common.archive import safe_extract_zip
from labelu.internal.application.service import task as task_service
from labelu.internal.application.service import yolo_bm1688_export as bm_export_service
from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.application.service.yolo_quality_assessment import (
    build_assessment_payload,
    quality_policy_snapshot_for_job,
)
from labelu.internal.application.service.yolo_train import (
    _class_metrics_for_job,
    _classes_fallback_for_job,
    _creator_emails_by_user_ids,
    _job_work_root,
    _sample_counts_for_job,
    _test_val_metrics_for_job,
    _training_val_metrics_for_job,
    _validation_pass_row,
)
from labelu.internal.common.yolo_ultralytics_split_val import (
    read_class_names_from_data_yaml,
)
from labelu.internal.common.yolo_rect_class_slug import (
    build_rect_label_slug_aliases,
    normalize_rect_class_names_to_slugs,
)
from labelu.internal.domain.models.annotation_taxonomy import RuntimePublishTemplate
from labelu.internal.common.aicm_publish_config import (
    DEFAULT_ALGORITHM_TYPE,
    DEFAULT_CHIP_TYPE,
    DEFAULT_MODEL_ALG_TYPE,
    DEFAULT_MODEL_FUNCTION,
    config_is_complete,
    config_missing_fields,
    create_environment_record,
    delete_environment_record,
    find_environment_record,
    get_active_environment_id,
    load_aicm_publish_config,
    load_environments_store,
    normalize_aicm_base_url,
    repair_stored_config_if_needed,
    save_aicm_publish_config,
    set_active_environment_id,
    update_environment_record,
    _env_record_to_cfg,
)
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import (
    assert_user_has_task_access,
    is_consistent_pool_admin,
    is_task_superuser,
)
from labelu.internal.common.yolo_training_metrics import (
    actual_epochs_for_train_job,
    read_classes_from_metrics_summary,
)
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus

AICM_TEMPLATE_SUBDIR = "aicm_model_templates"
AICM_PUBLISH_WORK_SUBDIR = "aicm_publish_work"
AICM_UPLOAD_PRODUCT_MODEL = "bm1688"
PUBLISH_VERSION_SEQ_FILE = "publish_version_seq.json"
# 仅当模板缺失 labels.txt 时用于日志/回退；发布写入时首行以模板文件为准（可能是 ????? 或 ?????? 等）
LABELS_FIRST_LINE_PLACEHOLDER = "?????"
DIRECT_LABELS_FIRST_LINE_PLACEHOLDER = "??????"


def _assert_project_access(
    db: Session,
    current_user: User,
    project_id: Optional[int],
) -> Optional[int]:
    """Validate an explicit project scope and return its normalized id.

    A missing ``project_id`` remains compatible with legacy aggregate callers.
    Once a caller supplies a project, however, the project itself must be
    visible to the user before any export/job rows are queried.
    """

    if project_id is None:
        return None
    normalized = int(project_id)
    project = crud_project.get(db, normalized)
    if project is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="项目不存在",
        )
    if is_task_superuser(current_user) or int(project.owner_id) == int(current_user.id):
        return normalized
    member = crud_project.get_member(db, normalized, int(current_user.id))
    if member is None or str(member.status or "").upper() != "ACTIVE":
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="无权访问该项目",
        )
    return normalized


def _publish_raw_task(db: Session, raw: dict[str, Any]):
    """Resolve a persisted publish job's task through its explicit lineage."""

    task_id = raw.get("task_id")
    if task_id is None:
        export_id = raw.get("export_id")
        if export_id:
            export_row = crud_yolo_bm1688_export.get(db, int(export_id))
            task_id = getattr(export_row, "task_id", None)
    if task_id is None:
        return None
    return crud_task.get(db, int(task_id))


def _publish_raw_in_project(
    db: Session,
    raw: dict[str, Any],
    project_id: int,
) -> bool:
    """Return true only when the complete publish lineage is project-scoped.

    Legacy JSON jobs without an explicit project or a project-less parent are
    deliberately excluded from an explicit project view.  This prevents a
    nullable legacy field from being guessed into the requested project.
    """

    task = _publish_raw_task(db, raw)
    if task is None or getattr(task, "project_id", None) is None:
        return False
    if int(task.project_id) != int(project_id):
        return False
    export_id = raw.get("export_id")
    if export_id:
        export_row = crud_yolo_bm1688_export.get(db, int(export_id))
        if export_row is None:
            return False
        export_task = crud_task.get(db, int(export_row.task_id))
        if (
            export_task is None
            or getattr(export_task, "project_id", None) is None
            or int(export_task.project_id) != int(project_id)
            or int(export_task.id) != int(task.id)
        ):
            return False
        train_job = crud_yolo_train_job.get(db, getattr(export_row, "yolo_train_job_id", 0))
        if train_job is None or getattr(train_job, "project_id", None) is None:
            return False
        if (
            int(train_job.project_id) != int(project_id)
            or getattr(train_job, "task_id", None) is None
            or int(train_job.task_id) != int(export_task.id)
        ):
            return False
    return True
DIRECT_YOLOV11_BMODEL_FILENAME = "yolov11n_int8_1b.bmodel"
# (connect_timeout, read_timeout) 秒
AICM_HTTP_TIMEOUT = (20, 600)
AICM_UPLOAD_WRITE_TIMEOUT_SECONDS = 600.0
AICM_UPLOAD_HTTP_TIMEOUT = httpx.Timeout(
    connect=float(AICM_HTTP_TIMEOUT[0]),
    read=float(AICM_HTTP_TIMEOUT[1]),
    write=AICM_UPLOAD_WRITE_TIMEOUT_SECONDS,
    pool=float(AICM_HTTP_TIMEOUT[0]),
)
AICM_MODEL_SET_LOOKUP_ATTEMPTS = 8
AICM_MODEL_SET_LOOKUP_DELAY_SECONDS = 0.5
AICM_UPLOAD_MODEL_LOOKUP_ATTEMPTS = 30
AICM_UPLOAD_MODEL_LOOKUP_DELAY_SECONDS = 1.0
AICM_PUBLISH_CLAIM_LEASE_SECONDS = 6 * 60 * 60
AICM_MODEL_NAME_MAX_LENGTH = 20


class _AicmPublishLeaseLost(RuntimeError):
    """The AICM publisher no longer owns its database claim."""


class _AicmPublishLeaseMonitor:
    """Keep the external-publication claim alive across long AICM uploads."""

    def __init__(
        self,
        *,
        export_id: int,
        job_id: str,
        lease_owner: str,
        fencing_token: int,
        lease_seconds: int,
        session_scope_factory,
    ) -> None:
        self.export_id = int(export_id)
        self.job_id = str(job_id)
        self.lease_owner = str(lease_owner)
        self.fencing_token = int(fencing_token)
        self.lease_seconds = max(60, int(lease_seconds))
        self._session_scope = session_scope_factory
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._lost = False
        self._last_error: Optional[Exception] = None

    @property
    def lost(self) -> bool:
        with self._lock:
            return self._lost

    def _mark_lost(self, exc: Optional[Exception] = None) -> None:
        with self._lock:
            self._lost = True
            if exc is not None:
                self._last_error = exc

    def ensure_current(self) -> None:
        if self.lost:
            raise _AicmPublishLeaseLost("AICM 发布租约已失效")
        try:
            with self._session_scope() as db:
                from labelu.internal.common.db import begin_transaction

                with begin_transaction(db):
                    renewed = crud_yolo_bm1688_export.renew_publish_claim(
                        db,
                        export_id=self.export_id,
                        job_id=self.job_id,
                        lease_owner=self.lease_owner,
                        fencing_token=self.fencing_token,
                        lease_seconds=self.lease_seconds,
                    )
            if not renewed:
                self._mark_lost()
                raise _AicmPublishLeaseLost("AICM 发布租约已被接管或过期")
        except _AicmPublishLeaseLost:
            raise
        except Exception as exc:
            self._mark_lost(exc)
            raise _AicmPublishLeaseLost("AICM 发布租约续租失败") from exc

    def _run(self) -> None:
        # The lease is deliberately long enough for the largest upload, but
        # still renews periodically so retries after a stalled process are
        # bounded by the same database fencing contract.
        interval = max(1.0, min(60.0, self.lease_seconds / 3.0))
        while not self._stop.wait(interval):
            try:
                self.ensure_current()
            except Exception:
                return

    def start(self) -> "_AicmPublishLeaseMonitor":
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name=f"aicm-publish-lease-{self.export_id}",
            )
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def _edge_validation_status_for_export(
    db: Session,
    *,
    export_id: int,
    include_report: bool = False,
    quality_report: Optional[Dict[str, Any]] = None,
):
    board_status = board_agent.get_status_for_export(
        db,
        export_id=export_id,
        include_report=include_report,
        quality_report=quality_report,
    )
    board_status = _normalize_board_validation_status(
        board_status,
        export_id=export_id,
        include_report=include_report,
        quality_report=quality_report,
    )
    manual_status = _manual_edge_validation_status_for_export(db, export_id=export_id)
    manual_status = _normalize_edge_validation_status_report(
        manual_status,
        include_report=include_report,
        quality_report=quality_report,
    )
    return _latest_edge_validation_status(board_status, manual_status)


def _latest_edge_validation_status(board_status: Any, manual_status: Any):
    if board_status is None:
        return manual_status
    if manual_status is None:
        return board_status
    board_at = _edge_validation_updated_at(board_status)
    manual_at = _edge_validation_updated_at(manual_status)
    if manual_at and board_at:
        return manual_status if manual_at >= board_at else board_status
    if manual_at:
        return manual_status
    if board_at:
        return board_status
    if _edge_validation_priority(manual_status) >= _edge_validation_priority(
        board_status
    ):
        return manual_status
    return board_status


def edge_validation_statuses_for_exports(
    db: Session,
    export_rows: List[Any],
) -> Dict[int, BoardValidationStatusResponse]:
    """Load the latest board/manual validation state without per-export SQL."""
    exports = {
        int(row.id): row
        for row in export_rows
        if getattr(row, "id", None) is not None
    }
    if not exports:
        return {}

    export_ids = list(exports)
    board_rows = crud_yolo_board_validation.latest_by_export_ids(
        db, export_ids=export_ids
    )
    manual_rows = crud_yolo_edge_validation_job.latest_by_export_ids(
        db, export_ids=export_ids
    )
    statuses: Dict[int, BoardValidationStatusResponse] = {}
    for export_id, export_row in exports.items():
        board_row = board_rows.get(export_id)
        board_status = (
            board_agent._row_to_status_response(
                board_row,
                include_report=False,
                threshold_contract=board_agent.build_board_threshold_contract(
                    export_row
                ),
            )
            if board_row is not None
            else None
        )
        manual_row = manual_rows.get(export_id)
        manual_status = (
            _manual_edge_validation_status_for_export(
                db,
                export_id=export_id,
                row=manual_row,
                export_row=export_row,
            )
            if manual_row is not None
            else None
        )
        latest = _latest_edge_validation_status(board_status, manual_status)
        if latest is not None:
            statuses[export_id] = latest
    return statuses


def _load_legacy_board_validation_report(export_id: int) -> Optional[Dict[str, Any]]:
    report_path = (
        Path(settings.MEDIA_ROOT)
        / settings.YOLO_BM1688_EXPORT_SUBDIR
        / str(export_id)
        / "edge_ssh_pull"
        / "output"
        / "board_report.json"
    )
    if not report_path.is_file():
        return None
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _edge_result_status_from_board_report(
    edge_validation: Any,
    report: Dict[str, Any],
) -> str:
    status_value = str(getattr(edge_validation, "status", "") or "").upper()
    if status_value in {"PENDING", "PROCESSING"}:
        return "running"
    if status_value == "FAILED":
        return "fail"
    if status_value == "PARTIAL_COMPLETED":
        return "result_pending"
    if status_value != "COMPLETED":
        return "unknown"

    # Manual edge reports nest the actual execution verdict under result.
    # Check it before the legacy top-level fallback so COMPLETED means only
    # execution finished, not that the quality gate passed.
    nested_status = board_agent.edge_result_status_from_report(report)
    if nested_status in {"fail", "review", "result_pending"}:
        return nested_status

    passed = report.get("passed")
    if passed is True:
        return "pass"
    if passed is False:
        return "result_pending" if report.get("skipped") else "fail"
    report_status = str(report.get("status") or "").strip().lower()
    if report_status in {"ok", "pass", "passed", "completed"}:
        return "pass"
    if report_status in {"fail", "failed", "error"}:
        return "fail"
    if report_status == "skipped":
        return "result_pending"

    derived = _edge_result_status_from_validation(edge_validation)
    if derived != "result_pending":
        return derived
    current = str(getattr(edge_validation, "edge_result_status", "") or "")
    if current not in {"", "unknown", "result_pending"}:
        return current
    return derived


def _normalize_edge_validation_status_report(
    edge_validation: Optional[BoardValidationStatusResponse],
    *,
    include_report: bool,
    quality_report: Optional[Dict[str, Any]],
) -> Optional[BoardValidationStatusResponse]:
    if edge_validation is None or not include_report:
        return edge_validation
    report = getattr(edge_validation, "report", None)
    if not isinstance(report, dict) or not report:
        return edge_validation

    normalized = board_agent.normalize_board_report_metrics(report)
    edge_validation.report = normalized
    if quality_report is not None:
        from labelu.internal.application.service.board_validation_compare import (
            build_train_board_comparison,
        )

        edge_validation.train_board_comparison = build_train_board_comparison(
            quality_report=quality_report,
            board_report=normalized,
        )
    return edge_validation


def _normalize_board_validation_status(
    edge_validation: Optional[BoardValidationStatusResponse],
    *,
    export_id: int,
    include_report: bool,
    quality_report: Optional[Dict[str, Any]],
) -> Optional[BoardValidationStatusResponse]:
    if edge_validation is None or not include_report:
        return edge_validation
    report = getattr(edge_validation, "report", None)
    if not isinstance(report, dict) or not report:
        report = _load_legacy_board_validation_report(export_id)
    if not isinstance(report, dict) or not report:
        return edge_validation

    edge_validation.report = report
    edge_validation = _normalize_edge_validation_status_report(
        edge_validation,
        include_report=True,
        quality_report=quality_report,
    )
    if edge_validation is None or not isinstance(edge_validation.report, dict):
        return edge_validation
    edge_validation.edge_result_status = _edge_result_status_from_board_report(
        edge_validation,
        edge_validation.report,
    )
    if not edge_validation.report_summary:
        edge_validation.report_summary = (
            str(edge_validation.report.get("summary") or "")[:500] or None
        )
    return edge_validation


def _manual_edge_validation_status_for_export(
    db: Session,
    *,
    export_id: int,
    row: Any = None,
    export_row: Any = None,
) -> Optional[BoardValidationStatusResponse]:
    if row is None:
        row = crud_yolo_edge_validation_job.get_latest_for_export(
            db, export_id=export_id
        )
    if not row:
        return None
    result = _decode_edge_validation_json(getattr(row, "result_json", None))
    progress = _decode_edge_validation_json(getattr(row, "progress_json", None))
    execution = result.get("execution") if isinstance(result, dict) else None
    if not isinstance(execution, dict):
        execution = {}
    progress_response = None
    if progress:
        history = (
            progress.get("history") if isinstance(progress.get("history"), list) else []
        )
        progress_response = BoardValidationProgressResponse(
            stage=str(progress.get("stage") or ""),
            message=str(progress.get("message") or ""),
            detail=(
                progress.get("detail")
                if isinstance(progress.get("detail"), dict)
                else None
            ),
            updated_at=str(progress.get("updated_at") or "") or None,
            history=history[-20:],
        )
    mode = str(getattr(row, "mode", "") or "").strip()
    mode_label = "run_only" if mode == "run_only" else "run_and_eval"
    counts = (
        f"images={int(getattr(row, 'image_count', 0) or 0)},"
        f" labeled={int(getattr(row, 'labeled_image_count', 0) or 0)}"
    )
    summary_parts = [f"manual:{mode_label}", counts]
    if getattr(row, "report_summary", None):
        summary_parts.append(str(row.report_summary))
    export_id = int(getattr(row, "export_id", 0) or 0)
    job_id = int(getattr(row, "id", 0) or 0)
    report_payload: dict = {
        "source": "manual_edge_validation",
        "job_id": job_id,
        "export_id": export_id,
        "mode": mode,
        "threshold_source": getattr(row, "threshold_source", None),
        "image_count": int(getattr(row, "image_count", 0) or 0),
        "labeled_image_count": int(getattr(row, "labeled_image_count", 0) or 0),
        "unlabeled_image_count": int(getattr(row, "unlabeled_image_count", 0) or 0),
        "result": result if isinstance(result, dict) else {},
        "execution": execution,
        "updated_at": getattr(row, "updated_at", None),
    }
    thresholds = _decode_edge_validation_json(getattr(row, "thresholds_json", None))
    ai_thresholds = _decode_edge_validation_json(
        getattr(row, "ai_thresholds_json", None)
    )
    if thresholds:
        try:
            thresholds = normalize_edge_threshold_payload(
                thresholds,
                field_name="thresholds",
            )
        except ValueError:
            # Keep historical values readable, but never omit the unit from
            # the API contract when an old record cannot be fully normalized.
            thresholds = dict(thresholds)
            thresholds.setdefault("threshold_unit", "ratio_0_1")
        report_payload["thresholds"] = thresholds
    if ai_thresholds:
        report_payload["ai_thresholds"] = ai_thresholds
    # Manual/legacy jobs must expose the same immutable threshold contract as
    # board-agent jobs. The job snapshot wins because it describes the values
    # actually used for this validation run.
    threshold_contract: Dict[str, Any] = {}
    if export_row is None and hasattr(db, "query"):
        export_row = crud_yolo_bm1688_export.get(db, export_id)
    if export_row is not None or thresholds:
        threshold_contract = board_agent.build_board_threshold_contract(
            export_row,
            fallback=thresholds or None,
        )
    if export_id and job_id:
        from labelu.internal.application.service.yolo_edge_validation_pack import (
            load_manual_edge_board_report,
        )

        board_report = load_manual_edge_board_report(export_id=export_id, job_id=job_id)
        if isinstance(board_report, dict):
            board_report = board_agent.normalize_board_report_metrics(board_report)
            # SSH 验证的完整证据保存在回传的 board_report.json 中。以前这里只
            # 取了指标和预览，导致发布门禁看不到本次编译档案摘要、执行世代和
            # 固定评估集绑定，最终把“证据缺失”误报成“档案不一致”。保留整份
            # 报告用于详情展示，并把门禁需要的字段提升到统一报告上下文。
            report_payload["board_report"] = board_report
            for key in (
                "model_identity",
                "artifact_execution_signature",
                "execution_signature",
                "execution_generation",
                "execution_generation_state",
                "execution_fencing",
                "profile_digest",
                "bm1688_profile",
                "fixed_eval_dataset",
                "quality_rule",
            ):
                value = board_report.get(key)
                if value not in (None, "", {}, []):
                    report_payload.setdefault(key, value)
            metrics = board_report.get("metrics")
            if isinstance(metrics, dict) and metrics:
                report_payload["metrics"] = metrics
            if board_report.get("bmcv_infer"):
                report_payload["bmcv_infer"] = board_report["bmcv_infer"]
    return BoardValidationStatusResponse(
        validation_id=job_id,
        export_id=export_id,
        status=str(getattr(row, "status", "") or "").upper() or "PENDING",
        error_message=getattr(row, "error_message", None),
        workflow_stage="edge_validation",
        edge_result_status=_edge_result_status_from_validation(row),
        report_summary="; ".join(summary_parts),
        execution_device_host=(
            str(getattr(row, "edge_host", None) or "").strip()
            or board_agent.execution_device_host_from_report(report_payload)
        ),
        completed_at=getattr(row, "completed_at", None),
        progress=progress_response,
        threshold_contract=threshold_contract,
        report=report_payload,
    )


def _decode_edge_validation_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _edge_validation_updated_at(edge_validation: Any) -> Optional[datetime]:
    completed_at = getattr(edge_validation, "completed_at", None)
    if completed_at is not None:
        return completed_at
    report = getattr(edge_validation, "report", None)
    if isinstance(report, dict):
        updated_at = report.get("updated_at")
        if isinstance(updated_at, datetime):
            return updated_at
    return None


def _edge_validation_priority(edge_validation: Any) -> int:
    status_value = str(getattr(edge_validation, "status", "") or "").upper()
    order = {
        "COMPLETED": 5,
        "PARTIAL_COMPLETED": 4,
        "PROCESSING": 3,
        "PENDING": 2,
        "FAILED": 1,
    }
    return order.get(status_value, 0)


def _edge_result_status_from_validation(edge_validation: Any) -> str:
    if edge_validation is None:
        return "not_started"
    status_value = str(getattr(edge_validation, "status", "") or "").upper()
    report = getattr(edge_validation, "report", None)
    if status_value in {"PENDING", "PROCESSING"}:
        return "running"
    if status_value == "FAILED":
        return "fail"
    if status_value == "PARTIAL_COMPLETED":
        return "result_pending"
    if status_value != "COMPLETED":
        return "unknown"
    result: dict[str, Any] = {}
    if isinstance(report, dict):
        nested = report.get("result")
        if isinstance(nested, dict):
            result = nested
    if not result:
        decoded = _decode_edge_validation_json(
            getattr(edge_validation, "result_json", None)
        )
        if isinstance(decoded, dict):
            result = decoded
    if not result:
        return "result_pending"
    derived = board_agent.edge_result_status_from_report({"result": result})
    if derived in {"pass", "review", "fail"}:
        return derived
    if str(result.get("status") or "").lower() == "fail":
        return "fail"
    if str(result.get("status") or "").lower() == "ok":
        return "pass"
    execution = (
        result.get("execution") if isinstance(result.get("execution"), dict) else {}
    )
    ingest = (
        execution.get("ingest") if isinstance(execution.get("ingest"), dict) else {}
    )
    bmodel_sop = (
        ingest.get("bmodel_sop") if isinstance(ingest.get("bmodel_sop"), dict) else {}
    )
    sop_status = str(bmodel_sop.get("status") or "").lower()
    if sop_status == "ok":
        return "pass"
    if sop_status == "fail":
        return "fail"
    eval_summary = result.get("eval_summary")
    if isinstance(eval_summary, dict) and eval_summary:
        eval_status = str(eval_summary.get("status") or "").lower()
        if eval_status == "ok" and eval_summary.get("map50") is not None:
            return "pass"
        if eval_status == "fail":
            return "fail"
        return "result_pending"
    return "result_pending"


def _publish_gate_reason_codes(
    *,
    edge_validation: Any,
    validation_pass: Optional[YoloValidationPassAssessment] = None,
    artifact_manifest: Optional[Dict[str, Any]] = None,
    export_id: Optional[int] = None,
) -> List[str]:
    codes: List[str] = []
    if edge_validation is None:
        codes.append("edge_validation_missing")
    else:
        status_value = str(getattr(edge_validation, "status", "") or "").upper()
        if status_value in {"PENDING", "PROCESSING", "PARTIAL_COMPLETED"}:
            codes.append("edge_validation_incomplete")
        elif status_value == "FAILED":
            codes.append("edge_validation_failed")
        manifest = artifact_manifest if isinstance(artifact_manifest, dict) else {}
        profile = manifest.get("bm1688_profile")
        if not isinstance(profile, dict):
            profile = manifest.get("profile_snapshot")
        expected_profile_digest = (
            profile_snapshot_digest(profile) if isinstance(profile, dict) else ""
        )
        if expected_profile_digest:
            report = getattr(edge_validation, "report", None)
            if not isinstance(report, dict):
                report = _decode_edge_validation_json(
                    getattr(edge_validation, "result_json", None)
                )
            evidence = evaluate_edge_validation_evidence(
                report,
                expected={"profile_digest": expected_profile_digest},
            )
            if "profile_digest_missing" in evidence.get("reason_codes", []):
                codes.append("profile_digest_missing")
            elif "profile_digest_mismatch" in evidence.get("reason_codes", []):
                codes.append("profile_digest_mismatch")
        # Keep the candidate list in sync with the publish endpoint.  A
        # completed row is not sufficient by itself: the board report must
        # carry the sealed evaluation, runtime and execution-fencing evidence
        # required for release.  This is intentionally evaluated only when a
        # compile manifest is available; legacy callers and test fixtures that
        # have no manifest retain their existing status semantics.
        if isinstance(manifest, dict) and manifest:
            report = getattr(edge_validation, "report", None)
            if not isinstance(report, dict):
                report = _decode_edge_validation_json(
                    getattr(edge_validation, "result_json", None)
                )
            expected: Dict[str, Any] = {}
            if expected_profile_digest:
                expected["profile_digest"] = expected_profile_digest
            execution_signature = str(
                manifest.get("execution_signature") or ""
            ).strip()
            if execution_signature:
                expected["execution_generation"] = execution_signature
                expected["require_execution_generation_state"] = True
            expected["required_precisions"] = ["fp32", "fp16", "int8"]
            compiler = manifest.get("compiler")
            compiler_cfg = compiler.get("config") if isinstance(compiler, dict) else {}
            if isinstance(compiler_cfg, dict) and isinstance(
                compiler_cfg.get("quality_rule"), dict
            ):
                expected["quality_rule"] = compiler_cfg["quality_rule"]
            evidence = evaluate_edge_validation_evidence(report, expected=expected)
            codes.extend(evidence.get("reason_codes") or [])
            if _labeled_count_from_edge_report(report) <= 0:
                codes.append("fixed_eval_labeled_images_missing")
            if status_value != "COMPLETED":
                codes.append("edge_validation_incomplete")
    if (
        validation_pass is not None
        and str(getattr(validation_pass, "status", "") or "").strip()
        == "\u4e0d\u8fbe\u6807"
    ):
        codes.append("training_validation_failed")
    manifest_reason = _model_manifest_publish_reason(
        artifact_manifest,
        export_id=export_id,
    )
    if manifest_reason:
        codes.append(manifest_reason)
    return list(dict.fromkeys(codes))


def _model_manifest_signature_required() -> bool:
    return bool(
        getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_REQUIRED", False)
        or str(getattr(settings, "APP_ENV", "") or "").strip().lower()
        == "production"
    )


def _model_manifest_publish_reason(
    artifact_manifest: Any, *, export_id: Optional[int] = None
) -> Optional[str]:
    """Return a stable publish-block code for the signed export manifest.

    Historical exports without a signature remain readable outside the
    production-required mode. Once a manifest carries a signature, its files
    are always rehashed before publication.
    """

    manifest = artifact_manifest if isinstance(artifact_manifest, dict) else {}
    has_signature = bool(str(manifest.get("manifest_hmac_sha256") or "").strip())
    required = _model_manifest_signature_required()
    if not required and not has_signature:
        return None
    secret = str(getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_SECRET", "") or "").strip()
    if not secret:
        return "manifest_signature_secret_missing"
    ok, reason = verify_model_manifest(
        manifest,
        secret=secret,
        expected_key_id=str(
            getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_KEY_ID", "v1") or "v1"
        ),
        expected_export_id=export_id,
        required=True,
    )
    if not ok:
        return reason
    if export_id is not None:
        ok, reasons = verify_manifest_artifacts(
            manifest,
            root=bm_export_service._export_dir_for_row(int(export_id)),
        )
        if not ok:
            return reasons[0] if reasons else "manifest_artifact_integrity_failed"
    return None


def _assert_model_manifest_for_publish(
    export_id: int, artifact_manifest: Any
) -> None:
    reason = _model_manifest_publish_reason(artifact_manifest, export_id=export_id)
    if reason:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"ModelManifest 未通过发布校验：{reason}",
        )


def _assert_edge_profile_for_publish(
    edge_validation: Any, artifact_manifest: Any
) -> None:
    """发布前强制确认边端证据对应本次服务器编译档案。"""
    manifest = artifact_manifest if isinstance(artifact_manifest, dict) else {}
    profile = manifest.get("bm1688_profile")
    if not isinstance(profile, dict):
        profile = manifest.get("profile_snapshot")
    if not isinstance(profile, dict):
        return  # 历史制品没有档案时保持历史读取兼容
    expected_digest = profile_snapshot_digest(profile)
    report = getattr(edge_validation, "report", None)
    if not isinstance(report, dict):
        report = _decode_edge_validation_json(getattr(edge_validation, "result_json", None))
    evidence = evaluate_edge_validation_evidence(
        report,
        expected={"profile_digest": expected_digest},
    )
    reasons = evidence.get("reason_codes") or []
    if "profile_digest_missing" in reasons or "profile_digest_mismatch" in reasons:
        actual_digest = str(evidence.get("profile_digest") or "").strip()
        if "profile_digest_missing" in reasons:
            detail = "边端报告没有回传本次编译档案摘要"
        else:
            detail = (
                "边端回传摘要与本次编译档案不一致"
                f"（编译档案 {expected_digest[:12]}，边端 {actual_digest[:12] or '缺失'}）"
            )
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"{detail}，请重新执行当前编译档案的边端验证后再发布",
        )


def _publish_gate_status(
    *,
    reason_codes: List[str],
    release_gate_assessment: Any = None,
    quality_assessment: Any = None,
) -> str:
    # Explicit blocking evidence always wins over a stale or optimistic
    # quality assessment.  Otherwise the list can show “可发布” while the
    # publish endpoint correctly rejects the same export with HTTP 400.
    if reason_codes:
        return "blocked"
    if release_gate_assessment is not None:
        status_code = str(
            getattr(release_gate_assessment, "status_code", "") or ""
        ).strip()
        if status_code == "publishable":
            return "allowed"
        if status_code == "review_required":
            return "pending_review"
        if status_code == "blocked":
            return "blocked"
    release_status = ""
    if quality_assessment is not None:
        release_readiness = getattr(quality_assessment, "release_readiness", None)
        release_status = str(getattr(release_readiness, "status", "") or "").strip()
    if release_status == "\u53ef\u53d1\u5e03":
        return "allowed"
    if release_status == "\u9700\u590d\u6838":
        return "pending_review"
    if release_status == "\u4e0d\u53ef\u53d1\u5e03":
        return "blocked"
    return "unknown"


def _publish_block_reason_from_edge_validation(edge_validation: Any) -> Optional[str]:
    if edge_validation is None:
        return "边端验证未开始，禁止发布"
    status_value = str(getattr(edge_validation, "status", "") or "").upper()
    if status_value != "COMPLETED":
        return f"边端验证状态为 {status_value or 'UNKNOWN'}，未完成，禁止发布"
    return None


def _assert_edge_validation_completed_for_publish(db: Session, *, export_id: int):
    edge_validation = _edge_validation_status_for_export(db, export_id=export_id)
    blocked_reason = _publish_block_reason_from_edge_validation(edge_validation)
    if blocked_reason:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=blocked_reason,
        )
    return edge_validation


def _labeled_count_from_edge_report(report: Any) -> int:
    """Find the sealed fixed-evaluation labeled count without guessing a path."""
    if not isinstance(report, dict):
        return 0
    counts: List[int] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).lower() in {"labeled_count", "labeled_image_count"}:
                    try:
                        counts.append(int(child))
                    except (TypeError, ValueError):
                        pass
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(report)
    return max(counts, default=0)


def _assert_edge_evidence_for_publish(
    edge_validation: Any,
    artifact_manifest: Any,
    *,
    required_precisions: Tuple[str, ...] = ("fp32", "fp16", "int8"),
) -> None:
    """Require publish-grade board evidence, not merely a COMPLETED row.

    Older rows can still say COMPLETED even though they predate the evidence
    gate. Re-evaluate their report here so a zero-exit process, missing labels,
    or a missing fixed-eval binding cannot become a release.
    """
    if edge_validation is None:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="边端验证证据缺失，禁止发布",
        )
    report = getattr(edge_validation, "report", None)
    if not isinstance(report, dict):
        report = _decode_edge_validation_json(getattr(edge_validation, "result_json", None))
    manifest = artifact_manifest if isinstance(artifact_manifest, dict) else {}
    expected: Dict[str, Any] = {"required_precisions": list(required_precisions)}
    profile = manifest.get("bm1688_profile")
    if not isinstance(profile, dict):
        profile = manifest.get("profile_snapshot")
    if isinstance(profile, dict):
        expected["profile_digest"] = profile_snapshot_digest(profile)
    execution_signature = str(manifest.get("execution_signature") or "").strip()
    if execution_signature:
        expected["execution_generation"] = execution_signature
        expected["require_execution_generation_state"] = True
    compiler = manifest.get("compiler")
    compiler_cfg = compiler.get("config") if isinstance(compiler, dict) else {}
    if isinstance(compiler_cfg, dict):
        quality_rule = compiler_cfg.get("quality_rule")
        if isinstance(quality_rule, dict):
            expected["quality_rule"] = quality_rule
    evidence = evaluate_edge_validation_evidence(report, expected=expected)
    reasons = list(evidence.get("reason_codes") or [])
    if _labeled_count_from_edge_report(report) <= 0:
        reasons.append("fixed_eval_labeled_images_missing")
    if str(getattr(edge_validation, "status", "") or "").upper() != "COMPLETED":
        reasons.append("edge_validation_incomplete")
    if reasons:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="边端验证证据未满足发布条件：" + ",".join(dict.fromkeys(reasons)),
        )


def _log_step(
    steps: Optional[List[str]],
    msg: str,
    progress: Optional[PublishJobTracker] = None,
) -> None:
    if progress is not None:
        progress.log(msg)
    elif steps is not None:
        steps.append(msg)


def _templates_root() -> Path:
    return Path(settings.BASE_DATA_DIR) / AICM_TEMPLATE_SUBDIR


def _publish_work_root() -> Path:
    return Path(settings.BASE_DATA_DIR) / AICM_PUBLISH_WORK_SUBDIR


def _create_publish_work_dir(export_id: int) -> Path:
    root = _publish_work_root()
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for _ in range(10):
        work = root / f"export_{int(export_id)}_{timestamp}_{uuid.uuid4().hex[:8]}"
        try:
            work.mkdir()
            return work
        except FileExistsError:
            continue
    raise RuntimeError(f"无法为导出 {int(export_id)} 分配唯一发布目录")


def _safe_dir_name(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*]', "_", (name or "").strip())
    return s[:120] or "default"


def _environment_to_response(
    rec: dict[str, Any], *, active_id: str
) -> AicmPublishEnvironmentResponse:
    cfg = _env_record_to_cfg(rec)
    eid = str(rec.get("id") or "")
    password_reference_configured = bool(
        str(rec.get("password_encrypted") or "").strip()
    )
    password_runtime_available = bool(str(cfg.get("password") or "").strip())
    missing_fields = config_missing_fields(cfg)
    ready_to_publish = config_is_complete(cfg)
    if ready_to_publish:
        readiness_reason = "连接地址、账号和 MySQL 加密密码均已就绪"
    elif "password" in missing_fields and password_reference_configured:
        readiness_reason = (
            "密码已加密保存到 MySQL，但当前服务无法解密；"
            "请检查 PASSWORD_SECRET_KEY 是否与保存密码时一致"
        )
    elif "password" in missing_fields:
        readiness_reason = "尚未保存 AICM 密码，请编辑环境后重新录入"
    else:
        missing_labels = {
            "base_url": "后台 URL",
            "username": "用户名",
            "password": "运行时密码",
        }
        readiness_reason = "缺少" + "、".join(
            missing_labels.get(field, field) for field in missing_fields
        )
    return AicmPublishEnvironmentResponse(
        id=eid,
        name=str(rec.get("name") or ""),
        base_url=cfg.get("base_url") or "",
        username=cfg.get("username") or "",
        password_configured=password_reference_configured,
        password_runtime_available=password_runtime_available,
        password_masked=str(
            rec.get("password_masked_hint") or rec.get("password_masked") or ""
        ),
        password_source_type=str(rec.get("password_source_type") or ""),
        password_masked_hint=str(
            rec.get("password_masked_hint") or rec.get("password_masked") or ""
        ),
        password_updated_at=str(rec.get("password_updated_at") or "") or None,
        chip_type=cfg.get("chip_type") or DEFAULT_CHIP_TYPE,
        algorithm_type=cfg.get("algorithm_type") or DEFAULT_ALGORITHM_TYPE,
        verify_ssl=bool(cfg.get("verify_ssl")),
        ready_to_publish=ready_to_publish,
        missing_fields=missing_fields,
        readiness_reason=readiness_reason,
        is_active=eid == active_id,
        created_at=str(rec.get("created_at") or "") or None,
        updated_at=str(rec.get("updated_at") or "") or None,
    )


def list_environments_view(
    *,
    search: str | None = None,
    page: int | None = None,
    size: int | None = None,
) -> AicmPublishEnvironmentsBundleResponse:
    repair_stored_config_if_needed()
    store = load_environments_store()
    active_id = str(store.get("active_environment_id") or "").strip()
    all_rows = [
        _environment_to_response(rec, active_id=active_id)
        for rec in (store.get("environments") or [])
    ]
    active_environment = next(
        (row for row in all_rows if row.id == active_id),
        None,
    )
    rows = list(all_rows)
    keyword = str(search or "").strip().casefold()
    if keyword:
        rows = [
            row
            for row in rows
            if keyword in " ".join(
                str(value or "")
                for value in (
                    row.id,
                    row.name,
                    row.base_url,
                    row.username,
                    row.chip_type,
                    row.algorithm_type,
                    row.readiness_reason,
                )
            ).casefold()
        ]
    total = len(rows)
    ready_count = sum(1 for row in rows if row.ready_to_publish)
    resolved_page = int(page) if page is not None else None
    resolved_size = int(size) if size is not None else None
    if resolved_page is not None and resolved_size is not None:
        offset = resolved_page * resolved_size
        rows = rows[offset : offset + resolved_size]
    return AicmPublishEnvironmentsBundleResponse(
        active_environment_id=active_id,
        active_environment=active_environment,
        environments=rows,
        total=total,
        ready_count=ready_count,
        page=resolved_page,
        size=resolved_size if resolved_size is not None else len(rows),
        config_backend_label="MySQL 治理表（system_config_*）",
        template_cache_dir=str(_templates_root().resolve()),
    )


def create_environment(
    cmd: CreateAicmPublishEnvironmentCommand,
) -> AicmPublishEnvironmentResponse:
    bu = normalize_aicm_base_url(cmd.base_url or "")
    if not bu:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请填写 AICM 后台 URL",
        )
    if not (cmd.username or "").strip():
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请填写用户名",
        )
    try:
        rec = create_environment_record(
            name=cmd.name or "",
            base_url=bu,
            username=cmd.username,
            password=cmd.password,
            chip_type=cmd.chip_type,
            algorithm_type=cmd.algorithm_type,
            verify_ssl=cmd.verify_ssl,
            set_active=cmd.set_active,
        )
    except (RuntimeError, ValueError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=str(exc),
        ) from exc
    store = load_environments_store()
    return _environment_to_response(
        rec, active_id=str(store.get("active_environment_id") or "")
    )


def update_environment(
    environment_id: str, cmd: UpdateAicmPublishEnvironmentCommand
) -> AicmPublishEnvironmentResponse:
    if not find_environment_record(environment_id):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"发布环境不存在：{environment_id}",
        )
    if cmd.base_url is not None and not normalize_aicm_base_url(cmd.base_url):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请填写有效的 AICM 后台 URL",
        )
    try:
        rec = update_environment_record(
            environment_id,
            name=cmd.name,
            base_url=cmd.base_url,
            username=cmd.username,
            password_secret_action=cmd.password_secret_action,
            password=cmd.password,
            chip_type=cmd.chip_type,
            algorithm_type=cmd.algorithm_type,
            verify_ssl=cmd.verify_ssl,
        )
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=(
                status.HTTP_400_BAD_REQUEST
                if "密码" in str(exc)
                else status.HTTP_404_NOT_FOUND
            ),
            message=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=str(exc),
        ) from exc
    store = load_environments_store()
    return _environment_to_response(
        rec, active_id=str(store.get("active_environment_id") or "")
    )


def delete_environment(environment_id: str) -> AicmPublishEnvironmentsBundleResponse:
    try:
        delete_environment_record(environment_id)
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_404_NOT_FOUND,
            message=str(exc),
        ) from exc
    return list_environments_view()


def set_active_environment(
    cmd: SetActiveAicmPublishEnvironmentCommand,
) -> AicmPublishEnvironmentsBundleResponse:
    try:
        set_active_environment_id(cmd.environment_id)
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_404_NOT_FOUND,
            message=str(exc),
        ) from exc
    return list_environments_view()


def get_config_view() -> AicmPublishConfigResponse:
    """兼容旧接口：返回当前激活环境摘要。"""
    repair_stored_config_if_needed()
    cfg = load_aicm_publish_config()
    active_id = get_active_environment_id()
    active = find_environment_record(active_id) if active_id else None
    return AicmPublishConfigResponse(
        base_url=cfg.get("base_url") or "",
        username=cfg.get("username") or "",
        password_configured=bool(active and active.get("password_is_configured")),
        password_masked=str(
            (active or {}).get("password_masked_hint")
            or (active or {}).get("password_masked")
            or ""
        ),
        password_source_type=str((active or {}).get("password_source_type") or ""),
        password_masked_hint=str(
            (active or {}).get("password_masked_hint")
            or (active or {}).get("password_masked")
            or ""
        ),
        password_updated_at=str((active or {}).get("password_updated_at") or "")
        or None,
        chip_type=cfg.get("chip_type") or DEFAULT_CHIP_TYPE,
        algorithm_type=cfg.get("algorithm_type") or DEFAULT_ALGORITHM_TYPE,
        verify_ssl=bool(cfg.get("verify_ssl")),
        ready_to_publish=config_is_complete(cfg),
        missing_fields=config_missing_fields(cfg),
        template_cache_dir=str(_templates_root().resolve()),
    )


def patch_config(cmd: PatchAicmPublishConfigCommand) -> AicmPublishConfigResponse:
    bu_in = (
        normalize_aicm_base_url(cmd.base_url or "") if cmd.base_url is not None else ""
    )
    un_in = (cmd.username or "").strip() if cmd.username is not None else ""

    if cmd.base_url is not None and not bu_in:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请填写 AICM 后台 URL（仅 host:port，如 http://192.168.54.169:8102）",
        )
    if cmd.username is not None and not un_in:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请填写 AICM 用户名",
        )
    if cmd.password_secret_action == "set" and not str(cmd.password or "").strip():
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="更新密码时必须填写新的 AICM 密码",
        )

    try:
        save_aicm_publish_config(
            base_url=cmd.base_url,
            username=cmd.username,
            password_secret_action=cmd.password_secret_action,
            password=cmd.password,
            chip_type=cmd.chip_type,
            algorithm_type=cmd.algorithm_type,
            verify_ssl=cmd.verify_ssl,
        )
    except (RuntimeError, ValueError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=str(exc),
        ) from exc

    saved = load_aicm_publish_config()
    missing = config_missing_fields(saved)
    if missing:
        labels = {
            "base_url": "后台 URL",
            "username": "用户名",
            "password": "密码",
        }
        human = "、".join(labels.get(k, k) for k in missing)
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=(
                f"保存后配置仍不完整，缺少：{human}。请检查 MySQL 配置记录。"
            ),
        )
    return get_config_view()


def _preset_classes_by_slug(db: Session) -> dict[str, list[str]]:
    presets, _ = crud_annotation_taxonomy.label_preset_list(db, page=0, size=500)
    out: dict[str, list[str]] = {}
    for p in presets:
        slug = str(getattr(p, "slug", "") or "").strip()
        if not slug:
            continue
        out[slug] = task_service._rect_classes_from_label_preset(p)
    return out


def resolve_rect_preset_for_task(db: Session, task) -> Tuple[str, str]:
    """返回 (显示名, slug)。"""
    by_slug = _preset_classes_by_slug(db)
    slug = task_service._resolve_task_rect_preset_slug(
        task, preset_classes_by_slug=by_slug
    )
    if slug:
        row = crud_annotation_taxonomy.label_preset_get_by_slug(db, slug)
        if row and (row.name or "").strip():
            return str(row.name).strip(), slug
        if row:
            return slug, slug
    return "", slug or ""


def _template_meta_path(preset_name: str) -> Path:
    return _templates_root() / _safe_dir_name(preset_name) / "template_meta.json"


def _template_cached(preset_name: str) -> bool:
    meta_p = _template_meta_path(preset_name)
    if not meta_p.is_file():
        return False
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
        zp = Path(meta.get("zip_path") or "")
        return zp.is_file()
    except (json.JSONDecodeError, OSError):
        return False


def _normalize_base_url(url: str) -> str:
    u = normalize_aicm_base_url(url or "")
    if not u:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请先在模型发布页配置 AICM 根地址（仅 host:port，如 http://192.168.54.169:8102，不要含 /v2/login）",
        )
    return u


def _aicm_session(cfg: dict[str, Any]) -> requests.Session:
    base = _normalize_base_url(cfg.get("base_url") or "")
    user = (cfg.get("username") or "").strip()
    pw = (cfg.get("password") or "").strip()
    if not user or not pw:
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请配置 AICM 登录账号和密码，并确认 MySQL 中的加密密码可以正常解密",
        )
    sess = requests.Session()
    verify = bool(cfg.get("verify_ssl"))
    login_url = f"{base}/v2/login/"
    r = sess.post(
        login_url,
        data={"username": user, "password": pw},
        timeout=AICM_HTTP_TIMEOUT,
        verify=verify,
    )
    if r.status_code >= 400:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"AICM 登录失败 HTTP {r.status_code}: {r.text[:300]}",
        )
    return sess


def _extract_rows(payload: Any) -> List[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "list", "items", "data"):
        v = payload.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            for k2 in ("rows", "list", "items"):
                v2 = v.get(k2)
                if isinstance(v2, list):
                    return [x for x in v2 if isinstance(x, dict)]
    return []


_AICM_TOTAL_KEYS = (
    "total",
    "total_count",
    "totalCount",
    "count",
    "res_l_count",
    "recordsTotal",
)


def _coerce_non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _extract_total(payload: Any) -> int | None:
    """Read a total from the several response envelopes used by AICM."""
    if isinstance(payload, dict):
        for key in _AICM_TOTAL_KEYS:
            total = _coerce_non_negative_int(payload.get(key))
            if total is not None:
                return total
        for key in ("data", "result", "meta", "pagination"):
            nested = payload.get(key)
            total = _extract_total(nested)
            if total is not None:
                return total
    return None


class _ModelSetRows(list[dict]):
    """List-compatible AICM page carrying the remote filtered total."""

    def __init__(self, rows: List[dict], total: int | None = None):
        super().__init__(rows)
        self.total = total


def _search_model_sets(
    sess: requests.Session,
    base: str,
    *,
    like_name: str = "",
    verify: bool,
    limit: int = 20,
    page: int = 1,
) -> List[dict]:
    params = {
        "_page": max(1, int(page)),
        "_limit": max(1, min(int(limit), 100)),
        "_method": "list",
        "_filter_in_use": 0,
        "_filter_engine_id": 0,
        "_like_name": like_name or "",
        "_sort": "",
        "_filter-exact_list-learning_task__label_class_set__id": "",
        "_filter-gte-created_at": "",
        "_filter-lt-created_at": "",
        "_filter_model_source": "",
    }
    r = sess.get(
        f"{base}/v2/model-set/",
        params=params,
        timeout=AICM_HTTP_TIMEOUT,
        verify=verify,
    )
    if r.status_code >= 400:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"AICM 搜索模型集失败 HTTP {r.status_code}: {r.text[:300]}",
        )
    try:
        body = r.json()
    except ValueError:
        body = {}
    data = body.get("data") if isinstance(body, dict) else body
    rows = _extract_rows(data)
    return _ModelSetRows(rows, _extract_total(body))


def _model_set_row_id(row: dict) -> int:
    rid = row.get("id") or row.get("_id") or 0
    try:
        return int(rid)
    except (TypeError, ValueError):
        return 0


def _pick_newest_model_set(rows: List[dict]) -> Optional[dict]:
    if not rows:
        return None

    def _sort_key(row: dict) -> Tuple[int, str]:
        created = str(row.get("created_at") or row.get("create_time") or "")
        return _model_set_row_id(row), created

    return max(rows, key=_sort_key)


def _sort_model_sets_by_id_desc(rows: List[dict]) -> List[dict]:
    return sorted(rows, key=lambda r: _model_set_row_id(r), reverse=True)


def _model_set_row_name(row: dict) -> str:
    return str(row.get("name") or row.get("model_name") or "").strip()


class _RuntimeTemplateCandidates(list[AicmRuntimeTemplateCandidateResponse]):
    """List-compatible candidate page carrying the remote total."""

    total: int


def list_runtime_template_candidates(
    *,
    environment_id: str,
    q: str = "",
    page: int = 1,
    size: int = 50,
) -> List[AicmRuntimeTemplateCandidateResponse]:
    env_id = str(environment_id or "").strip()
    if not env_id or not find_environment_record(env_id):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"发布环境不存在：{env_id or '未选择'}",
        )
    cfg = load_aicm_publish_config(environment_id=env_id)
    if not config_is_complete(cfg):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=_config_incomplete_message(cfg),
        )

    session = _aicm_session(cfg)
    try:
        rows = _search_model_sets(
            session,
            _normalize_base_url(cfg.get("base_url") or ""),
            like_name=str(q or "").strip(),
            verify=bool(cfg.get("verify_ssl")),
            limit=size,
            page=page,
        )
    finally:
        session.close()

    remote_total = getattr(rows, "total", None)
    candidates: List[AicmRuntimeTemplateCandidateResponse] = []
    for row in rows:
        model_set_id = _model_set_row_id(row)
        if model_set_id <= 0:
            continue
        candidates.append(
            AicmRuntimeTemplateCandidateResponse(
                model_set_id=model_set_id,
                name=_model_set_row_name(row),
                model_alg_type=str(
                    row.get("model_alg_type")
                    or row.get("algorithm_type")
                    or row.get("alg_type")
                    or row.get("model_type")
                    or ""
                ).strip(),
                status=str(
                    row.get("status")
                    or row.get("state")
                    or row.get("use_status")
                    or ""
                ).strip(),
                created_at=(
                    str(row.get("created_at") or row.get("create_time") or "").strip()
                    or None
                ),
            )
        )
    # The remote endpoint returns the filtered total separately from the page
    # rows. Preserve that value for the router; old test doubles and older
    # AICM deployments may omit it, in which case the page length is the only
    # truthful fallback available.
    candidates_with_total = _RuntimeTemplateCandidates(candidates)
    candidates_with_total.total = (
        int(remote_total)
        if remote_total is not None
        else len(candidates_with_total)
    )
    return candidates_with_total


def _model_name_matches_preset(model_name: str, preset_name: str) -> bool:
    """
    判断 AICM 模型集名称是否属于该拉框预设。
    除完整包含预设名外，容忍 rename 笔误（如 占道经营 vs 占到经营：首字 + 末 2 字一致）。
    """
    mn = (model_name or "").strip()
    pn = (preset_name or "").strip()
    if not mn or not pn:
        return False
    if pn in mn:
        return True
    if len(pn) >= 3 and mn.startswith(pn[0]) and pn[-2:] in mn:
        return True
    return False


def _merge_model_set_rows(*groups: List[dict]) -> List[dict]:
    seen: set[int] = set()
    merged: List[dict] = []
    for rows in groups:
        for row in rows or []:
            rid = _model_set_row_id(row)
            if rid <= 0 or rid in seen:
                continue
            seen.add(rid)
            merged.append(row)
    return _sort_model_sets_by_id_desc(merged)


def _collect_aicm_template_candidates(
    sess: requests.Session,
    base: str,
    preset_name: str,
    *,
    verify: bool,
) -> List[dict]:
    """
    合并多种 AICM 列表策略并按 id 降序去重。
    避免仅 _like_name=预设全名 时漏掉 rename 笔误（如 id=522 占到经营 vs 占道经营）。
    """
    pn = preset_name.strip()
    groups: List[List[dict]] = [
        _search_model_sets(sess, base, like_name=pn, verify=verify, limit=100),
    ]
    if len(pn) >= 2:
        groups.append(
            _search_model_sets(sess, base, like_name=pn[:2], verify=verify, limit=100)
        )
    if len(pn) >= 1:
        groups.append(
            _search_model_sets(sess, base, like_name=pn[:1], verify=verify, limit=100)
        )
    recent = _search_model_sets(sess, base, like_name="", verify=verify, limit=100)
    groups.append(
        [
            row
            for row in recent
            if _model_name_matches_preset(_model_set_row_name(row), pn)
        ]
    )
    return _merge_model_set_rows(*groups)


def _video_json_from_zip(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith("video.json"):
                raw = json.loads(zf.read(name).decode("utf-8", errors="replace"))
                return raw if isinstance(raw, dict) else {}
    return {}


def _template_zip_is_yolov11_format(zip_path: Path) -> bool:
    """
    边端与垃圾暴露一致的新包：yoloV11 四文件结构，无 Config416.ini / yolov4 旧模板。
    """
    if not zip_path.is_file():
        return False
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if any(n.endswith(".ini") for n in zf.namelist()):
                return False
            for name in zf.namelist():
                if not name.endswith("video.json"):
                    continue
                raw = json.loads(zf.read(name).decode("utf-8", errors="replace"))
                rknn = raw.get("rknn") if isinstance(raw, dict) else None
                if not isinstance(rknn, dict):
                    return False
                alg = str(rknn.get("ModelAlgType") or "").strip().lower()
                return alg == "yolov11"
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError):
        return False
    return False


def _assert_yolov11_publish_package(pkg_root: Path) -> None:
    """发布产物须为 yoloV11 新包，避免 yolov4 混合包下发边端失败。"""
    out_rknn = _find_output_rknn(pkg_root)
    if any(out_rknn.glob("*.ini")) or any(pkg_root.rglob("*.ini")):
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=(
                "发布包含 Config416.ini 等 yolov4 旧模板文件，边端需 yoloV11 新包（与垃圾暴露一致）。"
                "请在 AICM 选用 yoloV11 模板重新发布，勿使用 #422 等 yolov4 模板。"
            ),
        )
    video_json = out_rknn / "video.json"
    if not video_json.is_file():
        video_json = pkg_root / "video.json"
    try:
        data = json.loads(video_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"video.json 解析失败: {exc}",
        ) from exc
    rknn = data.get("rknn") if isinstance(data, dict) else None
    alg = str(rknn.get("ModelAlgType") or "").strip() if isinstance(rknn, dict) else ""
    if alg != "yoloV11":
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=(
                f"发布包 ModelAlgType={alg!r}，边端需 yoloV11 新包。"
                "当前模板可能为 yolov4 旧版（如 model_set #422），请改用 yoloV11 模板。"
            ),
        )


def _fetch_latest_model_set(
    sess: requests.Session,
    base: str,
    *,
    verify: bool,
) -> dict:
    """无 _like_name 条件列表，取 id 最大的一条（即最新上传）。"""
    rows = _search_model_sets(sess, base, like_name="", verify=verify, limit=50)
    picked = _pick_newest_model_set(rows)
    if not picked:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="AICM 模型集列表为空，无法定位刚上传的模型",
        )
    return picked


def _wait_for_new_numeric_model_set(
    sess: requests.Session,
    base: str,
    *,
    before_id: int,
    verify: bool,
    attempts: int = AICM_UPLOAD_MODEL_LOOKUP_ATTEMPTS,
    delay_seconds: float = AICM_UPLOAD_MODEL_LOOKUP_DELAY_SECONDS,
) -> dict:
    """Locate the newest AICM-generated numeric model name after an upload."""
    attempt_cap = max(1, int(attempts))
    latest_seen: Optional[dict] = None
    for attempt in range(1, attempt_cap + 1):
        rows = _search_model_sets(
            sess,
            base,
            like_name="",
            verify=verify,
            limit=100,
        )
        latest_seen = _pick_newest_model_set(rows)
        candidates = [
            row
            for row in rows
            if _model_set_row_id(row) > int(before_id)
            and _model_set_row_name(row).isdigit()
        ]
        picked = _pick_newest_model_set(candidates)
        if picked is not None:
            return picked
        if attempt < attempt_cap and delay_seconds > 0:
            time.sleep(float(delay_seconds))

    latest_id = _model_set_row_id(latest_seen or {})
    latest_name = _model_set_row_name(latest_seen or {})
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_502_BAD_GATEWAY,
        message=(
            "AICM 上传成功，但未查询到上传后新增的数字名称模型集"
            f"（上传前最新 id={int(before_id)}，当前最新 id={latest_id} "
            f"name={latest_name!r}）；已停止 edit_name"
        ),
    )


def _build_upload_correlation_name(export_id: int) -> str:
    """Build a unique temporary AICM name used only to identify this upload."""
    # AICM limits ``name`` to 20 characters. The short export suffix keeps
    # the name useful in logs while the UUID portion prevents concurrent
    # uploads from being mistaken for one another.
    return f"lu{int(export_id) % 100:02d}_{uuid.uuid4().hex[:14]}"


def _fetch_model_set_ids_by_exact_name(
    sess: requests.Session,
    base: str,
    *,
    name: str,
    verify: bool,
) -> set[int]:
    expected = str(name or "").strip()
    if not expected:
        return set()
    rows = _search_model_sets(
        sess,
        base,
        like_name=expected,
        verify=verify,
        limit=20,
    )
    return {
        model_set_id
        for row in rows
        if _model_set_row_name(row) == expected
        and (model_set_id := _model_set_row_id(row)) > 0
    }


def _wait_for_model_set_ids_by_exact_name(
    sess: requests.Session,
    base: str,
    *,
    name: str,
    verify: bool,
    expected_id: Optional[int] = None,
    attempts: int = AICM_MODEL_SET_LOOKUP_ATTEMPTS,
    delay_seconds: float = AICM_MODEL_SET_LOOKUP_DELAY_SECONDS,
) -> set[int]:
    """Retry exact-name lookup to tolerate AICM list-index propagation delay."""
    attempt_cap = max(1, int(attempts))
    last_ids: set[int] = set()
    for attempt in range(1, attempt_cap + 1):
        last_ids = _fetch_model_set_ids_by_exact_name(
            sess,
            base,
            name=name,
            verify=verify,
        )
        if last_ids:
            # A different exact-name id is a definitive ownership mismatch, not lag.
            if expected_id is None or expected_id in last_ids:
                return last_ids
            return last_ids
        if attempt < attempt_cap and delay_seconds > 0:
            time.sleep(float(delay_seconds))
    return last_ids


def _get_download_url(
    sess: requests.Session,
    base: str,
    model_set_id: int,
    *,
    verify: bool,
) -> str:
    r = sess.get(
        f"{base}/v2/model-set/",
        params={"_method": "get_download_url", "_id": model_set_id},
        timeout=AICM_HTTP_TIMEOUT,
        verify=verify,
    )
    if r.status_code >= 400:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"获取下载地址失败 HTTP {r.status_code}: {r.text[:300]}",
        )
    try:
        body = r.json()
    except ValueError:
        body = {}
    if isinstance(body, dict) and body.get("error_code") not in (None, 0, "0"):
        error_info = str(body.get("error_info") or "").strip()
        detail = f"：{error_info}" if error_info else ""
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=(
                f"AICM 模型集 id={int(model_set_id)} 不存在或最终包尚未生成"
                f"（error_code={body.get('error_code')}）{detail}"
            ),
        )
    data = body.get("data") if isinstance(body, dict) else {}
    if isinstance(data, str) and data.startswith("http"):
        return data
    if isinstance(data, dict):
        for key in ("url", "download_url", "file_url", "link"):
            u = data.get(key)
            if isinstance(u, str) and u.startswith("http"):
                return u
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_502_BAD_GATEWAY,
        message=f"AICM 模型集 id={int(model_set_id)} 未返回有效的最终包下载 URL",
    )


def _ensure_template_zip(
    sess: requests.Session,
    base: str,
    preset_name: str,
    *,
    verify: bool,
    steps: List[str],
    progress: Optional[PublishJobTracker] = None,
    use_cache: bool = False,
) -> Tuple[Path, int]:
    """返回 (zip_path, model_set_id)。仅 use_cache=True 且 meta 与当前 model_set_id 一致时跳过下载。"""
    if not preset_name.strip():
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="无法确定拉框预设名称，请在任务中应用拉框预设或填写训练分类",
        )
    cache_dir = _templates_root() / _safe_dir_name(preset_name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta_path = cache_dir / "template_meta.json"
    zip_path = cache_dir / "template.zip"

    if progress is not None:
        progress.start("search", f"按名称搜索：{preset_name}")
    candidates = _collect_aicm_template_candidates(
        sess, base, preset_name, verify=verify
    )
    if not candidates:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"AICM 未找到名称包含「{preset_name}」的模型集模板",
        )
    preview_ids = [_model_set_row_id(c) for c in candidates[:12]]
    _log_step(
        steps,
        f"AICM 搜索「{preset_name}」合并候选 {len(candidates)} 条，"
        f"id 降序: {preview_ids}{'…' if len(candidates) > 12 else ''}；"
        f"将按序选用 yoloV11 新模板",
        progress,
    )

    if use_cache:
        if progress is not None:
            progress.start("template", "检查本地模板缓存")
    else:
        _log_step(
            steps,
            "未勾选保留模板：将重新从 AICM 下载模板 zip（忽略本地缓存）",
            progress,
        )

    skipped_legacy: List[int] = []
    for picked in candidates:
        model_set_id = _model_set_row_id(picked)
        if model_set_id <= 0:
            continue

        if use_cache and meta_path.is_file() and zip_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if int(meta.get("model_set_id") or 0) == model_set_id:
                    if _template_zip_is_yolov11_format(zip_path):
                        _log_step(
                            steps,
                            f"已勾选保留模板：使用本地 yoloV11 缓存（model_set_id={model_set_id}）",
                            progress,
                        )
                        if progress is not None:
                            progress.ok("search", f"model_set_id={model_set_id}")
                            progress.ok("template", f"使用缓存 {zip_path.name}")
                        return zip_path, model_set_id
                    skipped_legacy.append(model_set_id)
                    _log_step(
                        steps,
                        f"本地缓存 model_set_id={model_set_id} 为 yolov4 旧模板，忽略并尝试其它候选",
                        progress,
                    )
            except (json.JSONDecodeError, OSError, ValueError):
                pass

        dl_url = _get_download_url(sess, base, model_set_id, verify=verify)
        if progress is not None:
            progress.start("template", "从 AICM 下载模板 zip（可能较慢）")
        row_name = _model_set_row_name(picked)
        _log_step(
            steps,
            f"下载 AICM 模板 zip（model_set_id={model_set_id} name={row_name!r}）…",
            progress,
        )
        r = sess.get(dl_url, timeout=AICM_HTTP_TIMEOUT, verify=verify, stream=True)
        if r.status_code >= 400:
            raise LabelUException(
                code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                status_code=status.HTTP_502_BAD_GATEWAY,
                message=f"下载模板失败 HTTP {r.status_code}",
            )
        with zip_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)

        if not _template_zip_is_yolov11_format(zip_path):
            skipped_legacy.append(model_set_id)
            _log_step(
                steps,
                f"跳过 yolov4 旧模板 model_set_id={model_set_id}（含 Config416.ini 或非 yoloV11），尝试下一候选",
                progress,
            )
            continue

        meta_path.write_text(
            json.dumps(
                {
                    "model_set_id": model_set_id,
                    "like_name": preset_name,
                    "zip_path": str(zip_path),
                    "downloaded_at": datetime.now().isoformat(timespec="seconds"),
                    "template_format": "yoloV11",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _log_step(
            steps,
            f"已选用 yoloV11 模板 model_set_id={model_set_id}，缓存至 {zip_path}",
            progress,
        )
        if progress is not None:
            progress.ok("search", f"model_set_id={model_set_id}")
            progress.ok("template", str(zip_path))
        return zip_path, model_set_id

    legacy_hint = (
        f"（已跳过 id: {', '.join(map(str, skipped_legacy[:8]))}）"
        if skipped_legacy
        else ""
    )
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_404_NOT_FOUND,
        message=(
            f"AICM 未找到「{preset_name}」的 yoloV11 新模板{legacy_hint}。"
            "边端需与垃圾暴露相同的四文件 yoloV11 包；请勿使用 #422 等 yolov4 旧模板。"
        ),
    )


def _bmodel_precision_from_filename(filename: str) -> str:
    fn = (filename or "").strip().lower()
    if "_int8_" in fn or fn.endswith("_int8_1b.bmodel"):
        return "int8"
    if "_fp16_" in fn or fn.endswith("_fp16_1b.bmodel"):
        return "fp16"
    if "_fp32_" in fn or fn.endswith("_fp32_1b.bmodel"):
        return "fp32"
    return "unknown"


def _bmodel_option_label(precision: str) -> str:
    labels = {
        "int8": "INT8 量化",
        "fp16": "FP16 半精度",
        "fp32": "FP32 全精度",
    }
    return labels.get(precision, precision or "bmodel")


def _build_bmodel_options(bm_names: List[str]) -> List[AicmPublishBmodelOption]:
    """将导出目录中的 bmodel 整理为可发布的精度选项（INT8 优先）。"""
    order = {"int8": 0, "fp16": 1, "fp32": 2, "unknown": 9}
    options: List[AicmPublishBmodelOption] = []
    for rel in bm_names:
        rel = (rel or "").strip().replace("\\", "/")
        if not rel:
            continue
        fn = Path(rel).name
        prec = _bmodel_precision_from_filename(fn)
        options.append(
            AicmPublishBmodelOption(
                rel_path=rel,
                filename=fn,
                precision=prec,
                label=_bmodel_option_label(prec),
                recommended=(prec == "int8"),
                eval_gate_required=False,
                export_storage_path=f"models/{rel}",
            )
        )
    options.sort(key=lambda o: (order.get(o.precision, 9), o.rel_path))
    return options


def _publish_bmodel_options_for_job(
    job: Any, bm_names: List[str]
) -> List[AicmPublishBmodelOption]:
    options = _build_bmodel_options(bm_names)
    if str(getattr(job, "job_type", "") or "").strip() == "plate_recognition":
        return [
            option
            for option in options
            if option.filename == "plate_recognition_fp16_1b.bmodel"
        ]
    return options


def _default_bmodel_rel_path(options: List[AicmPublishBmodelOption]) -> Optional[str]:
    for o in options:
        if o.recommended:
            return o.rel_path
    return options[0].rel_path if options else None


def _compile_contract_block_reason(
    export_row: Any, edge_validation: Any = None
) -> Optional[str]:
    """读取导出时保存的 BM1688 证据门禁；旧制品没有该字段则走历史兼容。"""
    try:
        manifest = json.loads(getattr(export_row, "artifact_manifest_json", None) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    config = ((manifest.get("compiler") or {}).get("config") or {})
    if str(config.get("compile_contract_status") or "").strip() != "阻断":
        return None
    reasons = config.get("compile_contract_reasons") or []
    if isinstance(reasons, list):
        normalized_reasons = [str(item) for item in reasons if str(item).strip()]
    else:
        normalized_reasons = [str(reasons).strip()] if str(reasons).strip() else []
    # 编译完成时固定评估集尚未产生是预期状态；发布前必须已经有一条
    # COMPLETED 边端验证，否则继续阻断。其他编译证据缺失永远阻断。
    edge_status = str(getattr(edge_validation, "status", "") or "").upper()
    edge_result_status = str(
        getattr(edge_validation, "edge_result_status", "") or ""
    ).strip().lower()
    if edge_status == "COMPLETED" and edge_result_status not in {
        "",
        "unknown",
        "result_pending",
    }:
        normalized_reasons = [
            reason for reason in normalized_reasons if reason != "fixed_eval_missing"
        ]
    if not normalized_reasons:
        return None
    reason_text = "、".join(normalized_reasons[:5])
    return "BM1688 编译证据未满足发布条件" + (f"：{reason_text}" if reason_text else "")


def _prepare_publish_bmodel_options(
    job: Any,
    options: List[AicmPublishBmodelOption],
    *,
    export_id: int,
    rect_preset_slug: Optional[str] = None,
    quality_policy_snapshot: Optional[dict[str, Any]] = None,
    evidence_comparison: Optional[dict[str, Any]] = None,
    allow_quality_risk: bool = False,
) -> List[AicmPublishBmodelOption]:
    """Attach validation evidence and mark options selectable for this trigger."""
    if str(getattr(job, "job_type", "") or "").strip() == "plate_recognition":
        for option in options:
            option.validation_status = "not_applicable"
            option.publishable = True
            option.blocked_reason = None
        return options

    for option in options:
        state, comparison, _ = _bmodel_eval_gate_result(
            export_id,
            precision=option.precision,
            rect_preset_slug=rect_preset_slug,
            quality_policy_snapshot=quality_policy_snapshot,
            evidence_comparison=evidence_comparison,
        )
        option.validation_status = state
        # The review band is warning-only after completed edge evidence is
        # present. Missing evidence and an excessive drop remain blockers.
        option.publishable = state in {"pass", "review"}
        option.recommended = False
        if isinstance(comparison, dict):
            for field_name, target_name in (
                ("pt_map50", "pt_map50"),
                ("bmodel_map50", "bmodel_map50"),
                ("drop_map50", "drop_map50"),
            ):
                raw_value = comparison.get(field_name)
                try:
                    parsed_value = float(raw_value) if raw_value is not None else None
                except (TypeError, ValueError):
                    parsed_value = None
                if parsed_value is not None and math.isfinite(parsed_value):
                    setattr(option, target_name, parsed_value)
        quality_warning = (
            None
            if state == "pass"
            else _bmodel_eval_gate_notice(
                export_id,
                precision=option.precision,
                rect_preset_slug=rect_preset_slug,
                quality_policy_snapshot=quality_policy_snapshot,
                evidence_comparison=evidence_comparison,
            )
        )
        option.quality_warning = quality_warning
        option.blocked_reason = (
            quality_warning if state in {"missing", "exceeded"} else None
        )

    publishable_options = [option for option in options if option.publishable]
    preferred = next(
        (
            option
            for option in publishable_options
            if option.validation_status == "pass"
        ),
        None,
    )
    if preferred is None:
        preferred = next(
            (
                option
                for option in publishable_options
                if option.validation_status == "not_applicable"
            ),
            None,
        )
    if preferred is None:
        preferred = next(iter(publishable_options), None)
    if preferred is not None:
        preferred.recommended = True
    return options


def _pick_bmodel_rel_by_precision(bm_names: List[str], precision: str) -> Optional[str]:
    prec = (precision or "").strip().lower()
    if not prec:
        return None
    token = f"_{prec}_"
    matches = [
        name
        for name in bm_names
        if token in Path(name).name.lower()
        or Path(name).name.lower().endswith(f"_{prec}_1b.bmodel")
    ]
    if prec == "fp16":
        for preferred_name in (
            "plate_recognition_fp16_1b.bmodel",
            "plate_detection_fp16_1b.bmodel",
        ):
            for name in matches:
                if Path(name).name == preferred_name:
                    return name
    return matches[0] if matches else None


def _validate_bmodel_rel_path(rel_path: str) -> str:
    normalized = (rel_path or "").strip().replace("\\", "/")
    candidate = Path(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or candidate.is_absolute()
        or ".." in candidate.parts
        or candidate.suffix.lower() != ".bmodel"
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"bmodel 路径必须是 models/ 下的安全相对路径：{rel_path}",
        )
    return candidate.as_posix()


def _normalize_bmodel_selection(
    bm_names: List[str],
    *,
    precision: Optional[str] = None,
    rel_path: Optional[str] = None,
) -> str:
    """
    解析要读取的编译产物（相对 export/models/）。
    打入 AICM zip 时由 _build_publish_package 拷贝到 output_rknn/ 并保留编译产物文件名。
    """
    names = [_validate_bmodel_rel_path(n) for n in bm_names if (n or "").strip()]
    if not names:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="导出记录中未找到 bmodel，请先完成 BM1688 编译",
        )
    by_prec = _pick_bmodel_rel_by_precision(names, precision or "")
    if by_prec:
        return by_prec
    req = (rel_path or "").strip().replace("\\", "/")
    if req:
        if req in names:
            return req
        req_name = Path(req).name
        for n in names:
            if n == req or n.endswith("/" + req) or Path(n).name == req_name:
                return n
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"所选 bmodel 不存在或尚未编译完成：{rel_path}",
        )
    for n in names:
        if "_int8_1b.bmodel" in n:
            return n
    return names[0]


def _comparison_evidence_for_precision(
    evidence_comparison: Optional[dict[str, Any]], precision: str
) -> Optional[dict[str, Any]]:
    """Select the latest completed edge comparison for one model precision."""
    if not isinstance(evidence_comparison, dict) or not evidence_comparison:
        return None
    selected_precision = str(precision or "").strip().lower()
    by_precision = evidence_comparison.get("by_precision")
    if isinstance(by_precision, dict):
        candidate = by_precision.get(selected_precision)
        if isinstance(candidate, dict) and candidate:
            selected = dict(candidate)
            selected.setdefault("precision", selected_precision)
            return selected
    candidate_precision = str(evidence_comparison.get("precision") or "").strip().lower()
    if candidate_precision == selected_precision:
        return dict(evidence_comparison)
    # Legacy top-level board comparisons represented INT8 before precision was persisted.
    if (
        selected_precision == "int8"
        and not candidate_precision
        and evidence_comparison.get("pt_map50") is not None
        and evidence_comparison.get("bmodel_map50") is not None
    ):
        selected = dict(evidence_comparison)
        selected["precision"] = "int8"
        return selected
    return None


def _bmodel_eval_gate_result(
    export_id: int,
    *,
    precision: str = "int8",
    rect_preset_slug: Optional[str] = None,
    quality_policy_snapshot: Optional[dict[str, Any]] = None,
    evidence_comparison: Optional[dict[str, Any]] = None,
) -> tuple[str, Optional[dict[str, Any]], float]:
    selected_precision = (precision or "").strip().lower() or "int8"
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        discover_manual_bmodel_results_dir,
        load_server_sop_eval_summary,
    )
    from labelu.internal.application.service.yolo_quality_assessment import (
        _evaluate_pt_bmodel_comparison_for_policy,
        _policy_max_bmodel_drop,
        _policy_for_preset,
    )

    root = (
        Path(settings.MEDIA_ROOT) / settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id)
    )
    sop = load_server_sop_eval_summary(root)
    policy = (
        (quality_policy_snapshot or {}).get("edge_usability")
        or _policy_for_preset(rect_preset_slug).get("edge_usability")
        or {}
    )
    max_drop = _policy_max_bmodel_drop(policy)
    comparison = _comparison_evidence_for_precision(
        evidence_comparison, selected_precision
    )
    if comparison is None and selected_precision == "int8":
        comparison = (sop or {}).get("comparison_pt_bmodel") or None
    elif comparison is None:
        pt_metrics = ((sop or {}).get("pt") or {}).get("metrics") or {}
        bmodel_metrics = None
        variant_block = ((sop or {}).get("bmodels") or {}).get(selected_precision)
        if isinstance(variant_block, dict):
            bmodel_metrics = variant_block.get("metrics")
        if not isinstance(bmodel_metrics, dict):
            variant_paths: List[Path] = [
                root
                / "server_eval"
                / "results_bmodel"
                / "variants"
                / selected_precision
                / "eval_summary.json"
            ]
            manual_root = discover_manual_bmodel_results_dir(root)
            if manual_root is not None:
                variant_paths.insert(
                    0,
                    manual_root
                    / "variants"
                    / selected_precision
                    / "eval_summary.json",
                )
            for variant_path in variant_paths:
                if not variant_path.is_file():
                    continue
                try:
                    loaded = json.loads(variant_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(loaded, dict):
                    bmodel_metrics = loaded
                    break
            if not isinstance(bmodel_metrics, dict) and manual_root is not None:
                # Specialized/manual validators write the selected model result
                # directly under results_bmodel rather than variants/<precision>.
                # Only consume it when its identity proves it is the requested
                # precision; a legacy direct result may represent INT8.
                direct_path = manual_root / "eval_summary.json"
                if direct_path.is_file():
                    try:
                        loaded = json.loads(direct_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        loaded = None
                    if isinstance(loaded, dict):
                        model_tag = str(
                            loaded.get("model_tag") or loaded.get("precision") or ""
                        ).strip().lower()
                        model_filename = str(
                            loaded.get("model_filename") or ""
                        ).strip()
                        filename_precision = _bmodel_precision_from_filename(
                            model_filename
                        )
                        identity_matches = selected_precision in {
                            model_tag,
                            filename_precision,
                        }
                        if not identity_matches and not model_tag and not model_filename:
                            bmodel_names = bm_export_service._list_bmodel_names(
                                export_id
                            )
                            available_precisions = {
                                _bmodel_precision_from_filename(Path(name).name)
                                for name in bmodel_names
                            }
                            identity_matches = (
                                available_precisions == {selected_precision}
                                and selected_precision == "fp16"
                                and any(
                                    "plate_detection" in Path(name).name.lower()
                                    for name in bmodel_names
                                )
                            )
                        if identity_matches:
                            bmodel_metrics = loaded
        try:
            pt_map50 = float(pt_metrics.get("map50"))
            bmodel_map50 = float((bmodel_metrics or {}).get("map50"))
        except (TypeError, ValueError):
            pt_map50 = bmodel_map50 = float("nan")
        if math.isfinite(pt_map50) and math.isfinite(bmodel_map50):
            comparison = {
                "pt_map50": pt_map50,
                "bmodel_map50": bmodel_map50,
                "precision": selected_precision,
                "detail": (
                    f"{selected_precision.upper()} BModel 与 PT 使用同批数据完成 mAP50 比较"
                ),
            }
    state, effective, _ = _evaluate_pt_bmodel_comparison_for_policy(
        comparison,
        max_drop=max_drop,
    )
    if effective is not None:
        effective["precision"] = selected_precision
        if state == "pass":
            effective["detail"] = (
                f"{selected_precision.upper()} BModel 相对 PT 的 mAP50 掉点 "
                f"{float(effective.get('drop_map50') or 0.0):.4f}，满足场景上限 {max_drop:.4f}"
            )
        elif state == "exceeded":
            effective["detail"] = (
                f"{selected_precision.upper()} BModel 相对 PT 的 mAP50 掉点 "
                f"{float(effective.get('drop_map50') or 0.0):.4f} 超过当前场景上限 {max_drop:.4f}"
            )
    return state, effective or comparison, max_drop


def _bmodel_eval_gate_notice(
    export_id: int,
    *,
    precision: str = "int8",
    rect_preset_slug: Optional[str] = None,
    quality_policy_snapshot: Optional[dict[str, Any]] = None,
    evidence_comparison: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """Return a review/block notice for the effective scene conversion gate."""
    state, comparison, max_drop = _bmodel_eval_gate_result(
        export_id,
        precision=precision,
        rect_preset_slug=rect_preset_slug,
        quality_policy_snapshot=quality_policy_snapshot,
        evidence_comparison=evidence_comparison,
    )
    precision_label = (precision or "int8").strip().upper()
    detail = str((comparison or {}).get("detail") or "").strip()
    if state == "missing":
        return detail or f"缺少 PT 与 {precision_label} BModel 同口径精度比较"
    if state == "pass":
        return None
    if state == "exceeded":
        return detail or f"PT 到 {precision_label} BModel 的 mAP50 掉点超过场景上限 {max_drop:.4f}"
    return detail or f"{precision_label} BModel 转换保真度需要人工复核"


def _assert_bmodel_eval_gate(
    export_id: int,
    *,
    precision: str = "int8",
    rect_preset_slug: Optional[str] = None,
    quality_policy_snapshot: Optional[dict[str, Any]] = None,
    evidence_comparison: Optional[dict[str, Any]] = None,
    allow_quality_risk: bool = False,
) -> None:
    state, comparison, max_drop = _bmodel_eval_gate_result(
        export_id,
        precision=precision,
        rect_preset_slug=rect_preset_slug,
        quality_policy_snapshot=quality_policy_snapshot,
        evidence_comparison=evidence_comparison,
    )
    selected_precision = (precision or "int8").strip().lower() or "int8"
    if state == "pass":
        return
    # A completed comparison in the review band is publishable by policy. The
    # comparison remains visible as a warning, but it is not a second manual
    # review workflow. Missing evidence and excessive accuracy loss remain
    # hard blockers.
    if state == "review":
        return
    detail = str((comparison or {}).get("detail") or "").strip()
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_400_BAD_REQUEST,
        message=detail
        or (
            f"{selected_precision.upper()} BModel 缺少有效的同口径板端验证结果，禁止发布"
            if state in {"missing", "review"}
            else f"PT 到 {selected_precision.upper()} BModel 的 mAP50 掉点超过场景上限 {max_drop:.4f}，禁止发布"
        ),
    )


def _resolve_bmodel(
    export_id: int,
    *,
    bmodel_precision: Optional[str] = None,
    bmodel_rel_path: Optional[str] = None,
) -> Tuple[Path, str]:
    """返回 (导出目录内 bmodel 绝对路径, models/ 下相对路径)。"""
    names = bm_export_service._list_bmodel_names(export_id)
    pick = _normalize_bmodel_selection(
        names,
        precision=bmodel_precision,
        rel_path=bmodel_rel_path,
    )
    root = bm_export_service._export_dir_for_row(export_id)
    path = root / "models" / pick
    if not path.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"bmodel 文件不存在: {path}",
        )
    return path.resolve(), pick


def _find_package_root(extract_dir: Path) -> Path:
    if (extract_dir / "running_info.yml").is_file():
        return extract_dir
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "running_info.yml").is_file():
            return child
    for child in extract_dir.iterdir():
        if child.is_dir():
            found = _find_package_root(child)
            if (found / "running_info.yml").is_file():
                return found
    return extract_dir


def _find_output_rknn(pkg_root: Path) -> Path:
    direct = pkg_root / "output_rknn"
    if direct.is_dir():
        return direct
    for p in pkg_root.rglob("output_rknn"):
        if p.is_dir():
            return p
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="模板包中未找到 output_rknn 目录",
    )


def _read_labels_first_line(labels_path: Path) -> str:
    if not labels_path.is_file():
        return LABELS_FIRST_LINE_PLACEHOLDER
    lines = labels_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if lines and lines[0].strip():
        first = lines[0].rstrip("\r").strip()
        if first and _is_placeholder_first_line(first):
            return first
    return LABELS_FIRST_LINE_PLACEHOLDER


def _is_placeholder_first_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return True
    if set(s) <= {"?"}:
        return True
    if len(s) <= 2 and s.isascii():
        return True
    return False


def _resolve_labels_first_line(*candidates: Path) -> str:
    """发布包 labels.txt 首行：沿用模板占位符（?????），文档要求勿覆盖。"""
    for p in candidates:
        if not p.is_file():
            continue
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines:
            continue
        first = lines[0].rstrip("\r").strip()
        if first and _is_placeholder_first_line(first):
            return first
    return LABELS_FIRST_LINE_PLACEHOLDER


def _template_model_basename(out_rknn: Path) -> Optional[str]:
    """沿用模板 video.json 中的 ModelPath 文件名（ASCII），避免中文路径导致 AICM 解析异常。"""
    video_json = out_rknn / "video.json"
    if not video_json.is_file():
        return None
    try:
        raw = json.loads(video_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    rknn = raw.get("rknn") if isinstance(raw, dict) else None
    if not isinstance(rknn, dict):
        return None
    mp = str(rknn.get("ModelPath") or "").strip().replace("\\", "/")
    if mp.startswith("./"):
        mp = mp[2:]
    name = Path(mp).name
    if name.endswith(".bmodel") and re.match(r"^[\w.\-]+\.bmodel$", name):
        return name
    return None


def _ascii_bmodel_filename(*, export_id: int, template_name: Optional[str]) -> str:
    if template_name:
        return template_name
    return f"model_{export_id}_int8.bmodel"


def _published_bmodel_filename(picked_rel: str) -> str:
    """
    发布包 output_rknn 内 bmodel 文件名：与 BM1688 编译产物一致（如 yolov11n_int8_1b.bmodel）。
    不使用 AICM 模板 zip 内旧的 ljbl_* 等占位文件名。
    """
    rel = (picked_rel or "").strip().replace("\\", "/")
    name = Path(rel).name
    if not name.endswith(".bmodel"):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"无效的 bmodel 路径: {picked_rel}",
        )
    if not re.match(r"^[\w.\-]+\.bmodel$", name):
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"bmodel 文件名须为 ASCII（当前: {name}）",
        )
    return name


def _snapshot_package_files(pkg_root: Path) -> frozenset[str]:
    """包根目录下所有文件的相对路径（posix），用于校验发布前后目录结构不变。"""
    return frozenset(
        p.relative_to(pkg_root).as_posix() for p in pkg_root.rglob("*") if p.is_file()
    )


def _is_output_rknn_bmodel(rel_path: str) -> bool:
    p = (rel_path or "").replace("\\", "/")
    return p.endswith(".bmodel") and (p.startswith("output_rknn/") or "/" not in p)


def _assert_package_files_unchanged(
    before: frozenset[str], after: frozenset[str]
) -> None:
    if before == after:
        return
    added = after - before
    removed = before - after
    bmodel_added = {p for p in added if _is_output_rknn_bmodel(p)}
    bmodel_removed = {p for p in removed if _is_output_rknn_bmodel(p)}
    if (
        len(bmodel_added) == 1
        and len(bmodel_removed) == 1
        and added == bmodel_added
        and removed == bmodel_removed
    ):
        return
    added_sorted = sorted(added)
    removed_sorted = sorted(removed)
    parts: List[str] = [
        "发布包目录结构相对模板发生变化（仅允许修改文件内容或替换 bmodel 文件名）"
    ]
    if added_sorted:
        parts.append(
            f"新增: {', '.join(added_sorted[:8])}{'…' if len(added_sorted) > 8 else ''}"
        )
    if removed_sorted:
        parts.append(
            f"删除: {', '.join(removed_sorted[:8])}{'…' if len(removed_sorted) > 8 else ''}"
        )
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="；".join(parts),
    )


def _resolve_template_bmodel_dest(out_rknn: Path) -> Path:
    """
    在模板 output_rknn 内定位占位 bmodel 路径（与 video.json ModelPath 一致）。
    """
    name = _template_model_basename(out_rknn)
    if name:
        return out_rknn / name
    bmodels = sorted(out_rknn.glob("*.bmodel"))
    if len(bmodels) == 1:
        return bmodels[0]
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message=(
            "无法确定模板 bmodel 路径：video.json 缺少 ModelPath，"
            f"且 output_rknn 内 bmodel 数量={len(bmodels)}（须为 1 个）"
        ),
    )


def _deploy_publish_bmodel(
    out_rknn: Path,
    bmodel_src: Path,
    picked_rel: str,
    *,
    publish_name_override: Optional[str] = None,
) -> Tuple[Path, Optional[str]]:
    """
    将所选精度的 bmodel 写入 output_rknn，文件名与 BM1688 编译产物一致（如 yolov11n_fp16_1b.bmodel）。
    若与模板占位文件名不同，删除模板旧 bmodel 并返回需写入 video.json 的 ModelPath。
    """
    publish_name = publish_name_override or _published_bmodel_filename(picked_rel)
    dest = out_rknn / publish_name
    template_dest = _resolve_template_bmodel_dest(out_rknn)
    shutil.copy2(bmodel_src, dest)
    model_path_patch: Optional[str] = None
    if dest.name != template_dest.name:
        if template_dest.is_file() and template_dest.resolve() != dest.resolve():
            template_dest.unlink()
        model_path_patch = f"./{publish_name}"
    return dest, model_path_patch


def _json_value_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _replace_rknn_json_field(raw: str, key: str, value: Any) -> str:
    """在 video.json 原文中替换 rknn 内单个字段的值，保持键序与排版不变。"""
    lit = _json_value_literal(value)
    pattern = (
        rf'("{re.escape(key)}"\s*:\s*)'
        r"(?:null|true|false|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|"
        r'"(?:\\.|[^"\\])*")'
    )
    new_raw, n = re.subn(pattern, rf"\g<1>{lit}", raw, count=1)
    if n != 1:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"video.json 中未找到可替换字段: {key}",
        )
    return new_raw


def _running_info_yaml_style(template_text: str) -> str:
    """
    沿用 BM1688/AICM 模板里的 running_info 结构（非照搬参考包业务数据）。
    常见可上传模板为嵌套列表；根级交替 `- slug`/`- 0.9` 为旧写法。
    """
    in_list = False
    for raw in template_text.splitlines():
        line = raw
        stripped = line.strip()
        if stripped == "accuracy_list:":
            in_list = True
            continue
        if not in_list:
            continue
        if line.startswith("  - "):
            return "nested"
        # 根级 `- - slug` 是嵌套项被压扁后的误写，按嵌套结构回写（避免 float 下标错误）
        if stripped.startswith("- - "):
            return "nested"
        if stripped.startswith("- "):
            return "flat"
    return "nested"


def _normalize_running_info_label(item: str) -> str:
    """剥离 YAML 列表项前缀，避免 `- - slug` 被解析成带前导 `-` 的类别名。"""
    s = (item or "").strip()
    while s.startswith("- "):
        s = s[2:].strip()
    return s


def _parse_accuracy_labels_from_running_info(text: str) -> List[str]:
    """
    解析 AICM 模板 running_info.yml 中 accuracy_list 的类别名顺序。
    格式为交替列表：- label / - 0.908 / - label2 / - 0.968 ...
    兼容模板误写的 `- - label`（YAML 会变成嵌套列表，AICM 上传会报 float 不可下标）。
    """
    labels: List[str] = []
    in_list = False
    expect_label = True
    for raw in text.splitlines():
        line = raw.strip()
        if line == "accuracy_list:":
            in_list = True
            expect_label = True
            continue
        if not in_list:
            continue
        if not line.startswith("- "):
            if line and not line.startswith("#"):
                break
            continue
        item = _normalize_running_info_label(line[2:].strip())
        if expect_label:
            if item and not _looks_like_accuracy_number(item):
                labels.append(item)
            expect_label = False
        else:
            expect_label = True
    return labels


def _looks_like_accuracy_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _class_metric_rows_indexed(
    class_metrics_rows: List[Any],
) -> Tuple[Dict[int, Any], Dict[str, Any]]:
    by_index: Dict[int, Any] = {}
    by_label: Dict[str, Any] = {}
    for row in class_metrics_rows or []:
        label = str(getattr(row, "label", "") or "").strip()
        if label:
            by_label[label] = row
        try:
            idx = int(getattr(row, "index", -1))
        except (TypeError, ValueError):
            idx = -1
        if idx >= 0:
            by_index[idx] = row
    return by_index, by_label


def _metric_value_from_class_row(row: Any) -> float:
    """AICM running_info 单类精度：仅用指标验证（测试集）各类别召回率 test_recall。"""
    if row is None:
        return 0.0
    v = getattr(row, "test_recall", None)
    if v is None:
        return 0.0
    try:
        f = float(v)
        if f >= 0:
            return round(f, 3)
    except (TypeError, ValueError):
        pass
    return 0.0


def _metric_value_for_label(
    label: str,
    *,
    by_label: Dict[str, Any],
    by_index: Dict[int, Any],
    classes: List[str],
    index: int,
    lookup_classes: Optional[List[str]] = None,
) -> float:
    lookup = lookup_classes if lookup_classes is not None else classes
    row = by_index.get(index)
    if row is None:
        row = by_label.get(label)
    if row is None and 0 <= index < len(lookup):
        row = by_label.get(lookup[index])
    if row is None and label in lookup:
        row = by_index.get(lookup.index(label))
    if row is None and 0 <= index < len(classes):
        row = by_label.get(classes[index])
    return _metric_value_from_class_row(row)


def _preset_label_value_to_display(db: Session, preset_slug: str) -> Dict[str, str]:
    """训练 label_value（英文类名）→ 拉框预设中文 label_key。"""
    slug = (preset_slug or "").strip()
    if not slug:
        return {}
    preset = crud_annotation_taxonomy.label_preset_get_by_slug(db, slug)
    if not preset:
        return {}
    items = sorted(
        list(preset.items or []),
        key=lambda x: (int(getattr(x, "sort_order", 0) or 0), int(x.id)),
    )
    out: Dict[str, str] = {}
    for item in items:
        val = str(getattr(item, "label_value", "") or "").strip()
        key = str(getattr(item, "label_key", "") or "").strip()
        if val and key:
            out[val] = key
    return out


def _build_publish_label_slug_aliases(
    db: Session,
    *,
    preset_slug: str,
    task,
) -> Dict[str, str]:
    preset_items: List[Any] = []
    slug = (preset_slug or "").strip()
    if slug:
        preset = crud_annotation_taxonomy.label_preset_get_by_slug(db, slug)
        if preset:
            preset_items = list(preset.items or [])
    task_cfg: dict[str, Any] = {}
    if task is not None:
        task_cfg = task_service._parse_task_config(getattr(task, "config", None))
    return build_rect_label_slug_aliases(
        preset_label_items=preset_items,
        task_config=task_cfg,
    )


def _resolve_publish_class_slugs(
    db: Session,
    *,
    raw_classes: List[str],
    preset_slug: str,
    task,
) -> List[str]:
    aliases = _build_publish_label_slug_aliases(
        db,
        preset_slug=preset_slug,
        task=task,
    )
    slugs, unresolved = normalize_rect_class_names_to_slugs(raw_classes, aliases)
    if unresolved:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=(
                "以下训练类别无法解析为英文 slug，请在「预设与维度」为拉框预设补全 label_value："
                + "、".join(unresolved)
            ),
        )
    if not slugs:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="发布包类别列表为空，无法写入 labels.txt",
        )
    return slugs


def _class_names_for_publish(job) -> List[str]:
    names, _src = _class_names_with_source_for_publish(job)
    return names


def _class_names_with_source_for_publish(job) -> Tuple[List[str], str]:
    """
    发布包类别顺序与训练 data.yaml names（YOLO id 0..nc-1）一致。
    不使用 BM1688 模板 zip 内旧 running_info 的类别顺序。
    """
    if str(getattr(job, "job_type", "") or "").strip() == "plate_recognition":
        return ["doubleEP"], "plate_recognition_v1"
    frozen_names = []
    try:
        frozen_names = json.loads(getattr(job, "class_names_json", None) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        frozen_names = []
    names = read_class_names_from_data_yaml(
        _job_work_root(job.id) / "dataset" / "data.yaml"
    )
    if frozen_names:
        from labelu.internal.common.label_contract import class_order_signature

        if names and list(names) != list(frozen_names):
            raise LabelUException(
                code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="data.yaml 类别顺序与训练作业冻结契约不一致，禁止发布",
            )
        expected_signature = str(getattr(job, "class_order_signature", None) or "")
        if expected_signature and class_order_signature(frozen_names) != expected_signature:
            raise LabelUException(
                code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="训练作业类别顺序签名校验失败，禁止发布",
            )
        return [str(item) for item in frozen_names], "yolo_train_job.class_names_json"
    if names:
        return names, "dataset/data.yaml"
    from_metrics = read_classes_from_metrics_summary(job.metrics_summary)
    if from_metrics:
        return from_metrics, "metrics_summary"
    cm = _class_metrics_for_job(job)
    if cm and cm.classes:
        return list(cm.classes), "class_metrics"
    return _classes_fallback_for_job(job), "fallback"


def _runtime_template_for_job(
    db: Session,
    *,
    job,
    environment_id: str,
) -> RuntimePublishTemplate:
    runtime_profile = str(getattr(job, "runtime_profile", None) or "").strip()
    interface_version = getattr(job, "runtime_interface_version", None)
    if not runtime_profile or interface_version is None:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="训练作业没有新版运行契约，请使用当前版本重新训练后再发布",
        )
    rows = (
        db.query(RuntimePublishTemplate)
        .filter(
            RuntimePublishTemplate.runtime_profile == runtime_profile,
            RuntimePublishTemplate.interface_version == int(interface_version),
            RuntimePublishTemplate.environment_id.in_([environment_id, "default"]),
            RuntimePublishTemplate.status == "active",
            RuntimePublishTemplate.deleted_at.is_(None),
        )
        .all()
    )
    rows.sort(
        key=lambda row: (
            0 if str(row.environment_id) == str(environment_id) else 1,
            0 if bool(row.is_default) else 1,
            -int(row.id),
        )
    )
    if not rows:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=(
                f"未配置运行时模板: {runtime_profile} / interface v{interface_version} "
                f"/ environment {environment_id}"
            ),
        )
    row = rows[0]
    if not str(row.aicm_model_set_id or "").strip().isdigit():
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"运行时模板 {row.template_key} 未配置有效的 AICM model_set_id",
        )
    return row


def _runtime_template_cached(row: RuntimePublishTemplate) -> bool:
    cache_name = (
        f"runtime_{row.runtime_profile}_v{row.interface_version}_{row.environment_id}"
    )
    cache_dir = _templates_root() / _safe_dir_name(cache_name)
    zip_path = cache_dir / "template.zip"
    meta_path = cache_dir / "template_meta.json"
    if not zip_path.is_file() or not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return (
        int(meta.get("template_id") or 0) == int(row.id)
        and int(meta.get("model_set_id") or 0)
        == int(str(row.aicm_model_set_id or "0"))
        and str(meta.get("content_sha256") or "").strip().lower()
        == _file_content_sha256(zip_path)
    )


def _validate_runtime_template_capability(row: RuntimePublishTemplate, job, classes: List[str]) -> None:
    try:
        capability = json.loads(row.capability_contract_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message=f"运行时模板 {row.template_key} 的能力契约不是有效 JSON",
        ) from exc
    supported_job_types = {
        str(item).strip() for item in capability.get("supported_job_types", []) if str(item).strip()
    }
    job_type = str(getattr(job, "job_type", None) or "yolo_detection").strip()
    if supported_job_types and job_type not in supported_job_types:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message=f"运行时模板 {row.template_key} 不支持训练作业类型 {job_type}",
        )
    max_classes = capability.get("max_classes")
    if max_classes is not None and len(classes) > int(max_classes):
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message=f"运行时模板最多支持 {int(max_classes)} 个类别，当前为 {len(classes)} 个",
        )


def _file_content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_runtime_template_zip(
    sess: requests.Session,
    base: str,
    row: RuntimePublishTemplate,
    *,
    verify: bool,
    steps: List[str],
    progress: Optional[PublishJobTracker] = None,
    use_cache: bool = False,
) -> Tuple[Path, int]:
    model_set_id = int(str(row.aicm_model_set_id).strip())
    cache_name = f"runtime_{row.runtime_profile}_v{row.interface_version}_{row.environment_id}"
    cache_dir = _templates_root() / _safe_dir_name(cache_name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "template.zip"
    meta_path = cache_dir / "template_meta.json"
    expected_sha = str(row.template_sha256 or "").strip().lower()
    if use_cache and zip_path.is_file() and meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            cached_sha = _file_content_sha256(zip_path)
            if int(meta.get("model_set_id") or 0) == model_set_id and (
                not expected_sha or cached_sha == expected_sha
            ) and _template_zip_is_yolov11_format(zip_path):
                if progress is not None:
                    progress.ok("template", f"使用运行时模板缓存 {row.template_key}")
                return zip_path, model_set_id
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    if progress is not None:
        progress.start("template", f"下载运行时模板 {row.template_key}")
    download_url = _get_download_url(sess, base, model_set_id, verify=verify)
    response = sess.get(download_url, timeout=AICM_HTTP_TIMEOUT, verify=verify, stream=True)
    if response.status_code >= 400:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"下载运行时模板失败 HTTP {response.status_code}",
        )
    with zip_path.open("wb") as stream:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if chunk:
                stream.write(chunk)
    actual_sha = _file_content_sha256(zip_path)
    if expected_sha and actual_sha != expected_sha:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message=f"运行时模板 SHA256 不一致: expected={expected_sha}, actual={actual_sha}",
        )
    if not _template_zip_is_yolov11_format(zip_path):
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message=f"运行时模板 {row.template_key} 不是受支持的 yoloV11 包结构",
        )
    meta_path.write_text(
        json.dumps(
            {
                "template_id": int(row.id),
                "template_key": row.template_key,
                "model_set_id": model_set_id,
                "runtime_profile": row.runtime_profile,
                "interface_version": int(row.interface_version),
                "content_sha256": actual_sha,
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _log_step(
        steps,
        f"已按运行时契约下载模板 {row.template_key} (model_set_id={model_set_id})",
        progress,
    )
    if progress is not None:
        progress.ok("template", str(zip_path))
    return zip_path, model_set_id


def _resolve_publish_labels_content(
    db: Session,
    *,
    classes: List[str],
    preset_name: str,
    preset_slug: str,
) -> Tuple[str, List[str]]:
    """
    历史辅助（当前发布流水线未使用）。

    发布写入 labels.txt 时首行以 AICM 模板为准且禁止改写，见 _patch_labels_txt_in_place。
    """
    first_line = (preset_name or "").strip() or "model"
    value_to_cn = _preset_label_value_to_display(db, preset_slug)
    body: List[str] = []
    for cls in classes:
        body.append(value_to_cn.get(cls) or cls)
    return first_line, body


def _aicm_upload_label_lines(classes: List[str]) -> List[str]:
    """保持类别文本和 YOLO 训练顺序，不在写包阶段改名或重排。"""
    return list(classes)


def _aggregate_map_for_publish(job, train_m) -> float:
    test_m = _test_val_metrics_for_job(job)
    if test_m:
        if test_m.map50_95 is not None:
            return float(test_m.map50_95)
        if test_m.map50 is not None:
            return float(test_m.map50)
    if train_m:
        if train_m.map50_95 is not None:
            return float(train_m.map50_95)
        if train_m.map50 is not None:
            return float(train_m.map50)
    return 0.0


def _labels_first_line_preserve(raw: str) -> str:
    """从 labels 原文截取第 1 行（到首个换行符为止），不做 strip，避免改动 ? 个数或首尾空白。"""
    if not raw:
        return ""
    for sep in ("\r\n", "\n", "\r"):
        pos = raw.find(sep)
        if pos >= 0:
            return raw[:pos]
    return raw


def _patch_labels_txt_in_place(labels_path: Path, class_lines: List[str]) -> None:
    """
    替换 labels.txt 第 2 行起的类别值。

    **严格禁止修改首行**（含 ? 占位符个数、首尾空白、CRLF）：首行必须与原模板
    labels.txt 字节级一致，不得替换为预设中文名或其它字符串。
    第 2 行起仅写入训练类别 slug（YOLO id 0..nc-1 顺序）；行数按训练类数扩展或截断。
    """
    if not labels_path.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"模板缺少 labels.txt: {labels_path}",
        )
    raw_bytes = labels_path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw_bytes else "\n"
    raw = raw_bytes.decode("utf-8", errors="replace")
    had_trailing_nl = raw.endswith("\n") or raw.endswith("\r\n")
    lines = raw.splitlines()
    if not lines:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"模板 labels.txt 为空: {labels_path}",
        )
    first_line = _labels_first_line_preserve(raw)
    template_slots = max(0, len(lines) - 1)
    if template_slots == 0 and not class_lines:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=(
                f"模板 labels.txt 仅有首行、无类别槽位: {labels_path}；"
                "且无训练类别可写入"
            ),
        )
    body = list(class_lines)
    new_text = newline.join([first_line] + body)
    if had_trailing_nl:
        new_text += newline
    labels_path.write_bytes(new_text.encode("utf-8"))


def _patch_video_json_in_place(
    video_path: Path,
    *,
    imgsz: int,
    num_class: int,
    chip_type: str,
    model_rel_path: Optional[str] = None,
) -> None:
    """
    仅在 video.json 原文中替换模板已有且需同步的 rknn 字段值（InputSize / NumClass）。
    若提供 model_rel_path，则同步更新 ModelPath（用于模型文件名与原值不一致时）。
    不 json.dumps 重写整文件；不新增键；不改 NumResults / Priorsbox 等其它字段。
    """
    if not video_path.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"模板缺少 video.json: {video_path}",
        )
    raw = video_path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"video.json 解析失败: {exc}",
        ) from exc
    if not isinstance(data, dict):
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="video.json 根节点须为对象",
        )
    rknn = data.get("rknn")
    if not isinstance(rknn, dict):
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="模板 video.json 缺少 rknn 对象",
        )
    patches: List[Tuple[str, Any]] = []
    if "InputSize" in rknn:
        patches.append(("InputSize", int(imgsz)))
    if "NumClass" in rknn:
        patches.append(("NumClass", int(num_class)))
    if model_rel_path and "ModelPath" in rknn:
        current_mp = str(rknn.get("ModelPath") or "").strip()
        if current_mp != model_rel_path:
            patches.append(("ModelPath", model_rel_path))
    new_raw = raw
    for key, val in patches:
        if rknn.get(key) == val:
            continue
        new_raw = _replace_rknn_json_field(new_raw, key, val)
    if new_raw != raw:
        video_path.write_text(new_raw, encoding="utf-8")


def _initialize_direct_yolov11_package(
    pkg_root: Path,
    *,
    input_size: int,
    num_class: int,
) -> Path:
    """Create the exact four-file YOLOv11 AICM package used by production."""
    out_rknn = pkg_root / "output_rknn"
    out_rknn.mkdir(parents=True, exist_ok=True)
    (pkg_root / "running_info.yml").write_text(
        "accuracy_list:\n"
        "epoch: 0.0\n"
        "res_l_count: 0\n"
        "mAP: 0.0\n"
        f"resolution: {int(input_size)}x{int(input_size)}\n",
        encoding="utf-8",
        newline="\n",
    )
    (out_rknn / "labels.txt").write_text(
        DIRECT_LABELS_FIRST_LINE_PLACEHOLDER + "\n",
        encoding="utf-8",
        newline="\n",
    )
    video_payload = {
        "rknn": {
            "InputSize": int(input_size),
            "NumResults": 0,
            "NumClass": int(num_class),
            "CropType": 3,
            "MaxBatchSize": 1,
            "ImgCollect": 1,
            "ModelFunction": "targetDetetct",
            "ModelAlgType": "yoloV11",
            "ModelPath": f"./{DIRECT_YOLOV11_BMODEL_FILENAME}",
            "Priorsbox": "",
            "Labelsmap": "./labels.txt",
            "ModelChipType": "SOPHGO,BM1688,NPU",
        }
    }
    (out_rknn / "video.json").write_text(
        json.dumps(video_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out_rknn / DIRECT_YOLOV11_BMODEL_FILENAME).write_bytes(b"")
    return out_rknn


_PLATE_RUNTIME_PROFILE = "plate_recognition_v1"
_PLATE_RUNTIME_NETWORKS = ["plate_pose", "plate_ocr", "plate_color", "plate_type"]


def _plate_runtime_contract_path(pkg_root: Path, out_rknn: Path) -> Path:
    for candidate in (
        out_rknn / "plate_recognition_contract.json",
        pkg_root / "plate_recognition_contract.json",
    ):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        profile = str(
            payload.get("training_profile")
            or payload.get("profile")
            or payload.get("format")
            or ""
        )
        networks = payload.get("networks") or {}
        network_names = set(networks if isinstance(networks, dict) else networks)
        if _PLATE_RUNTIME_PROFILE in profile and set(_PLATE_RUNTIME_NETWORKS).issubset(
            network_names
        ):
            return candidate
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_400_BAD_REQUEST,
        message=(
            "当前 AICM 模板不支持复合车牌运行时。模板必须预置 "
            "plate_recognition_contract.json，并声明 plate_pose、plate_ocr、"
            "plate_color、plate_type 四个网络；禁止套用单 YOLO 模板发布。"
        ),
    )


def _patch_plate_video_json(video_path: Path, *, model_rel_path: Optional[str]) -> None:
    payload = json.loads(video_path.read_text(encoding="utf-8"))
    rknn = payload.get("rknn")
    if not isinstance(rknn, dict):
        rknn = {}
    rknn["ModelFunction"] = "plateRecognitionV1"
    rknn["ModelAlgType"] = "plateRecognitionV1"
    if model_rel_path:
        rknn["ModelPath"] = model_rel_path
    rknn["NumClass"] = 1
    rknn["Networks"] = [
        {"name": "plate_pose", "role": "plate_detection_and_four_corners"},
        {"name": "plate_ocr", "role": "plate_number_ctc"},
        {"name": "plate_color", "role": "plate_color_classification"},
        {"name": "plate_type", "role": "plate_type_classification"},
    ]
    rknn["Pipeline"] = [
        "plate_pose",
        "perspective_warp",
        "lower_row_extract",
        ["plate_ocr", "plate_color", "plate_type"],
    ]
    payload["rknn"] = rknn
    video_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _patch_plate_running_info(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    begin = "# labelu_plate_recognition_begin"
    end = "# labelu_plate_recognition_end"
    text = re.sub(
        rf"\n?{re.escape(begin)}.*?{re.escape(end)}\n?",
        "\n",
        text,
        flags=re.DOTALL,
    ).rstrip()
    block = "\n".join(
        [
            begin,
            f"training_profile: {_PLATE_RUNTIME_PROFILE}",
            "runtime_networks:",
            *[f"  - {name}" for name in _PLATE_RUNTIME_NETWORKS],
            "runtime_pipeline:",
            "  - plate_pose",
            "  - perspective_warp",
            "  - lower_row_extract",
            "  - plate_ocr",
            "  - plate_color",
            "  - plate_type",
            end,
        ]
    )
    path.write_text(text + "\n" + block + "\n", encoding="utf-8")


def _patch_running_info_yml_in_place(
    path: Path,
    *,
    classes: List[str],
    class_metrics_rows: List[Any],
    epoch: float,
    map_val: float,
    res_l_count: int,
    imgsz: int,
    metric_lookup_classes: Optional[List[str]] = None,
) -> None:
    """沿用模板 accuracy_list 结构，类别名写入英文 slug，仅替换精度数值与 epoch/mAP 等标量。"""
    if not path.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"模板缺少 running_info.yml: {path}",
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    template_labels = _parse_accuracy_labels_from_running_info(text)
    by_index, by_label = _class_metric_rows_indexed(class_metrics_rows)
    lines = text.splitlines()
    out: List[str] = []
    in_accuracy = False
    label_idx = 0

    def _metric_for_template_label(tpl_label: str, slot: int) -> float:
        slug = classes[slot] if 0 <= slot < len(classes) else tpl_label
        return _metric_value_for_label(
            slug,
            by_label=by_label,
            by_index=by_index,
            classes=classes,
            index=slot,
            lookup_classes=metric_lookup_classes,
        )

    for line in lines:
        stripped = line.strip()
        if stripped == "accuracy_list:":
            in_accuracy = True
            label_idx = 0
            out.append(line)
            continue
        if in_accuracy:
            if stripped.startswith("- ") or (
                stripped and line.startswith(" ") and "- " in stripped
            ):
                item = _normalize_running_info_label(
                    stripped[2:].strip() if stripped.startswith("- ") else stripped
                )
                if _looks_like_accuracy_number(item):
                    indent = line[: len(line) - len(line.lstrip())]
                    tpl = (
                        template_labels[label_idx - 1]
                        if 0 < label_idx <= len(template_labels)
                        else ""
                    )
                    val = _metric_for_template_label(tpl, max(0, label_idx - 1))
                    out.append(f"{indent}- {val}")
                    continue
                if item:
                    indent = line[: len(line) - len(line.lstrip())]
                    slug = (
                        classes[label_idx]
                        if label_idx < len(classes)
                        else _normalize_running_info_label(item)
                    )
                    if stripped.startswith("- - "):
                        out.append(f"{indent}- - {slug}")
                    else:
                        out.append(f"{indent}- {slug}")
                    label_idx += 1
                    continue
            if stripped and not stripped.startswith("#"):
                in_accuracy = False
        if stripped.startswith("epoch:"):
            out.append(f"epoch: {float(epoch)}")
            continue
        if stripped.startswith("mAP:"):
            out.append(f"mAP: {round(float(map_val), 3)}")
            continue
        if stripped.startswith("res_l_count:"):
            out.append(f"res_l_count: {int(res_l_count)}")
            continue
        if stripped.startswith("resolution:"):
            out.append(f"resolution: {int(imgsz)}x{int(imgsz)}")
            continue
        out.append(line)
    new_text = "\n".join(out)
    if text.endswith("\n"):
        new_text += "\n"
    path.write_text(new_text, encoding="utf-8")


def _write_running_info_yml(
    path: Path,
    *,
    classes: List[str],
    class_metrics_rows: List[Any],
    epoch: float,
    map_val: float,
    res_l_count: int,
    imgsz: int,
    template_path: Optional[Path] = None,
    accuracy_names: Optional[List[str]] = None,
    metric_lookup_classes: Optional[List[str]] = None,
) -> None:
    """
    写入 running_info.yml：结构对齐参考包；accuracy 类别名与 labels.txt 第 2 行起一致。
    指标按训练类别查表；accuracy_names 默认与 classes 相同。
    """
    yaml_style = "nested"
    if template_path is not None and template_path.is_file():
        try:
            template_text = template_path.read_text(encoding="utf-8", errors="replace")
            yaml_style = _running_info_yaml_style(template_text)
        except OSError:
            yaml_style = "nested"

    by_index, by_label = _class_metric_rows_indexed(class_metrics_rows)
    slug_order = list(classes)

    name_for_slug: Dict[str, str] = {}
    if accuracy_names:
        for i, slug in enumerate(classes):
            if i < len(accuracy_names):
                nm = str(accuracy_names[i] or "").strip()
                if nm:
                    name_for_slug[slug] = nm

    lines = ["accuracy_list:"]
    for i, slug in enumerate(slug_order):
        train_idx = classes.index(slug) if slug in classes else i
        name = name_for_slug.get(slug) or slug
        val = _metric_value_for_label(
            slug,
            by_label=by_label,
            by_index=by_index,
            classes=classes,
            index=train_idx,
            lookup_classes=metric_lookup_classes,
        )
        if yaml_style == "nested":
            lines.append(f"  - - {name}")
            lines.append(f"    - {val}")
        else:
            lines.append(f"- {name}")
            lines.append(f"- {val}")
    lines.append(f"epoch: {float(epoch)}")
    lines.append(f"res_l_count: {int(res_l_count)}")
    lines.append(f"mAP: {round(float(map_val), 3) if map_val is not None else 0.0}")
    lines.append(f"resolution: {int(imgsz)}x{int(imgsz)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _rknn_field(rknn: dict, key: str, default: Any) -> Any:
    """模板 video.json 里常见 null，AICM 侧可能对字段做 for-in 导致崩溃。"""
    v = rknn.get(key, default)
    return default if v is None else v


def _update_video_json(
    video_path: Path,
    *,
    imgsz: int,
    num_class: int,
    model_rel_path: str,
    chip_type: str,
) -> None:
    data: dict = {}
    if video_path.is_file():
        try:
            raw = json.loads(video_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except json.JSONDecodeError:
            data = {}
    rknn = data.get("rknn")
    if not isinstance(rknn, dict):
        rknn = {}
    num_results = _rknn_field(rknn, "NumResults", 0)
    try:
        num_results = int(num_results)
    except (TypeError, ValueError):
        num_results = 0
    if num_results <= 0:
        num_results = int(num_class)
    priors = _rknn_field(rknn, "Priorsbox", "")
    if priors is None or isinstance(priors, (int, float)):
        priors = ""
    elif not isinstance(priors, str):
        priors = str(priors)
    rknn.update(
        {
            "InputSize": int(imgsz),
            "NumResults": num_results,
            "NumClass": int(num_class),
            "CropType": int(_rknn_field(rknn, "CropType", 3)),
            "MaxBatchSize": int(_rknn_field(rknn, "MaxBatchSize", 1)),
            "ImgCollect": int(_rknn_field(rknn, "ImgCollect", 1)),
            "ModelFunction": DEFAULT_MODEL_FUNCTION,
            "ModelAlgType": DEFAULT_MODEL_ALG_TYPE,
            "ModelPath": model_rel_path,
            "Priorsbox": priors,
            "Labelsmap": "./labels.txt",
            "ModelChipType": chip_type,
        }
    )
    data["rknn"] = rknn
    video_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_publish_package(
    template_zip: Optional[Path],
    *,
    export_id: int,
    job,
    db: Session,
    task,
    preset_name: str,
    preset_slug: str,
    steps: List[str],
    chip_type: str,
    bmodel_precision: Optional[str] = None,
    bmodel_rel_path: Optional[str] = None,
    progress: Optional[PublishJobTracker] = None,
) -> Path:
    if progress is not None:
        progress.start("package", "生成 YOLOv11 标准发布包")
    work = _create_publish_work_dir(export_id)
    is_plate_job = (
        str(getattr(job, "job_type", "") or "").strip() == "plate_recognition"
    )
    export_row = crud_yolo_bm1688_export.get(db, export_id)
    package_input_size = int(
        getattr(export_row, "imgsz", None) or getattr(job, "imgsz", None) or 640
    )
    direct_package = template_zip is None
    if direct_package:
        if is_plate_job:
            raise LabelUException(
                code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="复合车牌模型必须使用其专用运行包，不能套用标准 YOLOv11 检测包",
            )
        pkg_root = work / "package"
        out_rknn = _initialize_direct_yolov11_package(
            pkg_root,
            input_size=package_input_size,
            num_class=max(1, len(_class_names_for_publish(job))),
        )
        _log_step(steps, "已创建无 Config416.ini 的 YOLOv11 标准包", progress)
    else:
        extract_dir = work / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        safe_extract_zip(template_zip, extract_dir)
        pkg_root = _find_package_root(extract_dir)
        out_rknn = _find_output_rknn(pkg_root)
        _log_step(steps, f"解压专用运行包至 {pkg_root.name}", progress)
    plate_contract_target = None
    if is_plate_job:
        plate_contract_target = _plate_runtime_contract_path(
            pkg_root, out_rknn
        )
    file_snapshot = _snapshot_package_files(pkg_root)

    bmodel_src, picked_rel = _resolve_bmodel(
        export_id,
        bmodel_precision=bmodel_precision,
        bmodel_rel_path=bmodel_rel_path,
    )
    if is_plate_job and bmodel_src.name != "plate_recognition_fp16_1b.bmodel":
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="复合车牌发布必须选择 plate_recognition_fp16_1b.bmodel 合并模型",
        )
    dest_bmodel, model_path_patch = _deploy_publish_bmodel(
        out_rknn,
        bmodel_src,
        picked_rel,
    )
    if model_path_patch:
        _log_step(
            steps,
            f"已写入 bmodel：读取导出 models/{picked_rel} → "
            f"output_rknn/{dest_bmodel.name}，并移除模板占位 bmodel、"
            f"将 video.json ModelPath 更新为 {model_path_patch}",
            progress,
        )
    else:
        _log_step(
            steps,
            f"已替换 bmodel：读取导出 models/{picked_rel} → "
            f"output_rknn/{dest_bmodel.name}（与模板 ModelPath 一致）",
            progress,
        )

    classes, class_source = _class_names_with_source_for_publish(job)
    if not classes:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="无法读取训练类别列表，请确认训练 metrics 或 data.yaml 存在",
        )
    if direct_package:
        publish_slugs = list(classes)
    else:
        publish_slugs = _resolve_publish_class_slugs(
            db,
            raw_classes=classes,
            preset_slug=preset_slug,
            task=task,
        )
    _log_step(
        steps,
        f"训练类别 {len(classes)} 个（顺序来自 {class_source}）：{', '.join(classes)}；"
        f"发布包写入训练类别原文：{', '.join(publish_slugs)}",
        progress,
    )

    ri_path = pkg_root / "running_info.yml"

    labels_path = out_rknn / "labels.txt"
    if not labels_path.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="模板 output_rknn 目录缺少 labels.txt（标签文件应在 output_rknn 内）",
        )
    labels_body = _aicm_upload_label_lines(publish_slugs)
    first_line_before = _labels_first_line_preserve(
        labels_path.read_bytes().decode("utf-8", errors="replace")
    )
    _patch_labels_txt_in_place(labels_path, labels_body)
    first_line_after = _labels_first_line_preserve(
        labels_path.read_bytes().decode("utf-8", errors="replace")
    )
    if first_line_after != first_line_before:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=(
                "labels.txt 首行被意外修改："
                f"改前 {first_line_before!r}（{len(first_line_before)} 字符）→ "
                f"改后 {first_line_after!r}（{len(first_line_after)} 字符）"
            ),
        )
    _log_step(
        steps,
        f"已补丁 output_rknn/labels.txt（首行原样保留={first_line_after!r}，"
        f"共 {len(first_line_after)} 字符；{len(labels_body)} 类="
        f"{', '.join(labels_body[:3])}{'…' if len(labels_body) > 3 else ''}）",
        progress,
    )

    video_json = out_rknn / "video.json"
    if not video_json.is_file():
        video_json = pkg_root / "video.json"
    _patch_video_json_in_place(
        video_json,
        imgsz=package_input_size,
        num_class=len(publish_slugs),
        chip_type=chip_type,
        model_rel_path=model_path_patch,
    )
    if is_plate_job:
        _patch_plate_video_json(video_json, model_rel_path=model_path_patch)
        export_contract = (
            Path(settings.MEDIA_ROOT)
            / settings.YOLO_BM1688_EXPORT_SUBDIR
            / str(export_id)
            / "plate_recognition_contract.json"
        )
        if not export_contract.is_file() or plate_contract_target is None:
            raise LabelUException(
                code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="复合车牌 BM1688 导出缺少 plate_recognition_contract.json",
            )
        shutil.copy2(export_contract, plate_contract_target)
    if model_path_patch:
        _log_step(
            steps,
            f"已更新 video.json（InputSize/NumClass/ModelPath={model_path_patch}，其余字段不变）",
            progress,
        )
    else:
        _log_step(
            steps,
            "已更新 video.json（仅 InputSize/NumClass，ModelPath 与其余字段不变）",
            progress,
        )

    tm = _training_val_metrics_for_job(job)
    map_val = _aggregate_map_for_publish(job, tm)
    epoch = float(
        actual_epochs_for_train_job(
            job_id=job.id,
            weights_path=job.weights_path,
            metrics_summary=job.metrics_summary,
        )
        or job.epochs
        or 0
    )
    sample_counts = _sample_counts_for_job(job)
    res_l = int(sample_counts.get("total") or 0)
    cm = _class_metrics_for_job(job)
    metric_rows = cm.rows if cm else []
    if direct_package:
        _write_running_info_yml(
            ri_path,
            classes=publish_slugs,
            class_metrics_rows=metric_rows,
            epoch=epoch,
            map_val=map_val,
            res_l_count=res_l,
            imgsz=int(job.imgsz or 640),
            metric_lookup_classes=classes,
        )
    else:
        _patch_running_info_yml_in_place(
            ri_path,
            classes=publish_slugs,
            class_metrics_rows=metric_rows,
            epoch=epoch,
            map_val=map_val,
            res_l_count=res_l,
            imgsz=int(job.imgsz or 640),
            metric_lookup_classes=classes,
        )
    if is_plate_job:
        _patch_plate_running_info(ri_path)
    _log_step(
        steps,
        "已写入 running_info.yml（accuracy_list 与训练类别顺序一致，并写入实际精度、epoch、样本数和 mAP）",
        progress,
    )

    _assert_package_files_unchanged(file_snapshot, _snapshot_package_files(pkg_root))
    _log_step(
        steps, "已校验发布包文件列表与模板一致（仅允许 bmodel 文件名替换）", progress
    )
    if is_plate_job:
        _log_step(
            steps,
            "已校验复合车牌模板能力并写入四网络运行契约",
            progress,
        )
    else:
        _assert_yolov11_publish_package(pkg_root)
        _log_step(
            steps,
            "已校验发布包为 yoloV11 新格式（无 Config416.ini，与垃圾暴露一致）",
            progress,
        )

    out_zip = work / f"model-{datetime.now().strftime('%Y%m%d%H%M%S')}.zip"
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zout:
        for fpath in pkg_root.rglob("*"):
            if fpath.is_file():
                arc = fpath.relative_to(pkg_root).as_posix()
                zout.write(fpath, arc)
    _log_step(steps, f"已打包 {out_zip.name}", progress)
    if progress is not None:
        progress.ok("package", str(out_zip))
    return out_zip


def _aicm_response_ok(upload_text: Optional[str]) -> bool:
    """与官方脚本一致：JSON 中 error_code == 0 表示上传成功。"""
    raw = (upload_text or "").strip()
    if not raw:
        return False
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return '"error_code": 0' in raw or '"error_code":0' in raw
    if not isinstance(body, dict):
        return False
    try:
        return int(body.get("error_code", -1)) == 0
    except (TypeError, ValueError):
        return False


def _aicm_response_error_message(response_text: Optional[str], action: str) -> str:
    raw = (response_text or "").strip()
    if not raw:
        return f"{action}响应为空，请检查 AICM 后台"
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return f"AICM {action}失败：{raw[:300]}"
    if isinstance(body, dict):
        info = str(body.get("error_info") or body.get("message") or "").strip()
        code = body.get("error_code")
        if info:
            return f"AICM {action}失败（error_code={code}）：{info}"
        if code is not None:
            return f"AICM {action}失败（error_code={code}）"
    return f"{action}响应未包含 error_code: 0，请检查 AICM 后台"


def _aicm_upload_error_message(upload_text: Optional[str]) -> str:
    return _aicm_response_error_message(upload_text, "上传")


def _positive_model_set_id(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _upload_response_model_set_ids(upload_text: Optional[str]) -> List[int]:
    """Extract model-set id candidates from a successful upload response."""
    raw = (upload_text or "").strip()
    if not raw:
        return []
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(body, dict):
        return []

    candidates: set[int] = set()

    def collect(node: dict[str, Any]) -> None:
        for key in ("model_set_id", "modelSetId"):
            parsed = _positive_model_set_id(node.get(key))
            if parsed is not None:
                candidates.add(parsed)

        for key in ("model_set", "modelSet"):
            model_set = node.get(key)
            if not isinstance(model_set, dict):
                continue
            for key in ("id", "_id"):
                parsed = _positive_model_set_id(model_set.get(key))
                if parsed is not None:
                    candidates.add(parsed)
            collect(model_set)

        for key in ("data", "result"):
            nested = node.get(key)
            if isinstance(nested, dict):
                collect(nested)

    # Some AICM versions return the created model-set id directly at the root.
    # It remains untrusted until the exact temporary upload name is verified.
    for key in ("id", "_id"):
        parsed = _positive_model_set_id(body.get(key))
        if parsed is not None:
            candidates.add(parsed)

    collect(body)
    return sorted(candidates)


def _resolve_uploaded_model_set_id(
    upload_text: Optional[str],
    *,
    correlated_ids: Optional[set[int]] = None,
) -> Tuple[int, str]:
    response_ids = _upload_response_model_set_ids(upload_text)
    if len(response_ids) > 1:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=(
                "AICM 上传响应包含多个模型集 id "
                f"{response_ids}，无法唯一绑定；已停止 edit_name"
            ),
        )
    if response_ids:
        response_id = response_ids[0]
        exact_name_ids = set(correlated_ids or set())
        if response_id not in exact_name_ids:
            if exact_name_ids:
                detail = (
                    f"临时关联名查询对应 id={sorted(exact_name_ids)}，"
                    f"与上传响应 id={response_id} 不一致"
                )
            else:
                detail = f"未查询到 id={response_id} 对应本次上传临时关联名"
            raise LabelUException(
                code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                status_code=status.HTTP_502_BAD_GATEWAY,
                message=(
                    f"AICM 上传成功但{detail}；无法证明模型归属，" "已停止 edit_name"
                ),
            )
        return response_id, "upload_response"

    exact_name_ids = sorted(correlated_ids or set())
    if len(exact_name_ids) == 1:
        return exact_name_ids[0], "upload_correlation_name"
    if len(exact_name_ids) > 1:
        detail = f"上传临时关联名匹配到多个模型集 id={exact_name_ids}"
    else:
        detail = "未找到与本次上传临时关联名一致的模型集"
    raise LabelUException(
        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
        status_code=status.HTTP_502_BAD_GATEWAY,
        message=f"AICM 上传成功但{detail}；无法证明模型归属，已停止 edit_name",
    )


def _locate_uploaded_model_set_id(
    sess: requests.Session,
    base: str,
    upload_text: Optional[str],
    *,
    correlation_name: str,
    verify: bool,
) -> Tuple[int, str]:
    """Locate an upload only after its id-to-temporary-name ownership is proven."""
    response_ids = _upload_response_model_set_ids(upload_text)
    expected_id = response_ids[0] if len(response_ids) == 1 else None
    correlated_ids = _wait_for_model_set_ids_by_exact_name(
        sess,
        base,
        name=correlation_name,
        verify=verify,
        expected_id=expected_id,
    )
    return _resolve_uploaded_model_set_id(
        upload_text,
        correlated_ids=correlated_ids,
    )


def _locate_uploaded_model_set_id_for_publish(
    sess: requests.Session,
    base: str,
    upload_text: Optional[str],
    *,
    correlation_name: str,
    before_id: int,
    verify: bool,
) -> Tuple[int, str]:
    """Resolve the ID created by this upload before changing its name.

    AICM deployments differ in their upload response: some return the new
    model-set ID, while others return only ``error_code=0`` and assign a
    numeric name asynchronously. The response ID is authoritative when
    present. Older deployments fall back to the temporary-name lookup and,
    finally, the newly-created numeric model-set lookup.
    """
    response_ids = _upload_response_model_set_ids(upload_text)
    if len(response_ids) > 1:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=(
                f"AICM 上传响应包含多个模型集 id {response_ids}，"
                "无法唯一绑定；已停止 edit_name"
            ),
        )
    if response_ids:
        return response_ids[0], "upload_response_model_set_id"

    correlation_error: Optional[Exception] = None
    try:
        return (
            _locate_uploaded_model_set_id(
                sess,
                base,
                upload_text,
                correlation_name=correlation_name,
                verify=verify,
            )[0],
            "upload_correlation_name",
        )
    except LabelUException as exc:
        correlation_error = exc

    try:
        row = _wait_for_new_numeric_model_set(
            sess,
            base,
            before_id=before_id,
            verify=verify,
        )
    except LabelUException as exc:
        detail = str(getattr(exc, "detail", None) or exc)
        if correlation_error is not None:
            detail = (
                f"{detail}；临时名称查询也未命中："
                f"{str(getattr(correlation_error, 'detail', None) or correlation_error)}"
            )
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"AICM 上传成功但无法定位新模型 ID：{detail}；已停止 edit_name",
        ) from exc
    model_set_id = _model_set_row_id(row)
    if model_set_id <= 0:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message="AICM 上传成功但新模型缺少有效模型 ID；已停止 edit_name",
        )
    return model_set_id, "upload_new_numeric_model_set"


def _sanitize_upload_name_segment(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\s]+', "_", (name or "").strip())
    return s.strip("_") or "model"


def _normalize_model_alg_type_for_name(raw: str) -> str:
    s = (raw or DEFAULT_MODEL_ALG_TYPE).strip()
    m = re.match(r"^yolo\s*v?\s*(\d+)\s*$", s, re.IGNORECASE)
    if m:
        return f"yolov{int(m.group(1))}"
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", s)
    if cleaned.lower().startswith("yolov"):
        return cleaned.lower()
    if cleaned.lower().startswith("yolo"):
        tail = cleaned[4:]
        if tail.isdigit():
            return f"yolov{int(tail)}"
        if tail.lower().startswith("v") and tail[1:].isdigit():
            return f"yolov{int(tail[1:])}"
    return cleaned.lower() or "yolov11"


def _read_model_alg_type_from_template_zip(template_zip: Path) -> str:
    if not template_zip.is_file():
        return DEFAULT_MODEL_ALG_TYPE
    try:
        with zipfile.ZipFile(template_zip, "r") as zf:
            for name in zf.namelist():
                if not name.endswith("video.json"):
                    continue
                raw = json.loads(zf.read(name).decode("utf-8"))
                if not isinstance(raw, dict):
                    continue
                rknn = raw.get("rknn")
                if isinstance(rknn, dict):
                    alg = rknn.get("ModelAlgType")
                    if alg:
                        return str(alg)
    except (OSError, json.JSONDecodeError, KeyError, zipfile.BadZipFile):
        pass
    return DEFAULT_MODEL_ALG_TYPE


def _read_publish_version_seq(preset_name: str) -> int:
    cache_dir = _templates_root() / _safe_dir_name(preset_name)
    meta_path = cache_dir / PUBLISH_VERSION_SEQ_FILE
    if not meta_path.is_file():
        return 0
    try:
        return int(json.loads(meta_path.read_text(encoding="utf-8")).get("seq") or 0)
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0


def _write_publish_version_seq(preset_name: str, seq: int) -> None:
    cache_dir = _templates_root() / _safe_dir_name(preset_name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    meta_path = cache_dir / PUBLISH_VERSION_SEQ_FILE
    meta_path.write_text(
        json.dumps({"seq": max(1, int(seq))}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _bump_publish_version_seq(preset_name: str) -> int:
    seq = _read_publish_version_seq(preset_name) + 1
    _write_publish_version_seq(preset_name, seq)
    return seq


def _model_version_segment(seq: int) -> str:
    """模型版本段：v1、v2 …（与 AICM 后台展示名一致，如 垃圾暴露_bm1688_v13）。"""
    n = max(1, int(seq))
    return f"v{n}"


def _display_name_for_publish_seq(preset_name: str, seq: int) -> Tuple[str, str]:
    """按序号生成 edit_name 展示名，不修改本地序号缓存。"""
    version = _model_version_segment(seq)
    suffix = f"_{AICM_UPLOAD_PRODUCT_MODEL}_{version}"
    # AICM uses the same 20-character limit for edit_name. Keep the model
    # type and version visible, shortening only the user-facing preset part.
    preset_budget = max(1, AICM_MODEL_NAME_MAX_LENGTH - len(suffix))
    preset = _sanitize_upload_name_segment(preset_name)[:preset_budget]
    name = f"{preset}{suffix}"
    return name[:AICM_MODEL_NAME_MAX_LENGTH], version


def _build_aicm_display_name(
    *,
    preset_name: str,
) -> Tuple[str, int, str]:
    """
    edit_name 使用的展示名：{拉框预设名}_bm1688_{模型版本}
    例：垃圾暴露_bm1688_v13
    """
    seq = _bump_publish_version_seq(preset_name)
    name, version = _display_name_for_publish_seq(preset_name, seq)
    return name, seq, version


def _aicm_name_unique_conflict(response_text: Optional[str]) -> bool:
    """AICM edit_name 返回名称已存在（unique）。"""
    raw = (response_text or "").strip()
    if not raw:
        return False
    haystacks: List[str] = [raw]
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        body = None
    if isinstance(body, dict):
        info = body.get("error_info")
        if info is not None:
            haystacks.append(str(info))
        if isinstance(info, str):
            try:
                nested = json.loads(info)
                if isinstance(nested, dict):
                    haystacks.append(json.dumps(nested, ensure_ascii=False))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
    joined = " ".join(haystacks)
    if "unique" in joined and ("已存在" in joined or "名称" in joined):
        return True
    return '"code": "unique"' in joined or '"code":"unique"' in joined


def _rename_model_set_with_unique_retry(
    sess: requests.Session,
    base: str,
    model_set_id: int,
    *,
    preset_name: str,
    verify: bool,
    progress: Optional[Any] = None,
    steps: Optional[List[str]] = None,
    max_attempts: int = 20,
    target_name: Optional[str] = None,
) -> Tuple[str, int, str, str]:
    """
    edit_name 遇名称冲突时自动递增 vN 重试。
    返回 (display_name, publish_seq, version_seg, last_response_text)。
    """
    if target_name is not None:
        requested = _sanitize_upload_name_segment(target_name)[:AICM_MODEL_NAME_MAX_LENGTH]
        if not requested:
            requested = "model"
        start_seq = 1
    else:
        requested = ""
        start_seq = _bump_publish_version_seq(preset_name)
    last_text = ""
    for offset in range(max(1, int(max_attempts))):
        try_seq = start_seq + offset
        if requested:
            suffix = "" if offset == 0 else f"_{try_seq}"
            display_name = requested[: max(1, AICM_MODEL_NAME_MAX_LENGTH - len(suffix))] + suffix
            version_seg = f"task_name_{try_seq}"
        else:
            display_name, version_seg = _display_name_for_publish_seq(preset_name, try_seq)
        if offset == 0:
            _log_step(
                steps,
                f"已定位上传模型集 id={model_set_id}，调用 edit_name → {display_name} "
                f"（产品型号 {AICM_UPLOAD_PRODUCT_MODEL}，版本 {version_seg}，该预设第 {try_seq} 次）",
                progress,
            )
        else:
            _log_step(
                steps,
                f"名称已存在，自动改用 edit_name → {display_name}（版本 {version_seg}）",
                progress,
            )
        last_text = _edit_model_set_name(
            sess,
            base,
            model_set_id,
            display_name,
            verify=verify,
        )
        _log_step(steps, f"修改名称完成：{(last_text or '')[:120]}", progress)
        if _aicm_response_ok(last_text):
            if not requested:
                _write_publish_version_seq(preset_name, try_seq)
            return display_name, try_seq, version_seg, last_text
        if not _aicm_name_unique_conflict(last_text):
            break
    return "", start_seq, _model_version_segment(start_seq), last_text


def _aicm_upload_model_name(
    *,
    zip_path: Path,
    preset_name: str = "",
    export_id: Optional[int] = None,
) -> str:
    """兼容旧脚本：未走发布流水线时回退为 zip 名或预设名。"""
    stem = (zip_path.stem or "").strip()
    preset = _sanitize_upload_name_segment(preset_name)
    if preset and export_id is not None:
        return f"{preset}_e{int(export_id) % 1000:03d}"[:AICM_MODEL_NAME_MAX_LENGTH]
    if preset:
        return preset[:AICM_MODEL_NAME_MAX_LENGTH]
    return stem[:AICM_MODEL_NAME_MAX_LENGTH] if stem else "model"


def _upload_model_package(
    sess: requests.Session,
    base: str,
    zip_path: Path,
    *,
    name: str = "",
    chip_type: str,
    algorithm_type: str,
    verify: bool,
) -> str:
    """
    POST /v2/model-set/ upload_model。
    发布流水线传唯一临时 name 作为归属证据，定位后再通过 edit_name 重命名。
    """
    data = {
        "_method": "upload_model",
        "chip_type": chip_type,
        "algorithm_type": algorithm_type,
    }
    model_name = (name or "").strip()
    if not model_name:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message="AICM 上传必须提供不超过 20 个字符的临时模型名称",
        )
    if len(model_name) > AICM_MODEL_NAME_MAX_LENGTH:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=(
                "AICM 上传临时模型名称过长："
                f"{len(model_name)} 字符，最多 {AICM_MODEL_NAME_MAX_LENGTH} 字符"
            ),
        )
    data["name"] = model_name

    # requests encodes files= into one large bytes object. urllib3 then applies
    # the connect timeout while sendall() writes that entire body, which can
    # abort slow multi-megabyte uploads. httpx streams the multipart body and
    # provides an independent write timeout.
    cookies = sess.cookies.get_dict()
    with httpx.Client(
        cookies=cookies,
        timeout=AICM_UPLOAD_HTTP_TIMEOUT,
        verify=verify,
        follow_redirects=True,
    ) as client:
        with zip_path.open("rb") as f:
            files = {"model_file": (zip_path.name, f, "application/zip")}
            r = client.post(
                f"{base}/v2/model-set/",
                data=data,
                files=files,
            )
    if r.status_code >= 400:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"AICM 上传 HTTP {r.status_code}: {r.text[:500]}",
        )
    return r.text


def _edit_model_set_name(
    sess: requests.Session,
    base: str,
    model_set_id: int,
    name: str,
    *,
    verify: bool,
) -> str:
    """POST /v2/model-set/ edit_name，为刚上传的模型集设置展示名。"""
    data = {
        "_method": (None, "edit_name"),
        "_id": (None, str(int(model_set_id))),
        "name": (None, (name or "").strip()[:AICM_MODEL_NAME_MAX_LENGTH]),
    }
    r = sess.post(
        f"{base}/v2/model-set/",
        data=data,
        timeout=AICM_HTTP_TIMEOUT,
        verify=verify,
    )
    if r.status_code >= 400:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"AICM 修改名称 HTTP {r.status_code}: {r.text[:500]}",
        )
    return r.text


def _format_aicm_network_error(
    exc: requests.RequestException | httpx.HTTPError,
    base: str,
    *,
    stage: str = "",
) -> str:
    msg = str(exc)
    if stage == "upload" and isinstance(exc, httpx.WriteTimeout):
        return (
            f"AICM 模型包上传发送超时: {msg}\n"
            f"已成功连接并登录 AICM（{base}），但 AICM 未在 "
            f"{int(AICM_UPLOAD_WRITE_TIMEOUT_SECONDS)} 秒内持续接收完模型包。"
            "请检查 AICM uWSGI 工作进程、磁盘和上传链路。"
        )
    hint = (
        f"无法连接 AICM（{base}）。请确认："
        "① 后台根地址仅为 http://IP:8102，不要带 /v2/login；"
        "② LabelU 部署服务器能访问该内网 IP（本机浏览器能打开不代表服务器能连）。"
    )
    if (
        "ConnectTimeout" in msg
        or "Connection refused" in msg
        or "timed out" in msg.lower()
    ):
        return f"AICM 网络请求失败: {msg}\n{hint}"
    return f"AICM 网络请求失败: {msg}"


def _publish_row_matches_search(row: Any, search: str | None) -> bool:
    """Match a publish row against the operator keyword contract.

    Publish jobs are persisted as JSON and the response contains nested gate,
    label, and strategy metadata.  Serializing the already-authorized response
    gives both list endpoints the same literal, case-insensitive search
    behavior without duplicating a long list of fields or exposing rows before
    access filtering has completed.
    """

    keyword = str(search or "").strip().casefold()
    if not keyword:
        return True
    if hasattr(row, "dict"):
        payload = row.dict()
    elif isinstance(row, dict):
        payload = row
    else:
        try:
            payload = vars(row)
        except TypeError:
            payload = str(row)
    def _values(value: Any):
        if value is None:
            return
        if isinstance(value, dict):
            for nested in value.values():
                yield from _values(nested)
            return
        if isinstance(value, (list, tuple, set)):
            for nested in value:
                yield from _values(nested)
            return
        yield str(value)

    haystack = " ".join(_values(payload)).casefold()
    return keyword in haystack


def list_publishable(
    db: Session,
    current_user: User,
    *,
    project_id: Optional[int] = None,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 0,
    size: int = 20,
) -> Tuple[List[AicmPublishableRowResponse], int]:
    project_id = _assert_project_access(db, current_user, project_id)
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    unrestricted = is_task_superuser(current_user)
    task_ids_filter = yolo_train_service._task_ids_for_rect_preset_filter(
        db, current_user, rect_preset_slug=rect_preset_slug
    )
    export_query: dict[str, Any] = {
        "page": 0,
        # Search is applied to the assembled response because tags, category
        # names, and gate metadata are not all persisted on the export row.
        # Read a wider candidate window for a keyword so matching rows are not
        # hidden behind the normal dashboard cap.
        "size": 10000 if str(search or "").strip() else 500,
        "dataset_category_id": dataset_category_id,
        "task_ids": task_ids_filter,
        "task_access_unrestricted": unrestricted,
        "task_access_builtin_admin": is_consistent_pool_admin(current_user),
        "export_status": "COMPLETED",
    }
    if project_id is not None:
        export_query["project_id"] = project_id
    rows, _ = crud_yolo_bm1688_export.list_for_user(
        db,
        current_user.id,
        **export_query,
    )
    out: List[AicmPublishableRowResponse] = []
    for export_row, task in rows:
        publish_status = str(getattr(export_row, "publish_status", "") or "").upper()
        publish_expires_at = getattr(export_row, "publish_lease_expires_at", None)
        if publish_status == "COMPLETED" or getattr(export_row, "published_model_id", None) is not None:
            continue
        if (
            publish_status == "PROCESSING"
            and publish_expires_at is not None
            and publish_expires_at > datetime.now()
        ):
            continue
        job = crud_yolo_train_job.get(db, export_row.yolo_train_job_id)
        if not job or job.status != YoloTrainStatus.COMPLETED.value:
            continue
        if project_id is not None:
            # The task is the export parent; the train job is an additional
            # lineage check because it carries a denormalized project FK.
            if getattr(task, "project_id", None) is None:
                continue
            if int(task.project_id) != int(project_id):
                continue
            if getattr(job, "project_id", None) is None:
                continue
            if (
                int(job.project_id) != int(project_id)
                or getattr(job, "task_id", None) is None
                or int(job.task_id) != int(task.id)
            ):
                continue
        bm_names = bm_export_service._list_bmodel_names(export_row.id)
        bmodel_options = _publish_bmodel_options_for_job(job, bm_names)
        if not bmodel_options:
            continue
        train_m = _training_val_metrics_for_job(job)
        vp = _validation_pass_row(train_m)
        contract_fields = yolo_train_service._job_contract_response_fields(job)
        preset_name = "YOLOv11 标准发布包"
        preset_slug = ""
        publish_template_key = "yolov11_direct"
        publish_template_source = "generated"
        template_cached = False
        creators = _creator_emails_by_user_ids(db, [job.created_by])
        dataset_category_fields = yolo_train_service._training_task_response_fields(
            db, job, task.name
        )
        edge_validation = _edge_validation_status_for_export(
            db, export_id=export_row.id
        )
        evidence_comparison = bm_export_service._effective_pt_bmodel_comparison(
            export_row,
            edge_validation,
        )
        policy_snapshot = quality_policy_snapshot_for_job(job)
        bmodel_options = _prepare_publish_bmodel_options(
            job,
            bmodel_options,
            export_id=export_row.id,
            rect_preset_slug=preset_slug,
            quality_policy_snapshot=policy_snapshot,
            evidence_comparison=evidence_comparison,
            allow_quality_risk=True,
        )
        compile_block_reason = _compile_contract_block_reason(export_row, edge_validation)
        if compile_block_reason:
            for option in bmodel_options:
                option.publishable = False
                option.recommended = False
                option.blocked_reason = compile_block_reason
                option.quality_warning = compile_block_reason
        preferred_option = next(
            (option for option in bmodel_options if option.recommended),
            bmodel_options[0],
        )
        _, preferred_comparison, _ = _bmodel_eval_gate_result(
            export_row.id,
            precision=preferred_option.precision,
            rect_preset_slug=preset_slug,
            quality_policy_snapshot=policy_snapshot,
            evidence_comparison=evidence_comparison,
        )
        # This endpoint feeds the authenticated manual publication workspace.
        # Edge and quality states are evidence for the operator, not blockers.
        publish_blocked_reason = compile_block_reason
        assessment_payload = build_assessment_payload(
            training_val_metrics=train_m,
            training_assessment_override=(
                yolo_train_service._composite_training_assessment_for_job(job)
            ),
            test_val_metrics=yolo_train_service._test_val_metrics_for_job(job),
            val_test_comparison=yolo_train_service._val_test_comparison_for_job(job),
            total_sample_count=yolo_train_service._sample_counts_for_job(job).get(
                "total"
            ),
            edge_validation=edge_validation,
            publish_blocked_reason=publish_blocked_reason,
            rect_preset_slug=preset_slug,
            pt_to_bmodel_comparison=(
                preferred_comparison
                or bm_export_service._effective_pt_bmodel_comparison(
                    export_row,
                    edge_validation,
                )
            ),
            pt_to_bmodel_comparison_expected=True,
            quality_policy_snapshot=policy_snapshot,
        )
        try:
            export_manifest = json.loads(
                getattr(export_row, "artifact_manifest_json", None) or "{}"
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            export_manifest = {}
        publish_gate_reason_codes = _publish_gate_reason_codes(
            edge_validation=edge_validation,
            validation_pass=vp,
            artifact_manifest=export_manifest,
            export_id=int(export_row.id),
        )
        if compile_block_reason:
            publish_gate_reason_codes.append("compile_contract_blocked")
        publish_gate_reason_codes = list(dict.fromkeys(publish_gate_reason_codes))
        if not publish_blocked_reason and publish_gate_reason_codes:
            publish_blocked_reason = (
                "发布前检查未通过："
                + "、".join(publish_gate_reason_codes[:5])
            )
        publish_gate_status = _publish_gate_status(
            reason_codes=publish_gate_reason_codes,
            release_gate_assessment=assessment_payload.release_gate_assessment,
            quality_assessment=assessment_payload.quality_assessment,
        )
        out.append(
            AicmPublishableRowResponse(
                export_id=export_row.id,
                strategy_task=_strategy_task_marker_for_export(db, export_row.id),
                yolo_train_job_id=job.id,
                task_id=task.id,
                task_name=task.name,
                dataset_category_id=dataset_category_fields["dataset_category_id"],
                dataset_category_key=dataset_category_fields["dataset_category_key"],
                dataset_category_name=dataset_category_fields["dataset_category_name"],
                **contract_fields,
                publish_template_key=publish_template_key,
                publish_template_name=preset_name,
                publish_template_source=publish_template_source,
                legacy_preset_fallback=False,
                category=getattr(job, "category", None) or "",
                rect_preset_name=preset_name,
                rect_preset_slug=preset_slug,
                imgsz=int(export_row.imgsz or job.imgsz or 640),
                bmodel_filename=preferred_option.filename,
                bmodel_options=bmodel_options,
                default_bmodel_rel_path=_default_bmodel_rel_path(bmodel_options),
                validation_pass=vp,
                training_assessment=assessment_payload.training_assessment,
                edge_validation_assessment=assessment_payload.edge_validation_assessment,
                release_gate_assessment=assessment_payload.release_gate_assessment,
                quality_assessment=assessment_payload.quality_assessment,
                map50_95=train_m.map50_95 if train_m else None,
                map50=train_m.map50 if train_m else None,
                export_created_at=export_row.created_at,
                job_created_at=job.created_at,
                creator_email=(
                    creators.get(int(job.created_by)) if job.created_by else None
                ),
                template_cached=template_cached,
                edge_validation=edge_validation,
                publish_gate_status=publish_gate_status,
                publish_gate_reason_codes=publish_gate_reason_codes,
                publish_blocked_reason=publish_blocked_reason,
            )
        )
    if str(search or "").strip():
        out = [row for row in out if _publish_row_matches_search(row, search)]
    total = len(out)
    start = page * size
    return out[start : start + size], total


def _config_incomplete_message(cfg: dict[str, Any]) -> str:
    parts = [
        "AICM 发布环境不完整，请在发布环境页面补齐连接信息和密码。",
        "配置来源：MySQL system_config_value。",
    ]
    if not (cfg.get("base_url") or "").strip():
        parts.append("缺少 base_url。")
    if not (cfg.get("username") or "").strip():
        parts.insert(1, "缺少 username。")
    if not (cfg.get("password") or "").strip():
        parts.insert(1, "缺少 password。")
    return " ".join(parts)


def publish_to_aicm(
    db: Session,
    current_user: User,
    *,
    export_id: int,
    publish_cmd: Optional[AicmPublishRequestCommand] = None,
    progress: Optional[PublishJobTracker] = None,
    environment_id: Optional[str] = None,
    publish_mode: str = "automatic",
) -> AicmPublishResultResponse:
    normalized_publish_mode = (
        "manual"
        if str(publish_mode or "").strip().lower() == "manual"
        else "automatic"
    )
    env_id = (environment_id or "").strip()
    if publish_cmd and publish_cmd.environment_id:
        env_id = env_id or str(publish_cmd.environment_id).strip()
    if not env_id:
        env_id = get_active_environment_id()
    steps: List[str] = []
    if progress is not None:
        progress.start("validate", f"export_id={export_id}")

    row = crud_yolo_bm1688_export.get(db, export_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    task = crud_task.get(db=db, task_id=row.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    from labelu.internal.application.service.yolo_bm1688_export import (
        _assert_task_access,
    )

    _assert_task_access(task, current_user)
    if not bm_export_service.is_bmodel_artifact_ready(row):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="BM1688 导出未生成可用 bmodel，无法发布",
        )
    # Manual publication is an operator trigger, not a quality override.  It
    # must consume the same completed edge evidence as the automatic path.
    edge_validation = _assert_edge_validation_completed_for_publish(
        db, export_id=export_id
    )
    compile_block_reason = _compile_contract_block_reason(row, edge_validation)
    if compile_block_reason:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=compile_block_reason,
        )
    try:
        export_manifest = json.loads(
            getattr(row, "artifact_manifest_json", None) or "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        export_manifest = {}
    _assert_model_manifest_for_publish(export_id, export_manifest)
    _assert_edge_profile_for_publish(edge_validation, export_manifest)
    job = crud_yolo_train_job.get(db, row.yolo_train_job_id)
    if not job:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    train_m = _training_val_metrics_for_job(job)
    vp = _validation_pass_row(train_m)
    evidence_comparison = bm_export_service._effective_pt_bmodel_comparison(
        row,
        edge_validation,
    )

    is_plate_job = (
        str(getattr(job, "job_type", "") or "").strip() == "plate_recognition"
    )
    required_precisions = ("fp16",) if is_plate_job else ("fp32", "fp16", "int8")
    if not bm_export_service.is_bmodel_release_ready(
        row, required_precisions=required_precisions
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="BM1688 发布制品不完整，必须具备当前模型契约要求的全部精度 BModel",
        )
    _assert_edge_evidence_for_publish(
        edge_validation,
        export_manifest,
        required_precisions=required_precisions,
    )
    runtime_template: Optional[RuntimePublishTemplate] = None
    if is_plate_job:
        runtime_template = _runtime_template_for_job(
            db, job=job, environment_id=env_id
        )
        publish_classes = _class_names_for_publish(job)
        _validate_runtime_template_capability(runtime_template, job, publish_classes)
        preset_name = str(runtime_template.name or runtime_template.template_key)
        preset_slug = ""
    else:
        preset_name, preset_slug = resolve_rect_preset_for_task(db, task)
        if not preset_name:
            preset_name = (getattr(job, "category", None) or "").strip() or task.name

    _log_step(
        steps,
        f"开始{('人工' if normalized_publish_mode == 'manual' else '自动')}发布 "
        f"export_id={export_id}，任务「{task.name}」，发布包「YOLOv11 标准包」",
        progress,
    )
    if progress is not None:
        progress.ok(
            "validate",
            f"标准包 · 验证：{vp.status}",
        )
    _log_step(
        steps,
        f"校验通过：BM1688 导出已完成，指标验证状态 {vp.status}",
        progress,
    )
    bm_names_preview = bm_export_service._list_bmodel_names(export_id)
    selected_precision = "fp16" if is_plate_job else ""
    selected_bmodel_rel = ""
    if publish_cmd:
        selected_precision = (
            (publish_cmd.bmodel_precision or "").strip().lower()
            or _bmodel_precision_from_filename(
                Path((publish_cmd.bmodel_rel_path or "").strip()).name
            )
        )
    if not selected_precision and not is_plate_job:
        fallback_options = _prepare_publish_bmodel_options(
            job,
            _publish_bmodel_options_for_job(job, bm_names_preview),
            export_id=export_id,
            rect_preset_slug=preset_slug,
            quality_policy_snapshot=quality_policy_snapshot_for_job(job),
            evidence_comparison=evidence_comparison,
            allow_quality_risk=normalized_publish_mode == "manual",
        )
        fallback_option = next(
            (option for option in fallback_options if option.publishable), None
        )
        if fallback_option is not None:
            selected_precision = fallback_option.precision
            selected_bmodel_rel = fallback_option.rel_path
        else:
            selected_precision = "int8"
    selected_bmodel_rel = _normalize_bmodel_selection(
        bm_names_preview,
        precision=selected_precision,
        rel_path=(
            selected_bmodel_rel
            or (publish_cmd.bmodel_rel_path if publish_cmd else None)
        ),
    )
    selected_precision = _bmodel_precision_from_filename(Path(selected_bmodel_rel).name)
    _log_step(
        steps,
        f"选用编译精度 {selected_precision}（读取导出 models/{selected_bmodel_rel}）",
        progress,
    )
    gate_notice = None
    if not is_plate_job:
        _assert_bmodel_eval_gate(
            export_id,
            precision=selected_precision,
            rect_preset_slug=preset_slug,
            quality_policy_snapshot=quality_policy_snapshot_for_job(job),
            evidence_comparison=evidence_comparison,
            allow_quality_risk=normalized_publish_mode == "manual",
        )
        gate_notice = _bmodel_eval_gate_notice(
            export_id,
            precision=selected_precision,
            rect_preset_slug=preset_slug,
            quality_policy_snapshot=quality_policy_snapshot_for_job(job),
            evidence_comparison=evidence_comparison,
        )
    if gate_notice:
        _log_step(
            steps,
            f"边端 {selected_precision.upper()} 验证完成，存在精度提示（不阻断发布）：{gate_notice}",
            progress,
        )
    elif not is_plate_job:
        _log_step(
            steps,
            f"边端 {selected_precision.upper()} mAP50 掉点满足当前场景质量标准",
            progress,
        )

    if progress is not None:
        progress.start("config", "读取发布环境配置")
    if not env_id or not find_environment_record(env_id):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请先配置并选择有效的 AICM 发布环境",
        )
    cfg = load_aicm_publish_config(environment_id=env_id)
    if not config_is_complete(cfg):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=_config_incomplete_message(cfg),
        )
    env_rec = find_environment_record(env_id) or {}
    env_label = str(env_rec.get("name") or env_id)
    _log_step(
        steps,
        f"使用环境「{env_label}」：{cfg.get('base_url')}（MySQL）",
        progress,
    )
    if progress is not None:
        progress.ok("config", str(cfg.get("base_url") or ""))

    model_set_id: Optional[int] = None
    upload_text: Optional[str] = None
    package_path: Optional[str] = None
    upload_name: Optional[str] = None

    verify = bool(cfg.get("verify_ssl"))
    chip_type = str(cfg.get("chip_type") or DEFAULT_CHIP_TYPE)
    algorithm_type = str(cfg.get("algorithm_type") or DEFAULT_ALGORITHM_TYPE)

    try:
        base = _normalize_base_url(cfg.get("base_url") or "")
        if progress is not None:
            progress.start("login", f"POST {base}/v2/login/")
        sess = _aicm_session(cfg)
        if progress is not None:
            progress.ok("login", "session 已建立")
        _log_step(steps, f"AICM 登录成功（{base}/v2/login/）", progress)
        template_zip: Optional[Path] = None
        if is_plate_job and runtime_template is not None:
            use_template_cache = bool(
                publish_cmd and getattr(publish_cmd, "keep_template_cache", False)
            )
            template_zip, _template_model_set_id = _ensure_runtime_template_zip(
                sess,
                base,
                runtime_template,
                verify=verify,
                steps=steps,
                progress=progress,
                use_cache=use_template_cache,
            )
            if progress is not None:
                progress.update_metadata(
                    publish_template_id=int(runtime_template.id),
                    publish_template_version=int(runtime_template.version or 1),
                    publish_template_sha256=_file_content_sha256(template_zip),
                    publish_template_model_set_id=int(_template_model_set_id),
                )
        elif progress is not None:
            progress.skip("search", "标准 YOLOv11 包无需搜索 AICM 模板")
            progress.skip("template", "标准 YOLOv11 包由系统直接生成")
        package_zip = _build_publish_package(
            template_zip,
            export_id=export_id,
            job=job,
            db=db,
            task=task,
            preset_name=preset_name,
            preset_slug=preset_slug,
            steps=steps,
            chip_type=chip_type,
            bmodel_precision=selected_precision,
            bmodel_rel_path=selected_bmodel_rel,
            progress=progress,
        )
        package_path = str(package_zip)
        if progress is not None:
            progress.start("upload", "上传模型包")
        correlation_name = _build_upload_correlation_name(export_id)
        latest_before_upload = _fetch_latest_model_set(sess, base, verify=verify)
        latest_before_upload_id = _model_set_row_id(latest_before_upload)
        _log_step(
            steps,
            f"已记录上传前最新模型集 id={latest_before_upload_id}（仅查询第一页）",
            progress,
        )
        _log_step(
            steps,
            f"上传模型包（临时名称 {correlation_name}，{len(correlation_name)}/"
            f"{AICM_MODEL_NAME_MAX_LENGTH} 字符）· chip_type={chip_type} · "
            f"algorithm_type={algorithm_type} · 文件 {package_zip.name}",
            progress,
        )
        package_size_mb = package_zip.stat().st_size / (1024 * 1024)
        _log_step(steps, "正在 POST /v2/model-set/ upload_model…", progress)
        _log_step(
            steps,
            f"流式上传 {package_size_mb:.2f} MB（连接超时 "
            f"{AICM_HTTP_TIMEOUT[0]} 秒，发送/读取超时 "
            f"{int(AICM_UPLOAD_WRITE_TIMEOUT_SECONDS)} 秒）",
            progress,
        )
        upload_text = _upload_model_package(
            sess,
            base,
            package_zip,
            name=correlation_name,
            chip_type=chip_type,
            algorithm_type=algorithm_type,
            verify=verify,
        )
        _log_step(steps, f"上传完成：{(upload_text or '')[:120]}", progress)
        if progress is not None:
            progress.ok("upload", (upload_text or "")[:200])
        ok = _aicm_response_ok(upload_text)
        err_detail = _aicm_upload_error_message(upload_text)
        if not ok:
            logger.warning("aicm upload response: {}", upload_text[:500])
            return AicmPublishResultResponse(
                ok=False,
                export_id=export_id,
                precision=selected_precision,
                bmodel_rel_path=selected_bmodel_rel,
                rect_preset_name=preset_name,
                model_set_id=model_set_id,
                upload_response=(upload_text or "")[:2000],
                package_path=package_path,
                steps=steps,
                error_message=err_detail,
            )

        if progress is not None:
            progress.start("rename", "edit_name")
        _log_step(
            steps,
            f"获取本次上传的 AICM 模型 ID（优先读取上传响应，临时名称 {correlation_name} 仅作兼容兜底）…",
            progress,
        )
        uploaded_model_set_id, locate_source = _locate_uploaded_model_set_id_for_publish(
            sess,
            base,
            upload_text,
            correlation_name=correlation_name,
            before_id=latest_before_upload_id,
            verify=verify,
        )
        model_set_id = uploaded_model_set_id
        _log_step(
            steps,
            f"已获取本次上传 AICM 模型 ID={uploaded_model_set_id} "
            f"（依据 {locate_source}，上传前最新 id={latest_before_upload_id}）",
            progress,
        )
        task_name_for_aicm = _sanitize_upload_name_segment(str(task.name or "model"))[
            :AICM_MODEL_NAME_MAX_LENGTH
        ] or "model"
        _log_step(
            steps,
            f"使用训练任务名称重命名 AICM 模型：{task_name_for_aicm!r} "
            f"（原任务名称 {str(task.name or 'model')!r}）",
            progress,
        )
        display_name, publish_seq, version_seg, rename_text = (
            _rename_model_set_with_unique_retry(
                sess,
                base,
                uploaded_model_set_id,
                preset_name=preset_name,
                verify=verify,
                progress=progress,
                steps=steps,
                target_name=task_name_for_aicm,
            )
        )
        rename_ok = bool(display_name) and _aicm_response_ok(rename_text)
        if not rename_ok:
            err_detail = _aicm_response_error_message(rename_text, "重命名")
            logger.warning("aicm edit_name response: {}", rename_text[:500])
            if progress is not None:
                progress.fail(err_detail, failed_step="rename")
            return AicmPublishResultResponse(
                ok=False,
                export_id=export_id,
                precision=selected_precision,
                bmodel_rel_path=selected_bmodel_rel,
                rect_preset_name=preset_name,
                model_set_id=uploaded_model_set_id,
                upload_name=display_name,
                upload_response=(upload_text or "")[:2000],
                package_path=package_path,
                steps=steps,
                error_message=err_detail,
            )
        _log_step(
            steps,
            (
                "AICM 上传与重命名已完成；最终 ZIP 由 LabelU 本地模型目录"
                "独立保存，不以 AICM 下载地址作为成功门禁"
            ),
            progress,
        )
        if progress is not None:
            progress.ok("rename", display_name)

        return AicmPublishResultResponse(
            ok=True,
            export_id=export_id,
            precision=selected_precision,
            bmodel_rel_path=selected_bmodel_rel,
            model_id=str(uploaded_model_set_id),
            rect_preset_name=preset_name,
            model_set_id=uploaded_model_set_id,
            upload_name=display_name,
            upload_response=(upload_text or "")[:2000],
            package_path=package_path,
            steps=steps,
            error_message=None,
        )
    except LabelUException as e:
        _log_step(steps, str(e.msg or e), progress)
        if progress is not None:
            progress.fail(
                str(e.msg or e), failed_step=progress._data.get("current_step")
            )
        return AicmPublishResultResponse(
            ok=False,
            export_id=export_id,
            precision=selected_precision,
            bmodel_rel_path=selected_bmodel_rel,
            rect_preset_name=preset_name,
            model_set_id=model_set_id,
            upload_response=upload_text,
            package_path=package_path,
            steps=steps,
            error_message=str(e.msg or e),
        )
    except (requests.RequestException, httpx.HTTPError) as e:
        base_hint = ""
        try:
            base_hint = _normalize_base_url(cfg.get("base_url") or "")
        except LabelUException:
            base_hint = str(cfg.get("base_url") or "")
        failed_stage = (
            str(progress._data.get("current_step") or "") if progress is not None else ""
        )
        msg = _format_aicm_network_error(e, base_hint, stage=failed_stage)
        _log_step(steps, msg, progress)
        if progress is not None:
            progress.fail(msg)
        logger.exception("aicm publish export_id={}", export_id)
        return AicmPublishResultResponse(
            ok=False,
            export_id=export_id,
            precision=selected_precision,
            bmodel_rel_path=selected_bmodel_rel,
            rect_preset_name=preset_name,
            model_set_id=model_set_id,
            upload_response=upload_text,
            package_path=package_path,
            steps=steps,
            error_message=msg,
        )
    except Exception as e:
        msg = str(e)
        _log_step(steps, msg, progress)
        if progress is not None:
            progress.fail(msg)
        logger.exception("aicm publish export_id={}", export_id)
        return AicmPublishResultResponse(
            ok=False,
            export_id=export_id,
            precision=selected_precision,
            bmodel_rel_path=selected_bmodel_rel,
            rect_preset_name=preset_name,
            model_set_id=model_set_id,
            upload_response=upload_text,
            package_path=package_path,
            steps=steps,
            error_message=msg,
        )


def _publish_success(raw: dict[str, Any]) -> bool:
    st = str(raw.get("status") or "")
    if st != "COMPLETED":
        return False
    r = raw.get("result")
    if isinstance(r, dict):
        return bool(r.get("ok"))
    return False


def _published_model_set_id_from_logs(raw: dict[str, Any]) -> Optional[int]:
    texts: list[str] = []
    for item in raw.get("log_lines") or []:
        if isinstance(item, str):
            texts.append(item)
    for item in raw.get("steps") or []:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            for key in ("detail", "label"):
                value = item.get(key)
                if isinstance(value, str):
                    texts.append(value)
    result = raw.get("result")
    if isinstance(result, dict):
        for item in result.get("steps") or []:
            if isinstance(item, str):
                texts.append(item)

    for text in reversed(texts):
        match = re.search(r"(?:最新模型集|已定位上传模型集)\s*id\s*=\s*(\d+)", text)
        if not match:
            continue
        try:
            return int(match.group(1))
        except ValueError:
            continue
    return None


def _published_model_set_id(raw: dict[str, Any]) -> Optional[int]:
    log_value = _published_model_set_id_from_logs(raw)
    if log_value:
        return log_value
    result = raw.get("result")
    if isinstance(result, dict):
        for key in ("model_id", "model_set_id"):
            value = result.get(key)
            if value is None:
                continue
            text = str(value).strip()
            match = re.search(r"\d+", text)
            if not match:
                continue
            try:
                return int(match.group(0))
            except ValueError:
                continue
    return None


def _normalize_publish_result(raw: dict[str, Any]) -> dict[str, Any]:
    result = raw.get("result")
    if not isinstance(result, dict):
        return {}
    normalized = dict(result)
    published_id = _published_model_set_id(raw)
    if published_id:
        normalized["model_set_id"] = published_id
        normalized["model_id"] = str(published_id)
    return normalized


def _json_loads_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _load_export_threshold_payloads(
    db: Session, *, export_id: int
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if int(export_id or 0) <= 0:
        return None, None, None
    export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    if not export_row:
        return None, None, None
    business = _json_loads_dict(getattr(export_row, "business_thresholds_json", None))
    validation = _json_loads_dict(
        getattr(export_row, "validation_thresholds_json", None)
    )
    binding = _json_loads_dict(getattr(export_row, "threshold_binding_json", None))
    return (
        business or None,
        validation or None,
        binding or None,
    )


def _strategy_task_marker_for_export(
    db: Session, export_id: int
) -> dict[str, str] | None:
    if int(export_id or 0) <= 0:
        return None
    return strategy_task_marker(
        crud_strategy_task_integration.get_by_related_export_id(
            db,
            export_id=int(export_id),
        )
    )


def _dataset_category_fields_for_export(
    db: Session,
    export_id: int,
) -> dict[str, Any]:
    empty = {
        "dataset_category_id": None,
        "dataset_category_key": None,
        "dataset_category_name": None,
    }
    if db is None or int(export_id or 0) <= 0:
        return empty
    export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    train_job_id = getattr(export_row, "yolo_train_job_id", None)
    if export_row is None or train_job_id is None:
        return empty
    job = crud_yolo_train_job.get(db, int(train_job_id))
    if job is None:
        return empty
    return yolo_train_service._training_task_response_fields(db, job, None)


def _dataset_category_fields_for_publish_job(
    db: Session,
    raw: dict[str, Any],
) -> dict[str, Any]:
    stored_id = raw.get("dataset_category_id")
    fields = {
        "dataset_category_id": int(stored_id) if stored_id is not None else None,
        "dataset_category_key": str(raw.get("dataset_category_key") or "").strip() or None,
        "dataset_category_name": str(raw.get("dataset_category_name") or "").strip() or None,
    }
    if all(fields.values()):
        return fields
    fallback = _dataset_category_fields_for_export(
        db, int(raw.get("export_id") or 0)
    )
    return {
        key: fields.get(key) or fallback.get(key)
        for key in fields
    }


def _contract_fields_for_publish_job(
    db: Session | None,
    raw: dict[str, Any],
) -> dict[str, Any]:
    keys = (
        "class_names",
        "label_set_signature",
        "class_order_signature",
        "data_yaml_sha256",
        "dataset_content_signature",
        "training_recipe_key",
        "training_recipe_version",
        "runtime_profile",
        "runtime_interface_version",
    )
    fields = {key: raw.get(key) for key in keys}
    if not isinstance(fields["class_names"], list):
        fields["class_names"] = []
    export_fields: dict[str, Any] = {}
    if db is not None:
        export_row = crud_yolo_bm1688_export.get(
            db, int(raw.get("export_id") or 0)
        )
        train_job_id = getattr(export_row, "yolo_train_job_id", None)
        if train_job_id is not None:
            train_job = crud_yolo_train_job.get(db, int(train_job_id))
            if train_job is not None:
                export_fields = yolo_train_service._job_contract_response_fields(
                    train_job
                )
    resolved = {
        key: fields.get(key) or export_fields.get(key)
        for key in keys
    }
    resolved["class_names"] = (
        list(fields["class_names"])
        if fields["class_names"]
        else list(export_fields.get("class_names") or [])
    )
    return resolved


def _job_to_history_row(
    db: Session,
    raw: dict[str, Any],
) -> AicmPublishHistoryRowResponse:
    st = str(raw.get("status") or "PROCESSING")
    err = raw.get("error_message")
    r = _normalize_publish_result(raw)
    if not err and isinstance(r, dict) and r.get("error_message"):
        err = r.get("error_message")
    published_model_set_id = _published_model_set_id(raw)
    upload_name = str(r.get("upload_name") or "") if isinstance(r, dict) else ""
    export_id = int(raw.get("export_id") or 0)
    business_thresholds, validation_thresholds, threshold_binding = (
        _load_export_threshold_payloads(db, export_id=export_id)
    )
    dataset_category_fields = _dataset_category_fields_for_publish_job(db, raw)
    contract_fields = _contract_fields_for_publish_job(db, raw)
    return AicmPublishHistoryRowResponse(
        job_id=str(raw.get("job_id") or ""),
        export_id=export_id,
        strategy_task=_strategy_task_marker_for_export(db, export_id),
        model_id=str(published_model_set_id) if published_model_set_id else None,
        model_set_id=published_model_set_id,
        upload_name=upload_name or None,
        task_id=raw.get("task_id"),
        task_name=str(raw.get("task_name") or ""),
        dataset_category_id=dataset_category_fields["dataset_category_id"],
        dataset_category_key=dataset_category_fields["dataset_category_key"],
        dataset_category_name=dataset_category_fields["dataset_category_name"],
        **contract_fields,
        publish_template_key=(str(raw.get("publish_template_key") or "").strip() or None),
        publish_template_name=(str(raw.get("publish_template_name") or raw.get("preset_name") or "").strip() or None),
        publish_template_source=(str(raw.get("publish_template_source") or "").strip() or None),
        publish_template_id=raw.get("publish_template_id"),
        publish_template_version=raw.get("publish_template_version"),
        publish_template_sha256=(str(raw.get("publish_template_sha256") or "").strip() or None),
        publish_template_model_set_id=raw.get("publish_template_model_set_id"),
        preset_name=str(raw.get("preset_name") or ""),
        status=st,
        success=_publish_success(raw),
        publisher_username=str(raw.get("publisher_username") or ""),
        publisher_user_id=(
            int(raw["user_id"]) if raw.get("user_id") is not None else None
        ),
        publish_mode=str(raw.get("publish_mode") or "automatic"),
        quality_gate_status=(
            str(raw.get("quality_gate_status") or "").strip() or None
        ),
        quality_risk_snapshot=(
            raw.get("quality_risk_snapshot")
            if isinstance(raw.get("quality_risk_snapshot"), dict)
            else None
        ),
        business_thresholds=business_thresholds,
        validation_thresholds=validation_thresholds,
        threshold_binding=threshold_binding,
        error_message=str(err)[:500] if err else None,
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
    )


def list_publish_history(
    db: Session,
    current_user: User,
    *,
    project_id: Optional[int] = None,
    dataset_category_id: Optional[int] = None,
    rect_preset_slug: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 0,
    size: int = 20,
) -> Tuple[List[AicmPublishHistoryRowResponse], int]:
    project_id = _assert_project_access(db, current_user, project_id)
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    task_ids_filter = yolo_train_service._task_ids_for_rect_preset_filter(
        db, current_user, rect_preset_slug=rect_preset_slug
    )
    uid: Optional[int] = None
    if project_id is None and not is_task_superuser(current_user):
        uid = int(current_user.id)
    rows, _ = list_jobs(user_id=uid, page=0, size=10000)
    visible_rows: list[dict[str, Any]] = []
    for row in rows:
        task = _publish_raw_task(db, row)
        if task is None:
            # A malformed/very old JSON record cannot be tied to a tenant and
            # must never appear in an explicit project view.
            if project_id is not None:
                continue
        elif project_id is None and not is_task_superuser(current_user):
            try:
                assert_user_has_task_access(task, current_user)
            except LabelUException:
                continue
        if project_id is not None and not _publish_raw_in_project(
            db, row, project_id
        ):
            continue
        visible_rows.append(row)
    rows = visible_rows
    if task_ids_filter is not None:
        allowed = {int(tid) for tid in task_ids_filter}
        rows = [row for row in rows if int(row.get("task_id") or 0) in allowed]
    if dataset_category_id is not None:
        rows = [
            row
            for row in rows
            if _dataset_category_fields_for_publish_job(db, row)[
                "dataset_category_id"
            ]
            == int(dataset_category_id)
        ]
    # Convert before pagination so the search covers the same fields displayed
    # by the UI (including frozen labels and legacy fallback metadata).
    response_rows = [_job_to_history_row(db, raw) for raw in rows]
    if str(search or "").strip():
        response_rows = [
            row for row in response_rows if _publish_row_matches_search(row, search)
        ]
    total = len(response_rows)
    start = page * size
    return response_rows[start : start + size], total


def _job_to_response(db: Session, raw: dict[str, Any]) -> AicmPublishJobResponse:
    result = None
    r = _normalize_publish_result(raw)
    export_id = int(raw.get("export_id") or 0)
    dataset_category_fields = _dataset_category_fields_for_publish_job(db, raw)
    contract_fields = _contract_fields_for_publish_job(db, raw)
    business_thresholds, validation_thresholds, threshold_binding = (
        _load_export_threshold_payloads(db, export_id=export_id)
    )
    if r:
        result_payload = dict(r)
        if business_thresholds:
            result_payload["business_thresholds"] = business_thresholds
        if validation_thresholds:
            result_payload["validation_thresholds"] = validation_thresholds
        if threshold_binding:
            result_payload["threshold_binding"] = threshold_binding
        result = AicmPublishResultResponse(**result_payload)
    step_rows = [
        AicmPublishStepItem(**s)
        for s in (raw.get("steps") or [])
        if isinstance(s, dict)
    ]
    return AicmPublishJobResponse(
        job_id=str(raw.get("job_id") or ""),
        export_id=export_id,
        strategy_task=_strategy_task_marker_for_export(db, export_id),
        status=str(raw.get("status") or "PROCESSING"),
        current_step=raw.get("current_step"),
        preset_name=str(raw.get("preset_name") or ""),
        task_id=raw.get("task_id"),
        task_name=str(raw.get("task_name") or ""),
        dataset_category_id=dataset_category_fields["dataset_category_id"],
        dataset_category_key=dataset_category_fields["dataset_category_key"],
        dataset_category_name=dataset_category_fields["dataset_category_name"],
        **contract_fields,
        publish_template_key=(str(raw.get("publish_template_key") or "").strip() or None),
        publish_template_name=(str(raw.get("publish_template_name") or raw.get("preset_name") or "").strip() or None),
        publish_template_source=(str(raw.get("publish_template_source") or "").strip() or None),
        publish_template_id=raw.get("publish_template_id"),
        publish_template_version=raw.get("publish_template_version"),
        publish_template_sha256=(str(raw.get("publish_template_sha256") or "").strip() or None),
        publish_template_model_set_id=raw.get("publish_template_model_set_id"),
        publisher_username=str(raw.get("publisher_username") or ""),
        publisher_user_id=(
            int(raw["user_id"]) if raw.get("user_id") is not None else None
        ),
        publish_mode=str(raw.get("publish_mode") or "automatic"),
        quality_gate_status=(
            str(raw.get("quality_gate_status") or "").strip() or None
        ),
        quality_risk_snapshot=(
            raw.get("quality_risk_snapshot")
            if isinstance(raw.get("quality_risk_snapshot"), dict)
            else None
        ),
        steps=step_rows,
        log_lines=list(raw.get("log_lines") or []),
        process_log_path=raw.get("process_log_path"),
        result=result,
        error_message=raw.get("error_message"),
        created_at=raw.get("created_at"),
        updated_at=raw.get("updated_at"),
    )


def get_publish_job(
    db: Session,
    current_user: User,
    *,
    job_id: str,
    project_id: Optional[int] = None,
) -> AicmPublishJobResponse:
    project_id = _assert_project_access(db, current_user, project_id)
    raw = load_job(job_id)
    if not raw:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"发布任务不存在：{job_id}",
        )
    owner = int(raw.get("user_id") or 0)
    if (
        project_id is None
        and owner != int(current_user.id)
        and not is_task_superuser(current_user)
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    task = _publish_raw_task(db, raw)
    if task is not None and not is_task_superuser(current_user):
        assert_user_has_task_access(task, current_user)
    if project_id is not None and not _publish_raw_in_project(db, raw, project_id):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="发布任务不属于当前项目",
        )
    return _job_to_response(db, raw)


def _run_publish_job_sync(
    job_id: str,
    export_id: int,
    user_id: int,
    environment_id: str = "",
    keep_template_cache: bool = False,
    bmodel_precision: Optional[str] = None,
    bmodel_rel_path: Optional[str] = None,
    publish_mode: str = "automatic",
    publish_lease_owner: str = "",
    publish_fencing_token: Optional[int] = None,
) -> None:
    from labelu.internal.adapter.persistence import crud_user
    from labelu.internal.common.db import begin_transaction, session_scope

    progress = PublishJobTracker(job_id)

    def _finish_claim(status_value: str, model_set_id: Optional[int] = None) -> bool:
        if publish_fencing_token is None:
            return True
        with session_scope() as claim_db:
            with begin_transaction(claim_db):
                return crud_yolo_bm1688_export.finish_publish_claim(
                    claim_db,
                    export_id=export_id,
                    job_id=job_id,
                    lease_owner=publish_lease_owner,
                    fencing_token=int(publish_fencing_token),
                    status=status_value,
                    published_model_id=model_set_id,
                )

    lease_monitor: Optional[_AicmPublishLeaseMonitor] = None
    try:
        if publish_fencing_token is not None:
            lease_monitor = _AicmPublishLeaseMonitor(
                export_id=export_id,
                job_id=job_id,
                lease_owner=publish_lease_owner,
                fencing_token=int(publish_fencing_token),
                lease_seconds=AICM_PUBLISH_CLAIM_LEASE_SECONDS,
                session_scope_factory=session_scope,
            ).start()
            lease_monitor.ensure_current()
        with session_scope() as db:
            user = crud_user.get(db, user_id)
            if not user:
                progress.fail("用户不存在")
                _finish_claim("FAILED")
                return
            result = publish_to_aicm(
                db,
                user,
                export_id=export_id,
                progress=progress,
                environment_id=environment_id or None,
                publish_cmd=AicmPublishRequestCommand(
                    export_id=export_id,
                    environment_id=environment_id or None,
                    keep_template_cache=keep_template_cache,
                    bmodel_precision=bmodel_precision,
                    bmodel_rel_path=bmodel_rel_path,
                ),
                publish_mode=publish_mode,
            )
            if lease_monitor is not None:
                lease_monitor.ensure_current()
            if result.ok and result.model_set_id:
                with begin_transaction(db):
                    export_row = crud_yolo_bm1688_export.get(db, export_id)
                    if export_row:
                        from labelu.internal.application.service import (
                            published_model_catalog,
                        )

                        published_model_catalog.register_published_model(
                            db,
                            export_id=export_id,
                            model_set_id=int(result.model_set_id),
                            environment_id=environment_id,
                            precision=str(result.precision or ""),
                            publish_job_id=job_id,
                            upload_name=str(result.upload_name or ""),
                            publisher_username=str(user.username or ""),
                            published_by_id=int(user.id),
                            package_path=str(result.package_path or ""),
                            package_required=True,
                        )
                        if publish_fencing_token is not None:
                            finished = crud_yolo_bm1688_export.finish_publish_claim(
                                db,
                                export_id=export_id,
                                job_id=job_id,
                                lease_owner=publish_lease_owner,
                                fencing_token=int(publish_fencing_token),
                                status="COMPLETED",
                                published_model_id=int(result.model_set_id),
                            )
                            if not finished:
                                raise LabelUException(
                                    code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                                    message="发布任务已失去领取租约，拒绝写入完成状态",
                                )
                        else:
                            crud_yolo_bm1688_export.update_row(
                                db,
                                export_row,
                                published_model_id=int(result.model_set_id),
                            )
            elif publish_fencing_token is not None and not _finish_claim("FAILED"):
                raise _AicmPublishLeaseLost(
                    "AICM 发布失败结果无法写入，租约已失效"
                )
        progress.complete(result.dict())
    except LabelUException as e:
        if publish_fencing_token is not None:
            try:
                _finish_claim("FAILED")
            except Exception:
                logger.exception("failed to release AICM publish claim job_id={}", job_id)
        progress.fail(str(e.msg or e), failed_step=progress._data.get("current_step"))
    except Exception as e:
        logger.exception("aicm publish job_id={}", job_id)
        if publish_fencing_token is not None:
            try:
                _finish_claim("FAILED")
            except Exception:
                logger.exception("failed to release AICM publish claim job_id={}", job_id)
        progress.fail(str(e))
    finally:
        if lease_monitor is not None:
            lease_monitor.stop()


async def start_publish_job(
    db: Session,
    current_user: User,
    *,
    export_id: int,
    environment_id: Optional[str] = None,
    keep_template_cache: bool = False,
    bmodel_precision: Optional[str] = None,
    bmodel_rel_path: Optional[str] = None,
    publish_mode: str = "automatic",
) -> AicmPublishJobResponse:
    """创建后台发布任务并立即返回 job_id，前端轮询 GET /publish-jobs/{job_id}。"""
    normalized_publish_mode = (
        "manual"
        if str(publish_mode or "").strip().lower() == "manual"
        else "automatic"
    )
    row = crud_yolo_bm1688_export.get(db, export_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    task = crud_task.get(db=db, task_id=row.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    from labelu.internal.application.service.yolo_bm1688_export import (
        _assert_task_access,
    )

    _assert_task_access(task, current_user)
    # Both automatic and manual triggers are subject to the same release
    # evidence. Manual means a human initiated the action; it does not mean
    # missing board/quality evidence may be overridden.
    edge_validation = _assert_edge_validation_completed_for_publish(
        db, export_id=export_id
    )
    train_job = crud_yolo_train_job.get(db, row.yolo_train_job_id)
    if train_job is None:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    is_plate_job = (
        str(getattr(train_job, "job_type", "") or "").strip()
        == "plate_recognition"
    )
    required_precisions = ("fp16",) if is_plate_job else ("fp32", "fp16", "int8")
    if not bm_export_service.is_bmodel_release_ready(
        row, required_precisions=required_precisions
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="BM1688 发布制品不完整，必须具备当前模型契约要求的全部精度 BModel",
        )
    try:
        export_manifest = json.loads(
            getattr(row, "artifact_manifest_json", None) or "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        export_manifest = {}
    _assert_model_manifest_for_publish(export_id, export_manifest)
    _assert_edge_profile_for_publish(edge_validation, export_manifest)
    _assert_edge_evidence_for_publish(
        edge_validation,
        export_manifest,
        required_precisions=required_precisions,
    )
    contract_fields = yolo_train_service._job_contract_response_fields(train_job)
    dataset_category_fields = _dataset_category_fields_for_export(db, export_id)

    repair_stored_config_if_needed()
    env_id = (environment_id or "").strip() or get_active_environment_id()
    cfg = load_aicm_publish_config(environment_id=env_id)
    if not config_is_complete(cfg):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=_config_incomplete_message(cfg),
        )
    env_rec = find_environment_record(env_id) or {}
    env_name = str(env_rec.get("name") or env_id)
    runtime_template: Optional[RuntimePublishTemplate] = None
    if is_plate_job:
        runtime_template = _runtime_template_for_job(
            db,
            job=train_job,
            environment_id=env_id,
        )
        _validate_runtime_template_capability(
            runtime_template,
            train_job,
            _class_names_for_publish(train_job),
        )
        preset_name = str(runtime_template.name or runtime_template.template_key)
        publish_template_key = str(runtime_template.template_key)
        publish_template_name = preset_name
        publish_template_source = "runtime_profile"
    else:
        preset_name, _preset_slug = resolve_rect_preset_for_task(db, task)
        if not preset_name:
            preset_name = (
                (getattr(train_job, "category", None) or "").strip() or task.name
            )
        publish_template_key = "yolov11_direct"
        publish_template_name = "YOLOv11 标准发布包"
        publish_template_source = "generated"

    selected_precision = (
        str(bmodel_precision or "").strip().lower()
        or _bmodel_precision_from_filename(Path(str(bmodel_rel_path or "")).name)
        or ("fp16" if is_plate_job else "int8")
    )
    evidence_comparison = bm_export_service._effective_pt_bmodel_comparison(
        row, edge_validation
    )
    if is_plate_job:
        quality_gate_status = "not_applicable"
        effective_comparison = None
        max_drop = None
    else:
        quality_gate_status, effective_comparison, max_drop = _bmodel_eval_gate_result(
            export_id,
            precision=selected_precision,
            rect_preset_slug=_preset_slug,
            quality_policy_snapshot=quality_policy_snapshot_for_job(train_job),
            evidence_comparison=evidence_comparison,
        )
        _assert_bmodel_eval_gate(
            export_id,
            precision=selected_precision,
            rect_preset_slug=_preset_slug,
            quality_policy_snapshot=quality_policy_snapshot_for_job(train_job),
            evidence_comparison=evidence_comparison,
            allow_quality_risk=normalized_publish_mode == "manual",
        )
    quality_risk_snapshot = {
        "publish_mode": normalized_publish_mode,
        # Kept for history-schema compatibility; manual mode is no longer a
        # quality override and cannot create a publish job on a non-PASS gate.
        "manual_quality_override": False,
        "edge_validation_status": str(
            getattr(edge_validation, "status", "") or "missing"
        ).upper(),
        "selected_precision": selected_precision,
        "quality_gate_status": quality_gate_status,
        "max_pt_to_bmodel_map50_drop": max_drop,
        "comparison": effective_comparison,
    }

    job_id = uuid.uuid4().hex[:16]
    publish_lease_owner = f"web:{uuid.uuid4().hex}"
    publish_fencing_token: Optional[int] = None
    dedicated_worker = bool(
        getattr(settings, "AICM_PUBLISH_DEDICATED_WORKER_ENABLED", False)
    )
    if hasattr(row, "publish_fencing_token") and not dedicated_worker:
        from labelu.internal.common.db import begin_transaction

        with begin_transaction(db):
            claimed_row, claim_reason = crud_yolo_bm1688_export.claim_publish(
                db,
                export_id=export_id,
                job_id=job_id,
                lease_owner=publish_lease_owner,
                lease_seconds=AICM_PUBLISH_CLAIM_LEASE_SECONDS,
            )
        if claimed_row is None:
            if claim_reason == "ALREADY_PUBLISHED":
                message = "该模型已经发布成功，不能重复发布"
            elif claim_reason == "BUSY":
                message = "该模型正在发布，请等待当前发布任务结束"
            else:
                message = "发布任务领取失败，请稍后重试"
            raise LabelUException(
                code=ErrorCode.CODE_57015_YOLO_BM1688_EXPORT_BUSY,
                status_code=status.HTTP_409_CONFLICT,
                message=message,
            )
        publish_fencing_token = int(claimed_row.publish_fencing_token or 0)

    job_id = create_job(
        export_id=export_id,
        user_id=current_user.id,
        job_id=job_id,
        preset_name=preset_name,
        publisher_username=str(getattr(current_user, "username", None) or ""),
        task_id=int(task.id),
        task_name=str(task.name or ""),
        dataset_category_id=dataset_category_fields["dataset_category_id"],
        dataset_category_key=dataset_category_fields["dataset_category_key"] or "",
        dataset_category_name=dataset_category_fields["dataset_category_name"] or "",
        class_names=contract_fields["class_names"],
        label_set_signature=contract_fields["label_set_signature"] or "",
        class_order_signature=contract_fields["class_order_signature"] or "",
        data_yaml_sha256=contract_fields["data_yaml_sha256"] or "",
        dataset_content_signature=contract_fields["dataset_content_signature"] or "",
        training_recipe_key=contract_fields["training_recipe_key"] or "",
        training_recipe_version=contract_fields["training_recipe_version"],
        runtime_profile=contract_fields["runtime_profile"] or "",
        runtime_interface_version=contract_fields["runtime_interface_version"],
        publish_template_key=publish_template_key,
        publish_template_name=publish_template_name,
        publish_template_source=publish_template_source,
        publish_template_id=(int(runtime_template.id) if runtime_template else None),
        publish_template_version=(
            int(runtime_template.version or 1) if runtime_template else None
        ),
        publish_template_sha256=(
            str(runtime_template.template_sha256 or "") if runtime_template else ""
        ),
        publish_template_model_set_id=(
            int(str(runtime_template.aicm_model_set_id)) if runtime_template else None
        ),
        environment_id=env_id,
        environment_name=env_name,
        publish_mode=normalized_publish_mode,
        keep_template_cache=keep_template_cache,
        bmodel_precision=bmodel_precision,
        bmodel_rel_path=bmodel_rel_path,
        publish_lease_owner=publish_lease_owner,
        publish_fencing_token=publish_fencing_token,
        quality_gate_status=quality_gate_status,
        quality_risk_snapshot=quality_risk_snapshot,
    )
    if dedicated_worker:
        from labelu.internal.common.db import begin_transaction

        with begin_transaction(db):
            queued = crud_yolo_bm1688_export.enqueue_publish(
                db, export_id=export_id, job_id=job_id
            )
        if not queued:
            raise LabelUException(
                code=ErrorCode.CODE_57015_YOLO_BM1688_EXPORT_BUSY,
                status_code=status.HTTP_409_CONFLICT,
                message="发布任务入队失败，模型可能已被其他任务发布",
            )
        raw = load_job(job_id)
        return _job_to_response(
            db,
            raw or {"job_id": job_id, "export_id": export_id, "status": "PROCESSING"},
        )
    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        None,
        _run_publish_job_sync,
        job_id,
        export_id,
        current_user.id,
        env_id,
        keep_template_cache,
        bmodel_precision,
        bmodel_rel_path,
        normalized_publish_mode,
        publish_lease_owner,
        publish_fencing_token,
    )
    raw = load_job(job_id)
    return _job_to_response(
        db,
        raw or {"job_id": job_id, "export_id": export_id, "status": "PROCESSING"},
    )
