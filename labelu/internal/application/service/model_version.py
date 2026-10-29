from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_algorithm,
    crud_dataset,
    crud_model_version,
    crud_published_model_catalog,
    crud_task,
    crud_yolo_bm1688_export,
    crud_yolo_imported_model,
    crud_yolo_train_job,
)
from labelu.internal.application.command.model_version import (
    CreateModelArtifactCommand,
    CreateModelQualityEvidenceCommand,
    CreateModelVersionCommand,
    ImportExternalModelCommand,
    PublishModelVersionCommand,
    RegisterTrainingJobModelCommand,
)
from labelu.internal.application.response.model_version import (
    ModelArtifactResponse,
    ModelFamilyResponse,
    ModelQualityEvidenceResponse,
    ModelVersionResponse,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.config import settings
from labelu.internal.common.model_manifest_signature import (
    sign_model_manifest,
    verify_model_manifest,
)
from labelu.internal.common.algorithm_registry import adapter_capabilities
from labelu.internal.application.service import annotation_contract
from labelu.internal.application.service.algorithm import capability_state
from labelu.internal.common.label_contract import (
    class_order_signature as build_class_order_signature,
    label_set_signature as build_label_set_signature,
)
from labelu.internal.common.training_contract import (
    branch_identity_sha256,
    canonical_selection_contract,
    selection_contract_sha256 as build_selection_contract_sha256,
)
from labelu.internal.common.task_access import (
    is_consistent_pool_admin,
    is_task_superuser,
    user_has_task_access,
)
from labelu.internal.domain.models.model_version import (
    ModelArtifact,
    ModelQualityEvidence,
    ModelVersion,
)
from labelu.internal.domain.models.user import User

GATE_TYPES = ("training", "generalization", "conversion", "edge", "release")
PUBLISHABLE_STATUS = "VALIDATED"
PT_ARTIFACT_TYPES = frozenset({"pt"})
# 一期只支持 YOLO 在 BM1688 上运行。PT/ONNX 是编译链输入，边端制品
# 必须是同一模型版本下的 FP32、FP16、INT8 三份 BModel；不再接受 RKNN。
CONVERSION_ARTIFACT_TYPES = frozenset({"onnx", "bmodel"})
DEPLOYABLE_ARTIFACT_TYPES = frozenset({"bmodel"})


def _error(
    message: str,
    code=ErrorCode.CODE_30003_CLIENT_ERROR,
    http_status=status.HTTP_404_NOT_FOUND,
):
    return LabelUException(code=code, status_code=http_status, message=message)


def _json_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise _error(
            "字段必须是有效 JSON",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        ) from exc


def _json_value(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        result = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return result if isinstance(result, dict) else None


def _dataset_annotation_kind(dataset_source: Any) -> str:
    """Resolve the current or frozen label contract kind for adapter selection."""
    contract = getattr(dataset_source, "dataset_label_contract", None) or getattr(
        dataset_source, "label_contract", None
    )
    kind = str(getattr(contract, "annotation_kind", "") or "").strip().lower()
    if kind:
        return kind
    raw = getattr(dataset_source, "labels_snapshot_json", None) or getattr(
        contract, "labels_snapshot_json", None
    )
    try:
        labels = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        labels = []
    if isinstance(labels, list) and labels:
        first = labels[0]
        if isinstance(first, dict):
            kind = str(first.get("annotation_kind") or "").strip().lower()
    return kind or "bbox"


def _validate_algorithm_dataset_compatibility(
    algorithm: Any, dataset_source: Any
) -> None:
    """Keep model registration aligned with the selected label dataset.

    A product-specific algorithm may declare an exact label-set signature.  A
    generic algorithm is checked by annotation kind so a classification or
    segmentation model cannot be registered against a bbox dataset merely by
    changing the UI payload.
    """
    contract = getattr(dataset_source, "dataset_label_contract", None) or getattr(
        dataset_source, "label_contract", None
    )
    dataset_signature = str(
        getattr(dataset_source, "label_set_signature", "")
        or getattr(contract, "label_set_signature", "")
        or ""
    ).strip()
    algorithm_signature = str(
        getattr(algorithm, "label_set_signature", "") or ""
    ).strip()
    if (
        algorithm_signature
        and dataset_signature
        and algorithm_signature != dataset_signature
    ):
        raise _error(
            "算法能力与关联数据集的标签契约不匹配",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    expected_adapters = {
        "bbox": {"yolo_detection"},
        "rect": {"yolo_detection"},
        "class_label": {"classification"},
        "classification": {"classification"},
        "polygon": {"segmentation"},
        "keypoint": {"pose"},
        "keypoints": {"pose"},
    }
    kind = _dataset_annotation_kind(dataset_source)
    allowed = expected_adapters.get(kind)
    adapter = str(getattr(algorithm, "training_adapter", "") or "").strip().lower()
    if allowed and adapter not in allowed:
        raise _error(
            f"{kind} 标签数据集不能使用当前算法能力",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )


def _model_manifest_signature_required() -> bool:
    return bool(
        getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_REQUIRED", False)
        or str(getattr(settings, "APP_ENV", "") or "").strip().lower() == "production"
    )


def _assert_model_version_manifest_ready(row: ModelVersion) -> None:
    """Validate the canonical model-version manifest before publication."""

    manifest = _json_value(row.manifest_json) or {}
    has_signature = bool(str(manifest.get("manifest_hmac_sha256") or "").strip())
    required = _model_manifest_signature_required()
    if not required and not has_signature:
        return
    secret = str(getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_SECRET", "") or "").strip()
    if not secret:
        raise _error(
            "模型版本缺少 ModelManifest 签名密钥，禁止发布",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    if str(manifest.get("model_version_id") or "") != str(row.id):
        raise _error(
            "ModelManifest 与模型版本不匹配，禁止发布",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    ok, reason = verify_model_manifest(
        manifest,
        secret=secret,
        expected_key_id=str(
            getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_KEY_ID", "v1") or "v1"
        ),
        required=True,
    )
    if not ok:
        raise _error(
            f"ModelManifest 未通过校验：{reason}",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    root = Path(settings.MEDIA_ROOT).resolve()
    for artifact in row.artifacts or []:
        if str(artifact.status).upper() != "READY":
            continue
        path = (root / str(artifact.relative_path or "")).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise _error(
                "模型制品路径超出媒体目录，禁止发布",
                ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                status.HTTP_409_CONFLICT,
            ) from exc
        if not path.is_file():
            raise _error(
                f"模型制品文件不存在：{artifact.relative_path}",
                ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                status.HTTP_409_CONFLICT,
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != str(artifact.sha256 or "").lower():
            raise _error(
                f"模型制品校验值不一致：{artifact.relative_path}",
                ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                status.HTTP_409_CONFLICT,
            )
        if artifact.size_bytes is not None and path.stat().st_size != int(
            artifact.size_bytes
        ):
            raise _error(
                f"模型制品大小不一致：{artifact.relative_path}",
                ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                status.HTTP_409_CONFLICT,
            )


def _compact_dataset_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Keep model lineage bounded while retaining reproducible dataset evidence.

    Training manifests can contain one entry per image/XML file and easily
    exceed MySQL ``TEXT`` (64 KiB).  The immutable dataset-version record
    remains the source of the full manifest; a model version only needs the
    manifest identity, split/class summary and file count for traceability.
    """

    if not isinstance(manifest, dict):
        return {}
    retained_keys = (
        "manifest_version",
        "created_at",
        "source_task_ids",
        "per_task_sample_counts",
        "split_counts",
        "class_names",
        "sample_composition",
        "file_count",
        "dataset_content_signature",
        "content_sha256",
        "manifest_sha256",
        "manifest_rel_path",
    )
    compact = {key: manifest[key] for key in retained_keys if key in manifest}
    files = manifest.get("files")
    if isinstance(files, list):
        compact["omitted_file_entries"] = len(files)
    # Keep class metadata bounded even for malformed/legacy manifests.
    class_names = compact.get("class_names")
    if isinstance(class_names, list) and len(class_names) > 512:
        compact["class_names"] = class_names[:512]
        compact["class_names_truncated"] = True
    return compact


def _model_or_raise(
    db: Session, model_version_id: int, user: User, *, manage: bool = False
) -> ModelVersion:
    row = crud_model_version.get(db, model_version_id)
    if row is None:
        raise _error("模型版本不存在")
    task = None
    if row.training_job_id:
        job = crud_yolo_train_job.get(db, int(row.training_job_id))
        task = (
            crud_task.get(db=db, task_id=int(job.task_id))
            if job and job.task_id
            else None
        )
        # A training-derived model inherits the source task's visibility.  In
        # particular, configured/service superusers must not bypass the
        # administrator-only consistent-pool boundary, while third-party
        # tasks remain visible to every authenticated task user.
        task_access = user_has_task_access(user, task)
        can_manage = task_access
        can_read = task_access
    else:
        # External registrations have no task lineage.  Keep their historical
        # repository semantics: the creator and task superusers can manage
        # them, while a published external model is readable globally.
        can_manage = is_task_superuser(user) or int(row.created_by or 0) == int(user.id)
        can_read = can_manage or str(row.status).upper() == "PUBLISHED"
    if (manage and not can_manage) or (not manage and not can_read):
        raise _error(
            "无权访问该模型版本",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    return row


def _artifact_response(row: ModelArtifact) -> ModelArtifactResponse:
    return ModelArtifactResponse(
        id=row.id,
        model_version_id=row.model_version_id,
        artifact_type=row.artifact_type,
        relative_path=row.relative_path,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        chip_type=row.chip_type,
        precision=row.precision,
        status=row.status,
        metadata=_json_value(row.metadata_json),
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _evidence_response(row: ModelQualityEvidence) -> ModelQualityEvidenceResponse:
    return ModelQualityEvidenceResponse(
        id=row.id,
        model_version_id=row.model_version_id,
        gate_type=row.gate_type,
        revision=row.revision,
        status=row.status,
        metrics=_json_value(row.metrics_json),
        evidence_uri=row.evidence_uri,
        evidence_sha256=row.evidence_sha256,
        evaluator=row.evaluator,
        reason=row.reason,
        evaluated_at=row.evaluated_at,
        created_by=row.created_by,
        created_at=row.created_at,
    )


def _gate_summary(row: ModelVersion) -> dict[str, str]:
    result = {gate: "PENDING" for gate in GATE_TYPES}
    # Relationship data can contain multiple revisions; newest revision wins.
    for evidence in sorted(
        row.quality_evidence or [], key=lambda item: (item.gate_type, item.revision)
    ):
        if evidence.gate_type in result:
            result[evidence.gate_type] = evidence.status
    return result


def _metric_block_is_ready(value: Any) -> bool:
    """Return whether a persisted evaluation block contains usable metrics.

    Training jobs store their metrics summary as a JSON document.  A quality
    gate must never be inferred from the presence of the JSON field alone:
    failed, skipped, or partially written evaluation blocks are common after
    a worker restart.  Require an explicit successful status (when present)
    and at least one numeric detection metric.
    """
    if not isinstance(value, dict):
        return False
    status = str(value.get("status") or "ok").strip().lower()
    if status not in {"ok", "pass", "passed", "success", "completed"}:
        return False
    return any(
        isinstance(value.get(key), (int, float))
        for key in ("map50_95", "map50", "precision", "recall")
    )


def _effective_gate_summary(
    db: Session,
    row: ModelVersion,
    *,
    edge_validation_status: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve publish gates from explicit evidence and real model artifacts.

    The original evidence table remains the audit override: an explicit FAIL
    always blocks publication and an explicit PASS is retained.  For training
    models, however, registration already has immutable PT metrics and the
    compiler/edge workers already persist hashed artifacts and validation
    reports.  Requiring an operator to copy those facts into a second table
    left the model permanently at ``待验证``.  Derive only the missing gates
    from those persisted facts so the normal flow is one click from a passed
    edge check to publication.
    """
    summary = _gate_summary(row)
    metrics = _json_value(row.metrics_json) or {}

    # Training and generalization come from the completed training job's
    # immutable metrics snapshot copied onto the model version at registration.
    train_metrics = (
        metrics.get("best_pt_val_metrics")
        or metrics.get("final_metrics")
        or metrics.get("last_epoch_metrics")
    )
    test_metrics = metrics.get("test_val_metrics") or metrics.get("test_val")
    if summary["training"] == "PENDING" and _metric_block_is_ready(train_metrics):
        summary["training"] = "PASS"
    if summary["generalization"] == "PENDING" and _metric_block_is_ready(test_metrics):
        summary["generalization"] = "PASS"

    # The compiler synchronizes ONNX and all three BM1688 precisions to the
    # same model version only after the files are complete and hashed.
    if summary["conversion"] == "PENDING":
        has_onnx = _has_ready_artifact(row, frozenset({"onnx"}))
        has_bmodel_bundle = _requires_bm1688_bundle(row) and not (
            set(("FP32", "FP16", "INT8")) - set(_bm1688_bmodel_precisions(row))
        )
        if has_onnx and has_bmodel_bundle:
            summary["conversion"] = "PASS"

    if edge_validation_status is None:
        edge_validation_status = _edge_validation_status_for_model(db, row)
    if summary["edge"] == "PENDING" and edge_validation_status:
        if str(edge_validation_status.get("status") or "").upper() == "PASS":
            summary["edge"] = "PASS"
        elif str(edge_validation_status.get("status") or "").upper() == "FAIL":
            summary["edge"] = "FAIL"

    # Release is the final readiness result, not a second manual inspection.
    # It is derived only after all preceding checks have passed.
    publish_gate_status = str(
        (edge_validation_status or {}).get("publish_gate_status") or ""
    ).upper()
    # Edge inference success and release readiness are different facts.  A
    # completed device run can still lack a signed compile profile, runtime
    # evidence, or a publish-grade fixed-evaluation report.  Keep release
    # pending until the publish endpoint would accept the same evidence.
    release_evidence_ready = publish_gate_status in {"", "ALLOWED", "PASS"}
    if (
        summary["release"] == "PENDING"
        and release_evidence_ready
        and all(
            summary[gate] == "PASS"
            for gate in ("training", "generalization", "conversion", "edge")
        )
    ):
        summary["release"] = "PASS"
    return summary


def _model_algorithm_key(row: ModelVersion) -> str | None:
    """Return the canonical algorithm key without an extra query.

    New model versions persist ``<algorithm>:v<version>``.  The parser also
    tolerates historical ``project-<id>:<algorithm>:v<version>`` keys while
    old rows are being migrated.
    Keeping the response self-contained lets the model repository render the
    algorithm even when list/detail queries only eager-load artifacts and
    quality evidence.
    """
    model_key = str(row.model_key or "").strip()
    if ":v" not in model_key:
        return None
    value = model_key.rsplit(":v", 1)[0]
    if value.startswith("project-") and ":" in value:
        value = value.split(":", 1)[1]
    return value or None


def _quality_gate_status(summary: dict[str, str]) -> str:
    if all(summary[gate] == "PASS" for gate in GATE_TYPES):
        return "PASS"
    if any(summary[gate] == "FAIL" for gate in GATE_TYPES):
        return "FAIL"
    return "PENDING"


def _edge_validation_projection(
    status: Any,
    *,
    publish_gate: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Convert the latest edge job status into a model-center view.

    Edge validation is executed against a BM1688 export.  The model repository
    needs to show the actual latest result and uses a successful projection as
    the derived edge gate when no explicit evidence row exists.
    """
    if status is None:
        return None
    raw_status = str(getattr(status, "status", "") or "").upper()
    edge_result = str(getattr(status, "edge_result_status", "") or "").lower()
    report = getattr(status, "report", None)
    report = report if isinstance(report, dict) else {}
    result = report.get("result") if isinstance(report.get("result"), dict) else {}
    quality_gate = result.get("quality_gate")
    if not isinstance(quality_gate, dict):
        eval_summary = result.get("eval_summary")
        quality_gate = (
            eval_summary.get("quality_gate")
            if isinstance(eval_summary, dict)
            and isinstance(eval_summary.get("quality_gate"), dict)
            else {}
        )
    gate_status = str(quality_gate.get("status") or "").strip().lower()

    if raw_status in {"PENDING", "PROCESSING"}:
        display_status = "PROCESSING"
    elif raw_status == "STALE":
        # 重新编译后旧验证仍保留作审计记录，但不能继续作为当前制品证据。
        display_status = "PENDING"
    elif raw_status == "FAILED":
        display_status = "FAIL"
    elif gate_status in {"fail", "failed", "error"}:
        display_status = "FAIL"
    elif gate_status in {"pass", "passed", "ok", "success"} and edge_result == "pass":
        display_status = "PASS"
    elif edge_result in {"fail", "review"}:
        display_status = "FAIL"
    else:
        display_status = "PENDING"

    reasons = quality_gate.get("reasons")
    if not isinstance(reasons, list):
        reasons = []
    report_summary = str(getattr(status, "report_summary", None) or "").strip()
    # Historical edge reports used “发布需复核” for the warning-only drop
    # band. The current policy allows direct publication in that band, so keep
    # old records understandable without rewriting their audit payload.
    if "发布需复核" in report_summary:
        report_summary = report_summary.replace(
            "发布需复核", "存在精度提示（不阻断发布）"
        )
    comparison = result.get("comparison_pt_bmodel")
    if not isinstance(comparison, dict):
        comparison = None
    eval_summary = result.get("eval_summary")
    if not isinstance(eval_summary, dict):
        eval_summary = {}
    pt_eval = result.get("pt_eval")
    if not isinstance(pt_eval, dict):
        pt_eval = {}
    pt_metrics = pt_eval.get("metrics")
    if not isinstance(pt_metrics, dict):
        pt_metrics = {}
    edge_metrics = dict(eval_summary)
    edge_metrics.pop("quality_gate", None)
    # Keep the comparison payload compact enough for the model catalog while
    # preserving the exact same-batch/evaluation evidence flags.
    comparison_summary = None
    if comparison:
        comparison_summary = {
            key: comparison.get(key)
            for key in (
                "metric",
                "primary_precision",
                "pt_map50",
                "bmodel_map50",
                "signed_delta_map50",
                "drop_map50",
                "delta_map50",
                "delta_ratio",
                "drop_ratio",
                "status",
                "verdict",
                "reason_code",
                "same_batch",
                "same_thresholds",
                "same_preprocessing",
                "detail",
            )
            if key in comparison
        }
    signed_delta = (
        comparison_summary.get("signed_delta_map50") if comparison_summary else None
    )
    drop_delta = comparison_summary.get("drop_map50") if comparison_summary else None
    comparison_comparable = bool(
        comparison_summary
        and all(
            comparison_summary.get(key) is True
            for key in ("same_batch", "same_thresholds", "same_preprocessing")
        )
    )
    try:
        signed_delta = float(signed_delta) if signed_delta is not None else None
    except (TypeError, ValueError):
        signed_delta = None
    try:
        drop_delta = float(drop_delta) if drop_delta is not None else None
    except (TypeError, ValueError):
        drop_delta = None
    projection = {
        "validation_id": getattr(status, "validation_id", None),
        "export_id": getattr(status, "export_id", None),
        "status": display_status,
        "job_status": raw_status or "PENDING",
        "edge_result_status": edge_result or None,
        "quality_gate_status": gate_status.upper() or None,
        "reasons": [str(item) for item in reasons if str(item).strip()],
        "report_summary": report_summary or None,
        "completed_at": getattr(status, "completed_at", None),
        "training_metrics": pt_metrics or None,
        "inference_metrics": edge_metrics or None,
        "pt_bmodel_comparison": comparison_summary,
        "comparison_comparable": comparison_comparable,
        "improvement_status": (
            "IMPROVED"
            if comparison_comparable and signed_delta is not None and signed_delta > 0
            else (
                "REGRESSED"
                if comparison_comparable and drop_delta is not None and drop_delta > 0
                else (
                    "UNCHANGED"
                    if comparison_comparable and comparison_summary
                    else None
                )
            )
        ),
    }
    if publish_gate:
        projection.update(publish_gate)
    return projection


def _edge_version_comparison(
    current_status: dict[str, Any] | None,
    previous_status: dict[str, Any] | None,
    *,
    previous_model_version_id: int | None,
    previous_version_no: int | None,
) -> dict[str, Any]:
    """Compare two model versions using only completed, comparable edge runs.

    This deliberately does not compare PT metrics.  A version trend is valid
    only when both versions have passed a real edge-box inference validation,
    each validation has the same-batch/threshold/preprocessing evidence, and
    both reports contain a numeric mAP50 value.
    """
    base = {
        "previous_model_version_id": previous_model_version_id,
        "previous_version_no": previous_version_no,
        "previous_validation_id": (
            previous_status.get("validation_id") if previous_status else None
        ),
        "current_map50": None,
        "previous_map50": None,
        "signed_delta_map50": None,
        "delta_ratio": None,
        "status": None,
        "comparable": False,
        "baseline_state": (
            "NO_PREVIOUS_VERSION"
            if previous_model_version_id is None
            else "PREVIOUS_EDGE_UNAVAILABLE"
        ),
        "reason": None,
    }
    if previous_model_version_id is None:
        base["reason"] = "当前版本没有上一版本"
        return base
    if not previous_status or str(previous_status.get("status") or "").upper() != "PASS":
        base["reason"] = "上一版本尚未完成通过的边端推理验证"
        return base
    if not previous_status.get("comparison_comparable"):
        base["reason"] = "上一版本缺少同口径边端验证证据，不能比较边端版本效果"
        return base
    if not current_status or str(current_status.get("status") or "").upper() != "PASS":
        base["baseline_state"] = "CURRENT_EDGE_UNAVAILABLE"
        base["reason"] = "当前版本尚未完成通过的边端推理验证"
        return base
    if not current_status.get("comparison_comparable"):
        base["baseline_state"] = "CURRENT_EDGE_UNAVAILABLE"
        base["reason"] = "当前版本缺少同口径边端验证证据，不能比较边端版本效果"
        return base

    def _map50(value: Any) -> float | None:
        if not isinstance(value, dict):
            return None
        for key in ("map50", "mAP50", "mAP_50"):
            candidate = value.get(key)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return float(candidate)
        return None

    current_map50 = _map50(current_status.get("inference_metrics"))
    previous_map50 = _map50(previous_status.get("inference_metrics"))
    base["current_map50"] = current_map50
    base["previous_map50"] = previous_map50
    if current_map50 is None or previous_map50 is None:
        base["baseline_state"] = "CURRENT_EDGE_UNAVAILABLE"
        base["reason"] = "当前版本或上一版本缺少边端 mAP50 指标"
        return base
    delta = current_map50 - previous_map50
    epsilon = 1e-9
    base["signed_delta_map50"] = delta
    base["delta_ratio"] = delta / previous_map50 if abs(previous_map50) > epsilon else None
    base["status"] = (
        "IMPROVED" if delta > epsilon else "REGRESSED" if delta < -epsilon else "UNCHANGED"
    )
    base["comparable"] = True
    base["baseline_state"] = "COMPARABLE"
    base["reason"] = "当前版本与上一版本均已在边端盒子完成同口径推理验证"
    return base


def _publish_gate_projection(
    export_row: Any,
    edge_validation: Any,
) -> dict[str, Any]:
    """Project the real publish gate into the model-center response.

    The model center must use the same gate inputs as the publish endpoint;
    otherwise it can show a green release state while the publish request is
    correctly rejected.  This projection is read-only and does not mutate the
    export or validation records.
    """
    if export_row is None or edge_validation is None:
        return {}
    try:
        manifest = json.loads(
            getattr(export_row, "artifact_manifest_json", None) or "{}"
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    from labelu.internal.application.service import yolo_aicm_publish

    reasons = yolo_aicm_publish._publish_gate_reason_codes(
        edge_validation=edge_validation,
        artifact_manifest=manifest,
        export_id=int(getattr(export_row, "id", 0) or 0),
    )
    compile_reason = yolo_aicm_publish._compile_contract_block_reason(
        export_row, edge_validation
    )
    if compile_reason and "compile_contract_blocked" not in reasons:
        reasons.append("compile_contract_blocked")
    reasons = list(dict.fromkeys(str(item) for item in reasons if str(item).strip()))
    report_summary = str(getattr(edge_validation, "report_summary", "") or "").strip()
    if reasons:
        detail = compile_reason or report_summary or "发布前证据尚未完整"
        return {
            "publish_gate_status": "BLOCKED",
            "publish_gate_reason_codes": reasons,
            "publish_blocked_reason": detail,
        }
    return {
        "publish_gate_status": "ALLOWED",
        "publish_gate_reason_codes": [],
        "publish_blocked_reason": None,
    }


def _edge_validation_status_for_model(
    db: Session, row: ModelVersion
) -> dict[str, Any] | None:
    """Return the latest edge result for a model version without changing data."""
    if not row.training_job_id:
        return None
    exports = crud_yolo_bm1688_export.list_all_for_job(
        db, yolo_train_job_id=int(row.training_job_id)
    )
    if not exports:
        return None
    from labelu.internal.application.service import yolo_aicm_publish

    latest = yolo_aicm_publish._edge_validation_status_for_export(
        db, export_id=int(exports[0].id), include_report=True
    )
    return _edge_validation_projection(
        latest,
        publish_gate=_publish_gate_projection(exports[0], latest),
    )


def _edge_validation_statuses_for_models(
    db: Session, rows: list[ModelVersion]
) -> dict[int, dict[str, Any] | None]:
    """Batch-load edge results for a model list to avoid one query per row."""
    job_ids = {int(row.training_job_id) for row in rows if row.training_job_id}
    if not job_ids:
        return {}
    exports = crud_yolo_bm1688_export.latest_by_job_ids(db, yolo_train_job_ids=job_ids)
    export_rows = list(exports.values())
    if not export_rows:
        return {}
    from labelu.internal.application.service import yolo_aicm_publish

    latest_statuses = yolo_aicm_publish.edge_validation_statuses_for_exports(
        db, export_rows
    )
    return {
        int(row.id): _edge_validation_projection(
            latest_statuses.get(int(exports[int(row.training_job_id)].id)),
            publish_gate=_publish_gate_projection(
                exports[int(row.training_job_id)],
                latest_statuses.get(int(exports[int(row.training_job_id)].id)),
            ),
        )
        for row in rows
        if row.training_job_id and int(row.training_job_id) in exports
    }


def _has_ready_artifact(row: ModelVersion, artifact_types: frozenset[str]) -> bool:
    return any(
        str(artifact.status).upper() == "READY"
        and str(artifact.artifact_type).lower() in artifact_types
        for artifact in row.artifacts or []
    )


def _bm1688_bmodel_precisions(row: ModelVersion) -> frozenset[str]:
    return frozenset(
        str(artifact.precision or "").upper()
        for artifact in row.artifacts or []
        if str(artifact.status).upper() == "READY"
        and str(artifact.artifact_type).lower() == "bmodel"
        and str(artifact.chip_type or row.chip_type or "").upper() == "BM1688"
    )


def _requires_bm1688_bundle(row: ModelVersion) -> bool:
    return str(row.chip_type or "").upper() == "BM1688" or bool(
        _bm1688_bmodel_precisions(row)
    )


def _assert_bm1688_bundle_ready(row: ModelVersion) -> None:
    if not _requires_bm1688_bundle(row):
        return
    missing = sorted({"FP32", "FP16", "INT8"} - _bm1688_bmodel_precisions(row))
    if missing:
        raise _error(
            f"BM1688 模型必须同时具备 FP32、FP16、INT8 三份 BModel，当前缺少：{'、'.join(missing)}",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )


def _validate_evidence_stage(
    row: ModelVersion, gate_type: str, evidence_status: str
) -> None:
    """Enforce the real PT -> evaluation -> conversion -> edge -> release flow."""
    if evidence_status != "PASS":
        return
    latest = _gate_summary(row)
    if gate_type == "training" and not _has_ready_artifact(row, PT_ARTIFACT_TYPES):
        raise _error(
            "训练质量通过前必须先登记 READY 状态的 PT 模型制品",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    if gate_type == "generalization" and latest["training"] != "PASS":
        raise _error(
            "泛化质量通过前必须先通过训练质量",
            ErrorCode.CODE_57112_AUTO_TRAIN_STAGE_GATE_BLOCKED,
            status.HTTP_409_CONFLICT,
        )
    if gate_type == "conversion":
        if latest["generalization"] != "PASS":
            raise _error(
                "转换质量通过前必须先通过泛化质量",
                ErrorCode.CODE_57112_AUTO_TRAIN_STAGE_GATE_BLOCKED,
                status.HTTP_409_CONFLICT,
            )
        # ONNX is the compiler input. A BModel by itself cannot prove that the
        # exported graph was produced from this model version.
        if not _has_ready_artifact(row, frozenset({"onnx"})):
            raise _error(
                "转换质量通过前必须先登记 READY 状态的 ONNX 制品",
                ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                status.HTTP_409_CONFLICT,
            )
        _assert_bm1688_bundle_ready(row)
    if gate_type == "edge":
        if latest["conversion"] != "PASS":
            raise _error(
                "边端验证通过前必须先通过转换质量",
                ErrorCode.CODE_57112_AUTO_TRAIN_STAGE_GATE_BLOCKED,
                status.HTTP_409_CONFLICT,
            )
        if not _has_ready_artifact(row, DEPLOYABLE_ARTIFACT_TYPES):
            raise _error(
                "边端验证通过前必须先登记 READY 状态的 BM1688 BModel 制品",
                ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                status.HTTP_409_CONFLICT,
            )
        _assert_bm1688_bundle_ready(row)
    if gate_type == "release" and latest["edge"] != "PASS":
        raise _error(
            "发布门禁通过前必须先通过边端验证",
            ErrorCode.CODE_57112_AUTO_TRAIN_STAGE_GATE_BLOCKED,
            status.HTTP_409_CONFLICT,
        )


def _response(
    row: ModelVersion,
    *,
    db: Session | None = None,
    edge_validation_status: dict[str, Any] | None = None,
    bm1688_export_id: int | None = None,
    dataset_version_no: int | None = None,
    training_model_weights: str | None = None,
) -> ModelVersionResponse:
    training_job = None
    if bm1688_export_id is None and db is not None and row.training_job_id:
        exports = crud_yolo_bm1688_export.list_all_for_job(
            db, yolo_train_job_id=int(row.training_job_id)
        )
        if exports:
            bm1688_export_id = int(exports[0].id)
    if db is not None and dataset_version_no is None and row.dataset_version_id:
        dataset_version = crud_dataset.get_version(db, int(row.dataset_version_id))
        dataset_version_no = (
            int(dataset_version.version_no) if dataset_version is not None else None
        )
    if db is not None and training_model_weights is None and row.training_job_id:
        training_job = crud_yolo_train_job.get(db, int(row.training_job_id))
        training_model_weights = (
            str(training_job.model_weights)
            if training_job is not None and training_job.model_weights
            else None
        )
    elif db is not None and row.training_job_id:
        training_job = crud_yolo_train_job.get(db, int(row.training_job_id))
    job_params = (
        _training_job_json(training_job, "hyperparams_json")
        if training_job is not None
        else {}
    )
    job_manifest = (
        _training_job_json(training_job, "dataset_manifest_json")
        if training_job is not None
        else {}
    )
    # Prefer the canonical model-version snapshot, then fall back through the
    # training job and its historical hyperparameter/manifest shapes.  The
    # fallback is important for rows registered before the dedicated model
    # lineage columns were introduced.
    selection_contract = _json_value(getattr(row, "selection_contract_json", None))
    if not isinstance(selection_contract, dict) and training_job is not None:
        selection_contract = _json_value(
            getattr(training_job, "selection_contract_json", None)
        )
    if not isinstance(selection_contract, dict):
        candidate = job_params.get("selection_contract")
        selection_contract = candidate if isinstance(candidate, dict) else None
    if not isinstance(selection_contract, dict):
        candidate = job_manifest.get("selection_contract")
        selection_contract = candidate if isinstance(candidate, dict) else None
    if not isinstance(selection_contract, dict):
        model_manifest = _json_value(getattr(row, "manifest_json", None))
        candidate = model_manifest.get("selection_contract")
        selection_contract = candidate if isinstance(candidate, dict) else None
    nested_manifest = job_params.get("dataset_manifest")
    if isinstance(nested_manifest, dict):
        for key, value in nested_manifest.items():
            job_manifest.setdefault(key, value)
    training_recipe_key = (
        str(getattr(training_job, "training_recipe_key", "") or "").strip()
        or str(
            job_manifest.get("training_recipe_key")
            or job_params.get("training_recipe_key")
            or ""
        ).strip()
        or None
    )
    try:
        training_recipe_version = int(
            getattr(training_job, "training_recipe_version", None)
            or job_manifest.get("training_recipe_version")
            or job_params.get("training_recipe_version")
        )
    except (TypeError, ValueError):
        training_recipe_version = None
    label_set_signature = (
        str(getattr(training_job, "label_set_signature", "") or "").strip()
        or str(job_manifest.get("label_set_signature") or "").strip()
        or None
    )
    class_order_signature = (
        str(getattr(training_job, "class_order_signature", "") or "").strip()
        or str(job_manifest.get("class_order_signature") or "").strip()
        or None
    )
    # Historical training jobs may have retained only the immutable dataset
    # version reference.  The family endpoint can still match those jobs by
    # recovering signatures from DatasetVersion; mirror that recovery in the
    # response so the frontend does not mark an otherwise compatible parent as
    # "contract missing".
    if training_job is not None and (
        not label_set_signature or not class_order_signature
    ):
        dataset_version_id = row.dataset_version_id
        if dataset_version_id is None:
            dataset_version_id = job_manifest.get(
                "dataset_version_id"
            ) or job_params.get("dataset_version_id")
        try:
            dataset_version_id = int(dataset_version_id)
        except (TypeError, ValueError):
            dataset_version_id = None
        if dataset_version_id:
            dataset_version = crud_dataset.get_version(db, dataset_version_id)
            if dataset_version is not None:
                label_set_signature = label_set_signature or (
                    str(
                        getattr(dataset_version, "label_set_signature", "") or ""
                    ).strip()
                    or None
                )
                class_order_signature = class_order_signature or (
                    str(
                        getattr(dataset_version, "class_order_signature", "") or ""
                    ).strip()
                    or None
                )
    # Resolve names at response time so the API remains useful for old model
    # rows that only contain numeric series/variant links.  Relationships are
    # used first; direct queries cover detached/legacy ORM objects in tests.
    series_id = getattr(row, "series_id", None)
    variant_id = getattr(row, "variant_id", None)
    if series_id is None and training_job is not None:
        series_id = getattr(training_job, "series_id", None)
    if variant_id is None and training_job is not None:
        variant_id = getattr(training_job, "variant_id", None)
    series = getattr(row, "series", None)
    variant = getattr(row, "variant", None)
    if series_id is None and variant is not None:
        series_id = getattr(variant, "series_id", None)
    series_name = str(getattr(series, "name", "") or "").strip() or None
    variant_name = str(getattr(variant, "name", "") or "").strip() or None
    model_family = str(getattr(series, "model_family", "") or "").strip() or None
    if db is not None:
        try:
            from labelu.internal.domain.models.model_series import (
                ModelSeries,
                ModelVariant,
            )

            if variant is None and variant_id:
                variant = (
                    db.query(ModelVariant)
                    .filter(ModelVariant.id == int(variant_id))
                    .one_or_none()
                )
                variant_name = str(getattr(variant, "name", "") or "").strip() or None
                if series_id is None and variant is not None:
                    series_id = getattr(variant, "series_id", None)
            if series is None and series_id:
                series = (
                    db.query(ModelSeries)
                    .filter(ModelSeries.id == int(series_id))
                    .one_or_none()
                )
                series_name = str(getattr(series, "name", "") or "").strip() or None
                model_family = (
                    str(getattr(series, "model_family", "") or "").strip() or None
                )
        except (AttributeError, TypeError, ValueError):
            # Historical test doubles and partially upgraded deployments may
            # not expose the new tables; numeric lineage remains valid.
            pass
    branch_key = (
        str(
            getattr(row, "branch_key", None)
            or (getattr(training_job, "branch_key", None) if training_job else None)
            or getattr(variant, "branch_key", None)
            or ""
        ).strip()
        or None
    )
    selection_contract_sha256 = (
        str(
            getattr(row, "selection_contract_sha256", None)
            or (
                getattr(training_job, "selection_contract_sha256", None)
                if training_job is not None
                else None
            )
            or ""
        ).strip()
        or None
    )
    if selection_contract_sha256 is None and isinstance(selection_contract, dict):
        try:
            selection_contract_sha256 = build_selection_contract_sha256(
                canonical_selection_contract(selection_contract)
            )
        except (TypeError, ValueError):
            selection_contract_sha256 = None
    if branch_key is None:
        branch_key = (
            branch_identity_sha256(selection_contract)
            if isinstance(selection_contract, dict)
            else selection_contract_sha256
        )
    model_manifest = _json_value(getattr(row, "manifest_json", None))
    model_family = (
        model_family
        or str(
            job_params.get("model_family")
            or job_manifest.get("model_family")
            or model_manifest.get("model_family")
            or ""
        ).strip()
        or None
    )
    parent_training_job_id = getattr(row, "parent_training_job_id", None)
    if parent_training_job_id is None and training_job is not None:
        parent_training_job_id = getattr(training_job, "parent_training_job_id", None)
    parent_model_version_id = getattr(row, "parent_model_version_id", None)
    if parent_model_version_id is None and training_job is not None:
        parent_model_version_id = getattr(training_job, "parent_model_version_id", None)
    if parent_model_version_id is None and db is not None and parent_training_job_id:
        parent_model = crud_model_version.get_by_training_job(
            db, int(parent_training_job_id)
        )
        parent_model_version_id = (
            int(parent_model.id) if parent_model is not None else None
        )
    if edge_validation_status is None and db is not None:
        edge_validation_status = _edge_validation_status_for_model(db, row)
    quality_gate = (
        _effective_gate_summary(
            db,
            row,
            edge_validation_status=edge_validation_status,
        )
        if db is not None
        else _gate_summary(row)
    )
    return ModelVersionResponse(
        id=row.id,
        algorithm_id=row.algorithm_id,
        task_id=(
            int(getattr(training_job, "task_id"))
            if training_job is not None and getattr(training_job, "task_id", None) is not None
            else None
        ),
        algorithm_key=_model_algorithm_key(row),
        dataset_id=row.dataset_id,
        dataset_version_id=row.dataset_version_id,
        dataset_version_no=dataset_version_no,
        series_id=series_id,
        variant_id=variant_id,
        branch_key=branch_key,
        selection_contract_sha256=selection_contract_sha256,
        selection_contract=selection_contract,
        series_name=series_name,
        variant_name=variant_name,
        model_family=model_family,
        inheritance_mode=(
            getattr(row, "inheritance_mode", None)
            or (
                getattr(training_job, "inheritance_mode", None)
                if training_job is not None
                else None
            )
        ),
        parent_training_job_id=parent_training_job_id,
        training_job_id=row.training_job_id,
        training_model_weights=training_model_weights,
        training_recipe_key=training_recipe_key,
        training_recipe_version=training_recipe_version,
        label_set_signature=label_set_signature,
        class_order_signature=class_order_signature,
        bm1688_export_id=bm1688_export_id,
        parent_model_version_id=parent_model_version_id,
        published_catalog_id=row.published_catalog_id,
        model_key=row.model_key,
        branch_version_no=getattr(row, "branch_version_no", None),
        version_no=row.version_no,
        display_name=row.display_name,
        status=row.status,
        chip_type=row.chip_type,
        precision=row.precision,
        quality_gate_status=_quality_gate_status(quality_gate),
        input_contract=_json_value(row.input_contract_json),
        output_contract=_json_value(row.output_contract_json),
        preprocess_contract=_json_value(row.preprocess_contract_json),
        postprocess_contract=_json_value(row.postprocess_contract_json),
        metrics=_json_value(row.metrics_json),
        manifest=_json_value(row.manifest_json),
        artifacts=[_artifact_response(item) for item in row.artifacts or []],
        quality_evidence=[
            _evidence_response(item) for item in row.quality_evidence or []
        ],
        quality_gate=quality_gate,
        edge_validation_status=edge_validation_status,
        created_by=row.created_by,
        published_by=row.published_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        published_at=row.published_at,
        retired_at=row.retired_at,
    )


def _validate_sha(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise _error(
            "sha256 必须是 64 位十六进制摘要",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    return normalized


def _safe_relative_path(value: str) -> str:
    """Keep artifacts inside the model bundle namespace."""
    normalized = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise _error(
            "模型产物路径必须是安全的相对路径",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    return str(path)


def resolve_artifact_download(
    db: Session, model_version_id: int, artifact_type: str, user: User
) -> tuple[Path, str]:
    """Resolve a downloadable model artifact without applying publish gates.

    A PT file is a training output and may be downloaded from the internal
    repository before the BM1688 conversion and release checks are complete.
    The download still uses the normal model access check and never trusts an
    absolute or path-traversing database value.
    """

    row = _model_or_raise(db, model_version_id, user)
    normalized_type = str(artifact_type or "").strip().lower()
    artifact = next(
        (
            item
            for item in row.artifacts or []
            if str(item.artifact_type or "").strip().lower() == normalized_type
            and str(item.status or "").upper() == "READY"
        ),
        None,
    )
    if artifact is None:
        raise _error(
            f"模型版本尚未生成可下载的{normalized_type.upper() or '模型'}制品",
            ErrorCode.CODE_61000_NO_DATA,
            status.HTTP_404_NOT_FOUND,
        )

    relative_path = _safe_relative_path(str(artifact.relative_path or ""))
    root = Path(settings.MEDIA_ROOT).resolve()
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise _error(
            "模型制品路径无效",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_404_NOT_FOUND,
        ) from exc
    if not path.is_file():
        raise _error(
            "模型制品文件不存在",
            ErrorCode.CODE_61000_NO_DATA,
            status.HTTP_404_NOT_FOUND,
        )

    suffix = path.suffix or ".bin"
    filename = f"{row.display_name}-v{row.version_no}-{normalized_type}{suffix}"
    return path, filename


def _training_job_json(job: Any, field: str) -> dict[str, Any]:
    raw = getattr(job, field, None)
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _training_job_dataset_version_id(job: Any) -> int | None:
    """Return the frozen dataset version captured by a training job.

    Older training jobs may contain a complete dataset manifest but no
    governed dataset-version id. Those jobs can still be evaluated, but they
    must not be auto-registered as model versions because the lineage cannot
    be reconstructed safely.
    """
    params = _training_job_json(job, "hyperparams_json")
    manifest = _training_job_json(job, "dataset_manifest_json")
    # New jobs keep the frozen snapshot under ``hyperparams.dataset_manifest``
    # while older workers wrote the fields at the top level.  Search both
    # shapes so model registration does not silently reject a valid completed
    # run after a rolling upgrade.
    candidates: list[dict[str, Any]] = [params, manifest]
    for container in (params, manifest):
        for key in ("dataset_manifest", "dataset_contract", "manifest"):
            nested = container.get(key)
            if isinstance(nested, dict):
                candidates.append(nested)
    raw_value = next(
        (
            candidate.get("dataset_version_id")
            for candidate in candidates
            if candidate.get("dataset_version_id") not in (None, "", 0)
        ),
        None,
    )
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


async def register_from_training_job(
    db: Session, cmd: RegisterTrainingJobModelCommand, user: User
) -> ModelVersionResponse:
    """Register a completed platform training run as a governed model version.

    This is intentionally one operation from the operator's perspective:
    lineage is read from the immutable training job snapshot, the PT artifact
    is hashed from disk, and the artifact is created READY.  It does not mark
    quality gates as passed; those remain a separate evidence workflow.
    """

    job = crud_yolo_train_job.get(db, int(cmd.training_job_id))
    if job is None:
        raise _error(
            "训练作业不存在",
            ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status.HTTP_404_NOT_FOUND,
        )
    if str(getattr(job, "status", "")).upper() != "COMPLETED":
        raise _error(
            "只有已完成的训练作业可以登记到模型仓库",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    weights_value = str(getattr(job, "weights_path", "") or "").strip()
    if not weights_value:
        raise _error(
            "训练作业没有可登记的 best.pt 权重文件",
            ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status.HTTP_409_CONFLICT,
        )

    task = crud_task.get(db=db, task_id=int(job.task_id))
    if task is None:
        raise _error("训练作业关联任务不存在", http_status=status.HTTP_409_CONFLICT)
    if not is_task_superuser(user) and not user_has_task_access(user, task):
        raise _error(
            "无权登记该训练作业生成的模型",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )

    # The operation is idempotent so retrying the button never creates a
    # second model version for the same training run.
    existing = crud_model_version.get_by_training_job(db, int(job.id))
    if existing is not None:
        return _response(existing, db=db)

    params = _training_job_json(job, "hyperparams_json")
    manifest = _training_job_json(job, "dataset_manifest_json")
    selection_contract = _json_value(getattr(job, "selection_contract_json", None))
    if not isinstance(selection_contract, dict):
        candidate = params.get("selection_contract")
        selection_contract = candidate if isinstance(candidate, dict) else None
    if not isinstance(selection_contract, dict):
        candidate = manifest.get("selection_contract")
        selection_contract = candidate if isinstance(candidate, dict) else None
    if isinstance(selection_contract, dict):
        try:
            selection_contract = canonical_selection_contract(selection_contract)
        except (TypeError, ValueError):
            selection_contract = None
    selection_contract_hash = (
        str(getattr(job, "selection_contract_sha256", None) or "").strip() or None
    )
    if selection_contract_hash is None and isinstance(selection_contract, dict):
        selection_contract_hash = build_selection_contract_sha256(selection_contract)
    branch_key = (
        str(getattr(job, "branch_key", None) or "").strip() or selection_contract_hash
    )
    if not str(getattr(job, "branch_key", None) or "").strip() and isinstance(
        selection_contract, dict
    ):
        branch_key = branch_identity_sha256(selection_contract)
    parent_model_version_id = getattr(job, "parent_model_version_id", None)
    if parent_model_version_id is None:
        parent_model_version_id = params.get("parent_model_version_id")
    try:
        parent_model_version_id = (
            int(parent_model_version_id) if parent_model_version_id else None
        )
    except (TypeError, ValueError):
        parent_model_version_id = None
    parent_training_job_id = getattr(job, "parent_training_job_id", None)
    if parent_training_job_id is None:
        parent_training_job_id = params.get("parent_training_job_id")
    try:
        parent_training_job_id = (
            int(parent_training_job_id) if parent_training_job_id else None
        )
    except (TypeError, ValueError):
        parent_training_job_id = None
    inheritance_mode = (
        str(
            getattr(job, "inheritance_mode", None)
            or params.get("inheritance_mode")
            or ""
        ).strip()
        or None
    )
    parent_model = None
    if parent_model_version_id is not None:
        parent_model = crud_model_version.get(db, int(parent_model_version_id))
    elif parent_training_job_id is not None:
        parent_model = crud_model_version.get_by_training_job(
            db, int(parent_training_job_id)
        )
        if parent_model is not None:
            parent_model_version_id = int(parent_model.id)
    series_id = getattr(job, "series_id", None) or (
        getattr(parent_model, "series_id", None) if parent_model else None
    )
    variant_id = getattr(job, "variant_id", None) or (
        getattr(parent_model, "variant_id", None) if parent_model else None
    )
    dataset_version_id = _training_job_dataset_version_id(job)
    if dataset_version_id is None:
        raise _error(
            "训练作业缺少冻结数据集版本，无法登记模型",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )

    algorithm_id = params.get("algorithm_id")
    algorithm_key = (
        str(
            params.get("algorithm_key")
            or getattr(job, "category", None)
            or getattr(job, "job_type", None)
            or ""
        )
        .strip()
        .lower()
    )
    if algorithm_id:
        try:
            algorithm_id = int(algorithm_id)
        except (TypeError, ValueError):
            algorithm_id = None
    algorithm = (
        crud_algorithm.get_definition_by_id(db, algorithm_id)
        if algorithm_id
        else crud_algorithm.get_definition(db, algorithm_key)
    )
    if algorithm is None:
        raise _error(
            "训练作业缺少有效算法定义，无法登记模型",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )

    dataset_version = crud_dataset.get_version(db, dataset_version_id)
    if dataset_version is None or str(dataset_version.status).upper() != "FROZEN":
        raise _error(
            "训练作业关联的数据集版本不存在或尚未冻结",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )

    # Resolve through the same media-root guard used by the download route.
    from labelu.internal.application.service import yolo_train as yolo_train_service

    artifact_path = yolo_train_service.resolve_weights_path(weights_value)
    media_root = Path(settings.MEDIA_ROOT).resolve()
    relative_path = _safe_relative_path(
        artifact_path.relative_to(media_root).as_posix()
    )
    digest = hashlib.sha256()
    with artifact_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    metrics = _training_job_json(job, "metrics_summary")
    dataset_manifest_summary = _compact_dataset_manifest(manifest)
    dataset_contract = params.get("dataset_manifest")
    model_manifest = {
        "source": "training_job",
        "training_job_id": int(job.id),
        "task_id": int(task.id),
        "task_name": getattr(task, "name", None),
        "dataset_manifest": dataset_manifest_summary,
        "dataset_contract": (
            _compact_dataset_manifest(dataset_contract)
            if isinstance(dataset_contract, dict)
            else {}
        ),
        "sample_counts": {
            "train": int(getattr(job, "train_sample_count", 0) or 0),
            "val": int(getattr(job, "val_sample_count", 0) or 0),
        },
        "weights_path": relative_path,
        "series_id": series_id,
        "variant_id": variant_id,
        "branch_key": branch_key,
        "selection_contract_sha256": selection_contract_hash,
        "selection_contract": selection_contract,
        "parent_model_version_id": parent_model_version_id,
        "parent_training_job_id": parent_training_job_id,
        "inheritance_mode": inheritance_mode,
    }
    display_name = (
        cmd.display_name or ""
    ).strip() or f"{getattr(task, 'name', None) or '训练任务'} · 作业 #{int(job.id)}"
    row = await create(
        db,
        CreateModelVersionCommand(
            algorithm_id=int(algorithm.id),
            dataset_version_id=int(dataset_version.id),
            training_job_id=int(job.id),
            series_id=series_id,
            variant_id=variant_id,
            branch_key=branch_key,
            selection_contract=selection_contract,
            inheritance_mode=inheritance_mode,
            selection_contract_sha256=selection_contract_hash,
            parent_model_version_id=parent_model_version_id,
            parent_training_job_id=parent_training_job_id,
            display_name=display_name,
            chip_type=params.get("chip_type"),
            precision=params.get("precision"),
            metrics=metrics or None,
            manifest=model_manifest,
        ),
        user,
    )
    artifact = ModelArtifact(
        model_version_id=row.id,
        artifact_type="pt",
        relative_path=relative_path,
        sha256=digest.hexdigest(),
        size_bytes=int(artifact_path.stat().st_size),
        chip_type=params.get("chip_type"),
        precision=params.get("precision"),
        status="READY",
        metadata_json=_json_text(
            {
                "source": "training_job",
                "training_job_id": int(job.id),
                "filename": artifact_path.name,
            }
        ),
        created_by=user.id,
    )
    with begin_transaction(db):
        crud_model_version.create_artifact(db, artifact)
    return _response(crud_model_version.get(db, row.id), db=db)


def register_completed_training_job_system(job_id: int) -> bool:
    """Register a completed job from a synchronous post-train worker.

    The API registration service is asynchronous, while training completion and
    restart recovery run in worker threads.  Keep one registration contract and
    bridge it here with a short-lived session.  Returning ``False`` means the
    job was not eligible or had already been registered, so repeated recovery
    scans do not create duplicate model versions.
    """

    from labelu.internal.adapter.persistence import crud_user
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        job = crud_yolo_train_job.get(db, int(job_id))
        if (
            job is None
            or str(getattr(job, "status", "") or "").upper() != "COMPLETED"
            or not str(getattr(job, "weights_path", "") or "").strip()
            or crud_model_version.get_by_training_job(db, int(job_id)) is not None
        ):
            return False
        dataset_version_id = _training_job_dataset_version_id(job)
        if dataset_version_id is None:
            logger.warning(
                "skip system model registration for training job {}: "
                "missing frozen dataset version",
                int(job_id),
            )
            return False
        dataset_version = crud_dataset.get_version(db, dataset_version_id)
        if (
            dataset_version is None
            or str(getattr(dataset_version, "status", "")).upper() != "FROZEN"
        ):
            logger.warning(
                "skip system model registration for training job {}: "
                "dataset version {} is missing or not frozen",
                int(job_id),
                dataset_version_id,
            )
            return False
        user = crud_user.get(db, int(job.created_by))
        if user is None:
            raise RuntimeError(f"训练作业 {int(job_id)} 的创建者不存在，无法登记模型")
        asyncio.run(
            register_from_training_job(
                db,
                RegisterTrainingJobModelCommand(training_job_id=int(job_id)),
                user,
            )
        )
        return True


def _persist_model_version(
    db: Session,
    user: User,
    *,
    algorithm: Any,
    dataset_id: int,
    dataset_version_id: int | None,
    training_job_id: int | None,
    parent_model_version_id: int | None,
    series_id: int | None = None,
    variant_id: int | None = None,
    parent_training_job_id: int | None = None,
    branch_key: str | None = None,
    selection_contract: dict[str, Any] | None = None,
    inheritance_mode: str | None = None,
    selection_contract_sha256: str | None = None,
    display_name: str,
    chip_type: str | None,
    precision: str | None,
    input_contract: dict[str, Any] | None = None,
    output_contract: dict[str, Any] | None = None,
    preprocess_contract: dict[str, Any] | None = None,
    postprocess_contract: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
) -> ModelVersionResponse:
    normalized_selection_contract = None
    if isinstance(selection_contract, dict):
        try:
            normalized_selection_contract = canonical_selection_contract(
                selection_contract
            )
        except (TypeError, ValueError) as exc:
            raise _error(
                "selection_contract 必须是有效的训练选择合同",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_400_BAD_REQUEST,
            ) from exc
    resolved_selection_contract_sha256 = (
        str(selection_contract_sha256 or "").strip() or None
    )
    if resolved_selection_contract_sha256 is None and normalized_selection_contract:
        resolved_selection_contract_sha256 = build_selection_contract_sha256(
            normalized_selection_contract
        )
    resolved_branch_key = str(branch_key or "").strip() or None
    if resolved_branch_key is None:
        resolved_branch_key = (
            branch_identity_sha256(normalized_selection_contract)
            if isinstance(normalized_selection_contract, dict)
            else resolved_selection_contract_sha256
        )
    with begin_transaction(db):
        # Serialize version allocation per algorithm. The unique constraint is
        # still the final guard when several registrations arrive together.
        if crud_algorithm.lock_definition(db, algorithm.id) is None:
            raise _error(
                "算法不存在",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
        version_no = crud_model_version.next_version_no(db, algorithm.id)
        branch_version_no = crud_model_version.next_branch_version_no(
            db,
            series_id=series_id,
            variant_id=variant_id,
        )
        row = ModelVersion(
            algorithm_id=algorithm.id,
            dataset_id=dataset_id,
            dataset_version_id=dataset_version_id,
            training_job_id=training_job_id,
            parent_model_version_id=parent_model_version_id,
            series_id=series_id,
            variant_id=variant_id,
            parent_training_job_id=parent_training_job_id,
            inheritance_mode=inheritance_mode,
            branch_key=resolved_branch_key,
            selection_contract_sha256=resolved_selection_contract_sha256,
            selection_contract_json=_json_text(normalized_selection_contract),
            model_key=f"{algorithm.algorithm_key}:v{version_no}",
            branch_version_no=branch_version_no,
            version_no=version_no,
            display_name=display_name,
            status="CANDIDATE",
            chip_type=chip_type,
            precision=precision,
            input_contract_json=_json_text(input_contract),
            output_contract_json=_json_text(output_contract),
            preprocess_contract_json=_json_text(preprocess_contract),
            postprocess_contract_json=_json_text(postprocess_contract),
            metrics_json=_json_text(metrics),
            manifest_json=_json_text(manifest),
            created_by=user.id,
        )
        crud_model_version.create(db, row)
        manifest_payload = dict(manifest or {})
        manifest_payload.setdefault("manifest_schema_version", "v1")
        manifest_payload["manifest_id"] = f"model-version:{int(row.id)}"
        manifest_payload["model_version_id"] = int(row.id)
        manifest_secret = str(
            getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_SECRET", "") or ""
        ).strip()
        manifest_required = _model_manifest_signature_required()
        if manifest_secret:
            row.manifest_json = _json_text(
                sign_model_manifest(
                    manifest_payload,
                    secret=manifest_secret,
                    key_id=str(
                        getattr(settings, "YOLO_MODEL_MANIFEST_HMAC_KEY_ID", "v1")
                        or "v1"
                    ),
                )
            )
        elif manifest_required:
            manifest_payload["manifest_signature_status"] = "MISSING_KEY"
            row.manifest_json = _json_text(manifest_payload)
        else:
            row.manifest_json = _json_text(manifest_payload)
        db.flush()
    return _response(row, db=db)


def _active_trainable_algorithm(db: Session, algorithm_id: int) -> Any:
    algorithm = crud_algorithm.get_definition_by_id(db, algorithm_id)
    if algorithm is None or str(algorithm.status).upper() != "ACTIVE":
        raise _error(
            "算法不存在或未启用",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    try:
        adapter_detail = adapter_capabilities(algorithm.training_adapter)
        adapter_available = True
    except ValueError:
        adapter_detail = {}
        adapter_available = False
    _, trainable, _ = capability_state(
        algorithm,
        adapter_available=adapter_available,
        adapter_detail=adapter_detail,
    )
    if not trainable:
        raise _error(
            "该算法当前不可用于模型训练或外部模型登记",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    return algorithm


def _accessible_dataset(db: Session, dataset_id: int, user: User) -> Any:
    dataset = crud_dataset.get_dataset(db, dataset_id)
    if dataset is None:
        raise _error(
            "数据集不存在",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    from labelu.internal.application.service import dataset as dataset_service

    if not dataset_service._can_access_dataset(db, dataset, user):
        raise _error(
            "无权使用该数据集",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    return dataset


async def create(
    db: Session, cmd: CreateModelVersionCommand, user: User
) -> ModelVersionResponse:
    algorithm = _active_trainable_algorithm(db, cmd.algorithm_id)
    dataset_version = crud_dataset.get_version(db, cmd.dataset_version_id)
    if dataset_version is None:
        raise _error(
            "数据集版本不存在",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    dataset = _accessible_dataset(db, int(dataset_version.dataset_id), user)
    if str(dataset_version.status).upper() != "FROZEN":
        raise _error(
            "只有已冻结的数据集版本可以生成模型版本",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    _validate_algorithm_dataset_compatibility(algorithm, dataset_version)
    if cmd.training_job_id is not None:
        train_job = crud_yolo_train_job.get(db, cmd.training_job_id)
        if train_job is None:
            raise _error(
                "训练任务不存在",
                ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
                status.HTTP_404_NOT_FOUND,
            )
        # A completed job is only usable inside the project that owns it.  The
        train_task = (
            crud_task.get(db=db, task_id=int(train_job.task_id))
            if train_job.task_id
            else None
        )
        if not is_task_superuser(user) and not user_has_task_access(user, train_task):
            raise _error(
                "无权使用该训练任务",
                ErrorCode.CODE_30001_NO_PERMISSION,
                status.HTTP_403_FORBIDDEN,
            )
        if str(train_job.status).upper() != "COMPLETED":
            raise _error(
                "训练任务尚未完成",
                ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                status.HTTP_409_CONFLICT,
            )
        if cmd.parent_training_job_id is not None and int(
            cmd.parent_training_job_id
        ) == int(train_job.id):
            raise _error(
                "模型版本不能继承当前正在登记的训练作业",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
    parent_training_job = None
    if cmd.parent_training_job_id is not None:
        parent_training_job = crud_yolo_train_job.get(
            db, int(cmd.parent_training_job_id)
        )
        if parent_training_job is None:
            raise _error(
                "父训练作业不存在",
                ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
                status.HTTP_404_NOT_FOUND,
            )
        parent_task = (
            crud_task.get(db=db, task_id=int(parent_training_job.task_id))
            if parent_training_job.task_id
            else None
        )
        if not is_task_superuser(user) and not user_has_task_access(user, parent_task):
            raise _error(
                "无权使用该父训练作业",
                ErrorCode.CODE_30001_NO_PERMISSION,
                status.HTTP_403_FORBIDDEN,
            )
        if str(parent_training_job.status).upper() != "COMPLETED":
            raise _error(
                "父训练作业尚未完成",
                ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                status.HTTP_409_CONFLICT,
            )
        if (
            cmd.parent_model_version_id is not None
            and getattr(parent_training_job, "id", None)
            and getattr(
                crud_model_version.get(db, int(cmd.parent_model_version_id)),
                "training_job_id",
                None,
            )
            not in (None, int(parent_training_job.id))
        ):
            raise _error(
                "父模型版本与父训练作业不匹配",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
    if cmd.parent_model_version_id is not None:
        parent = crud_model_version.get(db, cmd.parent_model_version_id)
        if parent is None or (
            not is_task_superuser(user) and int(parent.created_by or 0) != int(user.id)
        ):
            raise _error(
                "父模型版本不存在或不属于当前用户可管理的算法链",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_400_BAD_REQUEST,
            )
        if int(parent.algorithm_id) != int(cmd.algorithm_id):
            raise _error(
                "父模型与当前算法不一致，不能建立模型版本血缘",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
        parent_series_id = getattr(parent, "series_id", None)
        if (
            cmd.series_id is not None
            and parent_series_id is not None
            and int(cmd.series_id) != int(parent_series_id)
        ):
            raise _error(
                "父模型与目标模型系列不一致",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
    if cmd.selection_contract is not None:
        try:
            normalized_contract = canonical_selection_contract(cmd.selection_contract)
            computed_hash = build_selection_contract_sha256(normalized_contract)
        except (TypeError, ValueError) as exc:
            raise _error(
                "selection_contract 必须是有效的训练选择合同",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_400_BAD_REQUEST,
            ) from exc
        if (
            cmd.selection_contract_sha256
            and str(cmd.selection_contract_sha256).strip() != computed_hash
        ):
            raise _error(
                "selection_contract_sha256 与训练选择合同不一致",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
        branch_hash = branch_identity_sha256(normalized_contract)
        if cmd.branch_key and str(cmd.branch_key).strip() not in {
            branch_hash,
            computed_hash,
        }:
            raise _error(
                "branch_key 与训练选择合同不一致",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
    display_name = cmd.display_name.strip()
    if not display_name:
        raise _error(
            "模型显示名称不能为空",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    return _persist_model_version(
        db,
        user,
        algorithm=algorithm,
        dataset_id=int(dataset.id),
        dataset_version_id=int(dataset_version.id),
        training_job_id=cmd.training_job_id,
        parent_model_version_id=cmd.parent_model_version_id,
        series_id=cmd.series_id,
        variant_id=cmd.variant_id,
        parent_training_job_id=cmd.parent_training_job_id,
        branch_key=cmd.branch_key,
        selection_contract=cmd.selection_contract,
        inheritance_mode=cmd.inheritance_mode,
        selection_contract_sha256=cmd.selection_contract_sha256,
        display_name=display_name,
        chip_type=cmd.chip_type,
        precision=cmd.precision,
        input_contract=cmd.input_contract,
        output_contract=cmd.output_contract,
        preprocess_contract=cmd.preprocess_contract,
        postprocess_contract=cmd.postprocess_contract,
        metrics=cmd.metrics,
        manifest=cmd.manifest,
    )


async def import_external(
    db: Session, cmd: ImportExternalModelCommand, user: User
) -> ModelVersionResponse:
    """Promote an uploaded external weight into the canonical model repository.

    ``yolo-imported-models`` remains the upload/compatibility resource.  This
    endpoint creates a governed ``ModelVersion`` and a READY ``pt``
    artifact, preserving the external row id in the manifest and artifact
    metadata for audit and later evaluation.
    """
    imported = (
        crud_yolo_imported_model.get_by_id(db, cmd.imported_model_id)
        if is_task_superuser(user)
        else crud_yolo_imported_model.get_available_for_user(
            db,
            cmd.imported_model_id,
            user.id,
            public_owner_ids=crud_yolo_imported_model.public_owner_ids(db),
        )
    )
    if imported is None:
        raise _error(
            "外部模型不存在或当前用户不可用",
            ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
            status.HTTP_404_NOT_FOUND,
        )

    algorithm = _active_trainable_algorithm(db, cmd.algorithm_id)
    dataset = _accessible_dataset(db, cmd.dataset_id, user)
    contract = getattr(dataset, "label_contract", None)
    if contract is None:
        raise _error(
            "关联数据集尚未配置标签契约",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    _validate_algorithm_dataset_compatibility(algorithm, dataset)

    root = Path(settings.MEDIA_ROOT).resolve()
    artifact_path = (root / str(imported.weights_rel_path or "")).resolve()
    try:
        artifact_path.relative_to(root)
    except ValueError as exc:
        raise _error(
            "外部模型文件路径无效",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        ) from exc
    if not artifact_path.is_file():
        raise _error(
            "外部模型文件不存在，请重新上传",
            ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
            status.HTTP_404_NOT_FOUND,
        )

    # Validate the persisted relative path before creating the model version;
    # otherwise a legacy malformed row could leave an orphan model on failure.
    relative_path = _safe_relative_path(str(imported.weights_rel_path))
    digest = hashlib.sha256()
    with artifact_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    display_name = (
        cmd.display_name
        or imported.display_name
        or imported.original_filename
        or "外部模型"
    ).strip()
    manifest = dict(cmd.manifest or {})
    manifest.setdefault("source", "external_import")
    manifest["imported_model_id"] = int(imported.id)
    manifest["original_filename"] = imported.original_filename or None
    manifest["dataset_id"] = int(dataset.id)
    manifest["dataset_label_contract"] = {
        "id": int(contract.id),
        "revision": int(contract.revision),
        "status": str(contract.status),
        "annotation_kind": str(contract.annotation_kind),
        "label_set_signature": str(contract.label_set_signature),
        "class_order_signature": str(contract.class_order_signature),
    }

    row = _persist_model_version(
        db,
        user,
        algorithm=algorithm,
        dataset_id=int(dataset.id),
        dataset_version_id=None,
        training_job_id=None,
        parent_model_version_id=None,
        display_name=display_name,
        chip_type=cmd.chip_type,
        precision=cmd.precision,
        manifest=manifest,
    )
    artifact = ModelArtifact(
        model_version_id=row.id,
        artifact_type="pt",
        relative_path=relative_path,
        sha256=digest.hexdigest(),
        size_bytes=int(artifact_path.stat().st_size),
        chip_type=cmd.chip_type,
        precision=cmd.precision,
        status="READY",
        metadata_json=_json_text(
            {
                "source": "yolo_imported_model",
                "imported_model_id": int(imported.id),
                "original_filename": imported.original_filename,
            }
        ),
        created_by=user.id,
    )
    with begin_transaction(db):
        crud_model_version.create_artifact(db, artifact)
    return _response(crud_model_version.get(db, row.id), db=db)


async def list_by(
    db: Session,
    page: int,
    size: int,
    status_filter: str | None,
    user: User,
    source: str | None = None,
    training_job_id: int | None = None,
    search: str | None = None,
):
    rows, total = crud_model_version.list_accessible(
        db,
        user_id=user.id,
        unrestricted=is_task_superuser(user),
        page=page,
        size=size,
        status=status_filter,
        source=source,
        training_job_id=training_job_id,
        search=search,
        task_access_builtin_admin=is_consistent_pool_admin(user),
    )
    export_ids_by_job = (
        crud_yolo_bm1688_export.latest_by_job_ids(
            db,
            yolo_train_job_ids={
                int(row.training_job_id) for row in rows if row.training_job_id
            },
        )
        if any(row.training_job_id for row in rows)
        else {}
    )
    edge_statuses = _edge_validation_statuses_for_models(db, rows)
    return [
        _response(
            row,
            db=db,
            edge_validation_status=edge_statuses.get(int(row.id)),
            bm1688_export_id=(
                int(export_ids_by_job[int(row.training_job_id)].id)
                if row.training_job_id and int(row.training_job_id) in export_ids_by_job
                else None
            ),
        )
        for row in rows
    ], total


def _model_branch_identity(row: ModelVersion) -> tuple[str, int | str] | None:
    """Return the stable branch identity used for implicit comparisons.

    A family groups all variants under one series, but an omitted explicit
    parent must never use a version from another variant as its baseline.
    Legacy rows without lineage metadata intentionally retain family-local
    comparison behavior for backward compatibility.
    """

    variant_id = getattr(row, "variant_id", None)
    try:
        variant_id = int(variant_id)
    except (TypeError, ValueError):
        variant_id = None
    if variant_id and variant_id > 0:
        return ("variant", variant_id)
    branch_key = str(getattr(row, "branch_key", "") or "").strip()
    if branch_key:
        return ("branch", branch_key)
    series_id = getattr(row, "series_id", None)
    try:
        series_id = int(series_id)
    except (TypeError, ValueError):
        series_id = None
    if series_id and series_id > 0:
        return ("series", series_id)
    return None


def _same_model_branch(left: ModelVersion, right: ModelVersion) -> bool:
    left_identity = _model_branch_identity(left)
    right_identity = _model_branch_identity(right)
    return left_identity == right_identity if left_identity or right_identity else True


async def list_families(
    db: Session,
    page: int,
    size: int,
    status_filter: str | None,
    user: User,
    search: str | None = None,
    artifact_type: str | None = None,
    model_id: int | None = None,
    training_job_id: int | None = None,
    export_id: int | None = None,
    task_id: int | None = None,
    training_recipe_key: str | None = None,
    training_recipe_version: int | None = None,
    label_ids: list[int] | None = None,
    label_set_signature: str | None = None,
    class_order_signature: str | None = None,
):
    """Return family pagination with full versions for the selected page.

    Deep links identify one family through a model version, training job or
    compile export; they intentionally retain the other historical versions.
    """
    focus_job_id = training_job_id
    if model_id is not None:
        linked_model = crud_model_version.get(db, int(model_id))
        focus_job_id = (
            int(linked_model.training_job_id)
            if linked_model and linked_model.training_job_id
            else -1
        )
    elif export_id is not None:
        export = crud_yolo_bm1688_export.get(db, int(export_id))
        focus_job_id = (
            int(export.yolo_train_job_id) if export and export.yolo_train_job_id else -1
        )
    family_task_id = int(task_id) if task_id is not None else None
    family_algorithm_id = None
    if focus_job_id is not None:
        job = crud_yolo_train_job.get(db, int(focus_job_id))
        model = crud_model_version.get_by_training_job(db, int(focus_job_id))
        if job is not None and model is not None:
            family_task_id = int(job.task_id)
            family_algorithm_id = int(model.algorithm_id)
        else:
            family_task_id, family_algorithm_id = -1, -1

    expected_label_signature = str(label_set_signature or "").strip() or None
    expected_order_signature = str(class_order_signature or "").strip() or None
    if label_ids:
        # Label-dimension training uses a subset contract.  Derive the exact
        # signatures on the server so the model catalog applies the same
        # ordering/hash rules as iteration-preview and job creation.
        labels = annotation_contract.active_labels_by_ids(db, label_ids)
        expected_label_signature = build_label_set_signature(
            [int(row.id) for row in labels]
        )
        expected_order_signature = build_class_order_signature(
            [str(row.label_value) for row in labels]
        )

    families, total = crud_model_version.list_accessible_families(
        db,
        user_id=user.id,
        unrestricted=is_task_superuser(user),
        page=page,
        size=size,
        status=status_filter,
        search=search,
        artifact_type=artifact_type,
        family_task_id=family_task_id,
        family_algorithm_id=family_algorithm_id,
        training_recipe_key=training_recipe_key,
        training_recipe_version=training_recipe_version,
        label_set_signature=expected_label_signature,
        class_order_signature=expected_order_signature,
        task_access_builtin_admin=is_consistent_pool_admin(user),
    )
    rows = [row for _task_id, _algorithm_id, versions in families for row in versions]
    export_ids_by_job = (
        crud_yolo_bm1688_export.latest_by_job_ids(
            db,
            yolo_train_job_ids={
                int(row.training_job_id) for row in rows if row.training_job_id
            },
        )
        if any(row.training_job_id for row in rows)
        else {}
    )
    edge_statuses = _edge_validation_statuses_for_models(db, rows)
    responses = {
        int(row.id): _response(
            row,
            db=db,
            edge_validation_status=edge_statuses.get(int(row.id)),
            bm1688_export_id=(
                int(export_ids_by_job[int(row.training_job_id)].id)
                if row.training_job_id and int(row.training_job_id) in export_ids_by_job
                else None
            ),
        )
        for row in rows
    }

    result = []
    for task_id, algorithm_id, versions in families:
        version_by_id = {int(row.id): row for row in versions}
        def _version_sort_key(row: ModelVersion) -> tuple[float, int]:
            value = row.created_at or row.updated_at
            if value is None:
                return (0.0, int(row.id))
            try:
                return (float(value.timestamp()), int(row.id))
            except (AttributeError, TypeError, ValueError, OverflowError):
                return (0.0, int(row.id))

        ordered_versions = sorted(versions, key=_version_sort_key)
        for current in versions:
            parent_id = (
                int(current.parent_model_version_id)
                if current.parent_model_version_id
                and int(current.parent_model_version_id) in version_by_id
                else None
            )
            if parent_id is None:
                same_branch_versions = [
                    row for row in ordered_versions if _same_model_branch(current, row)
                ]
                current_index = next(
                    (index for index, row in enumerate(same_branch_versions) if row.id == current.id),
                    -1,
                )
                parent = same_branch_versions[current_index - 1] if current_index > 0 else None
                parent_id = int(parent.id) if parent is not None else None
            previous = version_by_id.get(parent_id) if parent_id is not None else None
            comparison = _edge_version_comparison(
                edge_statuses.get(int(current.id)),
                edge_statuses.get(parent_id) if parent_id is not None else None,
                previous_model_version_id=parent_id,
                previous_version_no=(int(previous.version_no) if previous is not None else None),
            )
            responses[int(current.id)].edge_version_comparison = comparison
            responses[int(current.id)].edge_version_improvement_status = comparison["status"]
        latest = max(versions, key=_version_sort_key)
        series_id = getattr(latest, "series_id", None) or task_id
        series = getattr(latest, "series", None)
        manifest = _json_value(latest.manifest_json) or {}
        task = crud_task.get(db=db, task_id=int(task_id))
        display_name = str(
            manifest.get("task_name")
            or getattr(task, "name", None)
            or str(latest.display_name).split(" · 作业 #", 1)[0]
        )
        published = next(
            (
                row
                for row in versions
                if str(row.status or "").upper() == "PUBLISHED"
                or row.published_catalog_id is not None
            ),
            None,
        )
        updated_values = [
            row.updated_at or row.created_at
            for row in versions
            if row.updated_at or row.created_at
        ]
        result.append(
            ModelFamilyResponse(
                family_key=f"series:{int(series_id)}:algorithm:{algorithm_id}",
                display_name=(
                    str(getattr(series, "name", "") or display_name)
                    if getattr(latest, "series_id", None)
                    else display_name
                ),
                task_id=int(task_id),
                algorithm_id=int(algorithm_id),
                latest_version_id=int(latest.id),
                version_count=len(versions),
                published_version_id=(
                    int(published.id) if published is not None else None
                ),
                updated_at=max(updated_values) if updated_values else None,
                versions=[responses[int(row.id)] for row in versions],
            )
        )
    return result, total


async def get(db: Session, model_version_id: int, user: User) -> ModelVersionResponse:
    return _response(_model_or_raise(db, model_version_id, user), db=db)


async def add_artifact(
    db: Session, model_version_id: int, cmd: CreateModelArtifactCommand, user: User
) -> ModelVersionResponse:
    row = _model_or_raise(db, model_version_id, user, manage=True)
    if row.status == "PUBLISHED":
        raise _error(
            "已发布模型版本不可修改",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if cmd.artifact_type == "bmodel":
        chip = str(cmd.chip_type or row.chip_type or "").upper()
        precision = str(cmd.precision or "").upper()
        if chip == "BM1688" and precision not in {"FP32", "FP16", "INT8"}:
            raise _error(
                "BM1688 BModel 制品必须明确标注 FP32、FP16 或 INT8 精度",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_400_BAD_REQUEST,
            )
    existing = next(
        (
            item
            for item in row.artifacts
            if item.artifact_type == cmd.artifact_type
            and item.sha256 == cmd.sha256.lower()
        ),
        None,
    )
    if existing:
        return _response(row, db=db)
    artifact = ModelArtifact(
        model_version_id=row.id,
        artifact_type=cmd.artifact_type,
        relative_path=_safe_relative_path(cmd.relative_path),
        sha256=_validate_sha(cmd.sha256),
        size_bytes=cmd.size_bytes,
        chip_type=cmd.chip_type,
        precision=cmd.precision,
        metadata_json=_json_text(cmd.metadata),
        created_by=user.id,
    )
    with begin_transaction(db):
        crud_model_version.create_artifact(db, artifact)
    return _response(crud_model_version.get(db, row.id), db=db)


async def add_evidence(
    db: Session,
    model_version_id: int,
    cmd: CreateModelQualityEvidenceCommand,
    user: User,
) -> ModelVersionResponse:
    row = _model_or_raise(db, model_version_id, user, manage=True)
    if row.status == "PUBLISHED":
        raise _error(
            "已发布模型版本的质量证据不可修改",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    gate_type = str(cmd.gate_type).lower()
    if crud_model_version.get_evidence(db, row.id, gate_type, cmd.revision):
        raise _error(
            "该质量门禁版本已存在",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    evidence_status = str(cmd.status).upper()
    _validate_evidence_stage(row, gate_type, evidence_status)
    evidence = ModelQualityEvidence(
        model_version_id=row.id,
        gate_type=gate_type,
        revision=cmd.revision,
        status=evidence_status,
        metrics_json=_json_text(cmd.metrics),
        evidence_uri=cmd.evidence_uri,
        evidence_sha256=(
            _validate_sha(cmd.evidence_sha256) if cmd.evidence_sha256 else None
        ),
        evaluator=cmd.evaluator,
        reason=cmd.reason,
        created_by=user.id,
    )
    with begin_transaction(db):
        crud_model_version.create_evidence(db, evidence)
        # A failed gate makes the candidate explicitly ineligible. Once all
        # gates pass, it becomes VALIDATED and may be submitted for publishing.
        latest = _gate_summary(row)
        latest[gate_type] = evidence.status
        if evidence.status == "FAIL":
            row.status = "QUALITY_FAILED"
        elif all(latest[gate] == "PASS" for gate in GATE_TYPES):
            row.status = PUBLISHABLE_STATUS
        elif row.status == "QUALITY_FAILED":
            row.status = "CANDIDATE"
        db.flush()
    return _response(crud_model_version.get(db, row.id), db=db)


async def publish(
    db: Session, model_version_id: int, cmd: PublishModelVersionCommand, user: User
) -> ModelVersionResponse:
    row = _model_or_raise(db, model_version_id, user, manage=True)
    if row.status == "PUBLISHED":
        return _response(row, db=db)
    edge_validation_status = _edge_validation_status_for_model(db, row)
    gate_summary = _effective_gate_summary(
        db,
        row,
        edge_validation_status=edge_validation_status,
    )
    if any(gate_summary[gate] != "PASS" for gate in GATE_TYPES):
        raise _error(
            "模型版本尚未完成训练、测试、编译或边端检查，禁止发布",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    if not _has_ready_artifact(row, DEPLOYABLE_ARTIFACT_TYPES):
        raise _error(
            "模型版本缺少 READY 状态的 BM1688 BModel 制品",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    _assert_bm1688_bundle_ready(row)
    _assert_model_version_manifest_ready(row)
    if cmd.aicm_model_set_id is not None and cmd.published_catalog_id is not None:
        raise _error(
            "AICM 模型编号和已有平台发布目录不能同时传入",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    if (
        cmd.published_catalog_id is not None
        and crud_published_model_catalog.get(db, cmd.published_catalog_id) is None
    ):
        raise _error(
            "外部发布目录记录不存在",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    with begin_transaction(db):
        if cmd.aicm_model_set_id is not None:
            # The external AICM publication already owns the numeric model
            # id.  Register that result and materialize the platform package
            # before marking this canonical version as published.
            from labelu.internal.application.service import published_model_catalog

            published_model_catalog.register_model_version_publication(
                db,
                model_version=row,
                user=user,
                aicm_model_set_id=int(cmd.aicm_model_set_id),
                environment_id=str(cmd.aicm_environment_id or ""),
                package_path=str(cmd.package_path or ""),
            )
        if cmd.published_catalog_id is not None:
            row.published_catalog_id = cmd.published_catalog_id
        row.status = "PUBLISHED"
        row.published_by = user.id
        row.published_at = datetime.now()
        db.flush()
    return _response(crud_model_version.get(db, row.id), db=db)
