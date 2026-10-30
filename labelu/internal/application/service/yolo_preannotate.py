"""任务维度：用导入或本系统训练的 YOLO 权重批量推理并写入预标注。"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from functools import partial
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_pre_annotation,
    crud_sample,
    crud_task,
    crud_yolo_bm1688_export,
    crud_yolo_imported_model,
    crud_yolo_preannotate_job,
    crud_yolo_train_job,
)
from labelu.internal.application.command.yolo_preannotate import (
    BatchYoloPreannotateCommand,
)
from labelu.internal.application.response.yolo_preannotate import (
    YoloPreannotateCandidateResponse,
    YoloPreannotateJobResponse,
    YoloPreannotateModelsResponse,
)
from labelu.internal.application.service import (
    annotation_contract as annotation_contract_service,
)
from labelu.internal.application.service import auto_label as auto_label_mod
from labelu.internal.application.service import task as task_service
from labelu.internal.application.service.datasource import get_presigned_url
from labelu.internal.application.service.task_background_jobs import (
    GROUP_GEOMETRY,
    assert_no_active_task_background_job,
)
from labelu.internal.application.service.yolo_dataset import build_yolo_rect_class_names
from labelu.internal.application.service.yolo_train import (
    _assert_task_access,
    _training_val_metrics_for_job,
    resolve_weights_path,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.label_contract import class_order_signature
from labelu.internal.common.optional_dependencies import ensure_ultralytics_installed
from labelu.internal.common.plate_recognition_profile import (
    DEFAULT_PLATE_DETECTION_CONFIG,
    DEFAULT_PLATE_RECOGNITION_CONFIG,
    normalize_plate_recognition_task_config,
)
from labelu.internal.common.storage import get_model_read_url
from labelu.internal.common.tag_tool_frontend_payload import (
    expand_tag_tool_result_items,
)
from labelu.internal.common.task_access import (
    is_consistent_pool_admin,
    is_task_superuser,
)
from labelu.internal.common.yolo_detection_geometry import configured_geometry_tools
from labelu.internal.domain.models.auto_label_job import AutoLabelStatus
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus

YOLO_PRE_META = "yolo_generated"
AI_PREANNOTATION_SOURCE = "ai_generated"
_LOG = "[yolo-preannotate]"
PLATE_RECOGNITION_JOB_TYPE = "plate_recognition"
PLATE_DETECTION_JOB_TYPE = "plate_detection"

# A small, explicit compatibility map for labels that are semantically
# equivalent across common YOLO datasets.  Model labels are otherwise matched
# strictly to the task contract: silently mapping a generic class such as
# ``person`` to a riding class would create incorrect annotations.  The
# person/pedestrian pair is safe because the task contract uses ``pedestrian``
# for the generic person class and the model's ``person`` output is that same
# generic class.
_MODEL_LABEL_COMPATIBILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "person": ("pedestrian",),
    "pedestrian": ("person",),
}


def _recommended_conf_for_metrics(metrics) -> float | None:
    """将验证集 mAP50-95 转为 UI 可直接使用的 conf 建议值。"""
    if metrics is None or metrics.map50_95 is None:
        return None
    try:
        score = min(max(float(metrics.map50_95), 0.05), 1.0)
    except (TypeError, ValueError):
        return None
    return round(min(1.0, math.ceil((score - 1e-9) / 0.05) * 0.05), 2)


def _resolve_plate_component_artifacts(job) -> tuple[dict[str, Path], dict[str, Any]]:
    try:
        metadata = json.loads(job.component_artifacts_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
            message="Plate recognition component metadata is invalid",
        ) from exc
    if not isinstance(metadata, dict):
        metadata = {}

    filenames = {
        "pose": "plate_pose_best.pt",
        "ocr": "plate_ocr_best.pt",
        "color": "plate_color_best.pt",
        "plate_type": "plate_type_best.pt",
    }
    pose_path = resolve_weights_path(job.weights_path)
    artifacts: dict[str, Path] = {"pose": pose_path}
    for component in ("ocr", "color", "plate_type"):
        component_meta = metadata.get(component)
        if not isinstance(component_meta, dict):
            component_meta = {}
        stored = component_meta.get("pt") or str(
            pose_path.parent / filenames[component]
        )
        artifacts[component] = resolve_weights_path(stored)
    return artifacts, metadata


def _resolve_plate_detection_artifact(job) -> tuple[Path, dict[str, Any]]:
    try:
        metadata = json.loads(job.component_artifacts_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
            message="Plate detection component metadata is invalid",
        ) from exc
    if not isinstance(metadata, dict):
        metadata = {}
    pose = metadata.get("pose") if isinstance(metadata.get("pose"), dict) else {}
    pose_path = resolve_weights_path(pose.get("pt") or job.weights_path)
    if not pose_path.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
            message="Plate detection Pose 权重不存在",
        )
    return pose_path, metadata


def _explicit_target_sample_ids(cmd: BatchYoloPreannotateCommand) -> list[int] | None:
    """合并 sample_ids 与 sample_id，去重保序；无显式 id 时返回 None。"""
    combined: list[int] = []
    if cmd.sample_ids:
        for raw in cmd.sample_ids:
            combined.append(int(raw))
    if cmd.sample_id is not None:
        sid = int(cmd.sample_id)
        if sid not in combined:
            combined.append(sid)
    if not combined:
        return None
    seen: set[int] = set()
    uniq: list[int] = []
    for x in combined:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def is_yolo_preannotation_data(raw: Any) -> bool:
    """判断预标注 ``data`` JSON 是否由 YOLO 批量写入（与列表接口 ``is_yolo_pre_annotated`` 一致）。"""
    try:
        if not raw:
            return False
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return (parsed.get("meta") or {}).get("source_type") == YOLO_PRE_META
    except Exception:
        return False


def _is_yolo_preannotation_row(row: TaskPreAnnotation) -> bool:
    return is_yolo_preannotation_data(row.data)


def _collect_values_from_tag_result_items(items: list | None) -> dict[str, list[str]]:
    """把 tagTool.result 多条（每维一条）合并为 field_key -> value_en 列表。"""
    out: dict[str, list[str]] = {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        vp = it.get("value")
        if not isinstance(vp, dict):
            continue
        for k, arr in vp.items():
            sk = str(k)
            if isinstance(arr, list):
                out[sk] = [str(x) for x in arr if x is not None and str(x).strip()]
            elif arr is not None and str(arr).strip():
                out[sk] = [str(arr)]
    return out


def _load_latest_ai_tag_tool_result_items(
    db: Session, task_id: int, sample_name: str
) -> list | None:
    """取该样本最新一条 AI 预标注里的 tagTool.result（画布通常只展示最新一条预标注）。"""
    rows, _ = crud_pre_annotation.list_by(
        db=db,
        task_id=task_id,
        sample_name=sample_name,
        page=0,
        size=200,
    )
    for row in sorted(rows, key=lambda r: r.id or 0, reverse=True):
        try:
            raw = row.data
            if not raw:
                continue
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        if (parsed.get("meta") or {}).get("source_type") != AI_PREANNOTATION_SOURCE:
            continue
        ann = parsed.get("annotations") or {}
        block = ann.get("tagTool")
        if not isinstance(block, dict):
            continue
        res = block.get("result")
        if isinstance(res, list) and res:
            return list(res)
    return None


def merge_ai_tag_tool_results_into_yolo_payload(
    payload: dict[str, Any],
    ai_tag_result_items: list | None,
) -> None:
    """将 AI 全局 tag 合并进即将落库的 YOLO 预标注：同 field_key 以 AI 为准。"""
    if not ai_tag_result_items:
        return
    annotations = payload.setdefault("annotations", {})
    yolo_items: list = []
    tg = annotations.get("tagTool")
    if isinstance(tg, dict) and isinstance(tg.get("result"), list):
        yolo_items = tg["result"]
    merged_map = _collect_values_from_tag_result_items(yolo_items)
    ai_map = _collect_values_from_tag_result_items(ai_tag_result_items)
    for k, v in ai_map.items():
        merged_map[k] = v
    if not merged_map:
        return
    wrapper = {
        "id": str(uuid.uuid4()),
        "order": 0,
        "label": "",
        "visible": True,
        "type": "tag",
        "valid": True,
        "isVisible": True,
        "value": merged_map,
    }
    annotations["tagTool"] = {
        "toolName": "tagTool",
        "result": expand_tag_tool_result_items([wrapper]),
    }


def list_jobs_for_task(
    db: Session,
    task_id: int,
    current_user: User,
    *,
    search: str | None = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[YoloPreannotateJobResponse], int]:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    rows, total = crud_yolo_preannotate_job.list_by_task_id(
        db, task_id, search=search, page=page, size=size
    )
    data = [
        YoloPreannotateJobResponse(
            id=job.id,
            task_id=job.task_id,
            status=job.status,
            sample_count=job.sample_count or 0,
            processed_count=job.processed_count or 0,
            success_count=job.success_count or 0,
            failed_count=job.failed_count or 0,
            model_kind=job.model_kind,
            model_id=job.model_id,
            error_message=job.error_message,
            created_at=job.created_at,
        )
        for job in rows
    ]
    return data, total


def list_models_for_task(
    db: Session,
    task_id: int,
    current_user: User,
    *,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
) -> YoloPreannotateModelsResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    unrestricted = is_task_superuser(current_user)
    builtin_admin = is_consistent_pool_admin(current_user)
    task_preset_slug, _ = _task_rect_signature(task)
    task_contract = _task_contract_descriptor(db, task)
    task_label_signature = task_contract["labels"]
    task_labels = list(task_contract["ordered_labels"])
    if not task_label_signature:
        return YoloPreannotateModelsResponse(
            items=[],
            task_labels=[],
            task_preset_slug=task_preset_slug,
            total=0,
            page=max(int(page), 0),
            size=0,
        )

    items: list[YoloPreannotateCandidateResponse] = []
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    normalized_search = str(search or "").strip()

    # Candidate compatibility is evaluated in Python because the model label
    # manifest is stored with the weight metadata.  Fetch the complete
    # database-filtered search result first, then apply compatibility sorting
    # and pagination once across imported and trained candidates.  This avoids
    # duplicate pages caused by paginating each source independently.
    candidate_fetch_size = 5000

    imp_rows, _ = crud_yolo_imported_model.list_for_user(
        db,
        current_user.id,
        search=normalized_search or None,
        name=None,
        category=None,
        page=0,
        size=candidate_fetch_size,
        list_all_users_models=unrestricted,
        public_owner_ids=(
            [] if unrestricted else crud_yolo_imported_model.public_owner_ids(db)
        ),
    )
    for r in imp_rows:
        try:
            labels = _imported_model_label_signature(r)
        except Exception as exc:
            logger.opt(exception=exc).warning(
                "{} skip_unreadable_imported_model task_id={} model_id={}",
                _LOG,
                task_id,
                getattr(r, "id", None),
            )
            continue
        matched, missing, extra = _model_task_label_coverage(task_contract, labels)
        if not matched:
            continue
        dn = (r.display_name or "").strip()
        cat = (r.category or "").strip() or "未分类"
        items.append(
            YoloPreannotateCandidateResponse(
                kind="imported",
                id=r.id,
                title=dn or f"导入模型 #{r.id}",
                detail="",
                category=cat,
                imgsz=_imported_model_imgsz(r),
                labels=list(labels),
                matched_labels=matched,
                missing_task_labels=missing,
                extra_model_labels=extra,
                preset_slug=(r.category or "").strip(),
                recommended=False,
                training_val_metrics=None,
                recommended_conf=None,
            )
        )

    jobs, _ = crud_yolo_train_job.list_completed_models_for_user(
        db,
        current_user.id,
        task_name=None,
        search=normalized_search or None,
        page=0,
        size=candidate_fetch_size,
        task_access_unrestricted=unrestricted,
        task_access_builtin_admin=builtin_admin,
    )
    for job, src_task in jobs:
        src_preset_slug = _task_rect_signature(src_task)[0]
        src_labels, _, _ = _frozen_job_contract(job, src_task)
        matched, missing, extra = _model_task_label_coverage(task_contract, src_labels)
        if not matched:
            continue
        title = f"{src_task.name} · 作业#{job.id}"
        mw = (job.model_weights or "").strip()
        training_metrics = _training_val_metrics_for_job(job)
        items.append(
            YoloPreannotateCandidateResponse(
                kind="trained",
                id=job.id,
                title=title,
                detail=mw,
                category="本系统训练",
                imgsz=job.imgsz,
                labels=list(src_labels),
                matched_labels=matched,
                missing_task_labels=missing,
                extra_model_labels=extra,
                preset_slug=src_preset_slug,
                recommended=False,
                training_val_metrics=training_metrics,
                recommended_conf=_recommended_conf_for_metrics(training_metrics),
            )
        )

    items.sort(
        key=lambda item: (
            bool(item.missing_task_labels),
            -len(item.matched_labels),
            item.recommended_conf is None,
            -(
                float(item.training_val_metrics.map50_95)
                if item.training_val_metrics is not None
                and item.training_val_metrics.map50_95 is not None
                else -1.0
            ),
            -(item.recommended_conf or 0.0),
            item.kind != "trained",
            -int(item.id),
        )
    )
    if items:
        # Recommendation is contract/quality based; preset slugs are not part
        # of model compatibility anymore.
        items[0] = items[0].copy(update={"recommended": True})
    total = len(items)
    start = page * size
    page_items = items[start : start + size]
    return YoloPreannotateModelsResponse(
        items=page_items,
        task_labels=task_labels,
        task_preset_slug=task_preset_slug,
        total=total,
        page=page,
        size=len(page_items),
    )


def _normalize_label_signature(labels: list[str]) -> tuple[str, ...]:
    return tuple(sorted({str(label).strip() for label in labels if str(label).strip()}))


def _task_rect_class_names(task) -> list[str]:
    task_cfg = task_service._parse_task_config(getattr(task, "config", None))
    return [
        str(value).strip()
        for value in build_yolo_rect_class_names(task_cfg)
        if str(value).strip()
    ]


def _task_supports_rect_detection(task_config: dict[str, Any] | None) -> bool:
    return "rectTool" in configured_geometry_tools(task_config or {})


def _task_contract_descriptor(db: Session, task) -> dict[str, Any]:
    try:
        contract = annotation_contract_service.current_contract(db, int(task.id))
    except Exception:
        # Some callers only provide an in-memory task object (and legacy tasks
        # may not have a contract row yet); the task config remains a valid
        # compatibility fallback.
        contract = None
    if contract is not None:
        try:
            snapshot = json.loads(contract.labels_snapshot_json or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot = []
        ordered = [
            str(item.get("label_value") or "").strip()
            for item in snapshot
            if isinstance(item, dict) and str(item.get("label_value") or "").strip()
        ]
        if ordered:
            return {
                "ordered_labels": ordered,
                "labels": _normalize_label_signature(ordered),
                "label_set_signature": str(contract.label_set_signature or "").strip()
                or None,
                "class_order_signature": str(
                    contract.class_order_signature or ""
                ).strip()
                or None,
            }
    ordered = _task_rect_class_names(task)
    return {
        "ordered_labels": ordered,
        "labels": _normalize_label_signature(ordered),
        "label_set_signature": None,
        "class_order_signature": class_order_signature(ordered) if ordered else None,
    }


def _frozen_job_contract(
    job, src_task
) -> tuple[tuple[str, ...], str | None, str | None]:
    try:
        parsed = json.loads(getattr(job, "class_names_json", None) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = []
    if isinstance(parsed, list):
        ordered = [str(value).strip() for value in parsed if str(value).strip()]
        if ordered:
            return (
                _normalize_label_signature(ordered),
                str(getattr(job, "label_set_signature", None) or "").strip() or None,
                str(getattr(job, "class_order_signature", None) or "").strip()
                or class_order_signature(ordered),
            )
    ordered = _task_rect_class_names(src_task)
    return (
        _normalize_label_signature(ordered),
        str(getattr(job, "label_set_signature", None) or "").strip() or None,
        str(getattr(job, "class_order_signature", None) or "").strip()
        or (class_order_signature(ordered) if ordered else None),
    )


def _frozen_job_label_signature(job, src_task) -> tuple[str, ...]:
    """Return the labels frozen from the training data.yaml when available."""
    return _frozen_job_contract(job, src_task)[0]


def _model_contract_matches_task(
    task_contract: dict[str, Any],
    *,
    labels: tuple[str, ...],
    label_set_sig: str | None,
    class_order_sig: str | None,
) -> bool:
    if labels != task_contract["labels"]:
        return False
    expected_set = task_contract.get("label_set_signature")
    if expected_set and label_set_sig and expected_set != label_set_sig:
        return False
    expected_order = task_contract.get("class_order_signature")
    if expected_order and class_order_sig and expected_order != class_order_sig:
        return False
    return True


def _model_task_label_coverage(
    task_contract: dict[str, Any], labels: tuple[str, ...]
) -> tuple[list[str], list[str], list[str]]:
    """Describe which task labels a detection model can safely write.

    Exact label matches remain the default.  A model may also expose the
    generic ``person`` class while the task contract calls that class
    ``pedestrian`` (or the reverse); that one explicit compatibility alias is
    consumed for matching and is therefore not reported as an unusable extra
    model class.  All other mismatches stay visible and are ignored at write
    time.
    """
    task_ordered = [
        str(label).strip()
        for label in task_contract.get("ordered_labels")
        or task_contract.get("labels")
        or []
        if str(label).strip()
    ]
    model_ordered = [str(label).strip() for label in labels if str(label).strip()]
    task_set = set(task_ordered)
    model_set = set(model_ordered)
    exact_matches = {label for label in task_ordered if label in model_set}
    alias_matches: dict[str, str] = {}
    for model_label in model_ordered:
        if model_label in task_set:
            continue
        for task_alias in _MODEL_LABEL_COMPATIBILITY_ALIASES.get(
            model_label.casefold(), ()
        ):
            target = next(
                (
                    label
                    for label in task_ordered
                    if label.casefold() == task_alias.casefold()
                ),
                None,
            )
            if target and target not in exact_matches and target not in alias_matches:
                alias_matches[target] = model_label
                break
    matched = [
        label
        for label in task_ordered
        if label in exact_matches or label in alias_matches
    ]
    missing = [
        label
        for label in task_ordered
        if label not in exact_matches and label not in alias_matches
    ]
    consumed_model_labels = exact_matches | set(alias_matches.values())
    extra = [label for label in model_ordered if label not in consumed_model_labels]
    return matched, missing, extra


def resolve_published_model(
    db: Session,
    published_model_id: str,
) -> dict[str, Any]:
    """Resolve an external published model ID to usable local YOLO assets."""
    external_id = str(published_model_id or "").strip()
    if not external_id:
        return {"ready": False, "model_id": external_id, "reason": "model_id_missing"}

    # Keep the existing strategy-evolution mapping as the source of truth. The
    # lazy import avoids loading that orchestration module during normal YOLO
    # service startup and keeps the legacy mapping fallback intact.
    from labelu.internal.application.service.strategy_evolution import (
        _resolve_export_id_from_model_id,
    )

    export_id, resolution = _resolve_export_id_from_model_id(db, external_id)
    result: dict[str, Any] = {
        "ready": False,
        **(resolution if isinstance(resolution, dict) else {}),
        "model_id": external_id,
    }
    if result.get("resolved_by") == "direct_export_id":
        return {
            **result,
            "reason": "published_model_mapping_missing",
            "export_id": None,
        }
    if export_id is None:
        return result

    export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    if not export_row:
        result.update({"export_id": int(export_id), "reason": "export_not_found"})
        return result

    source_train_job_id = int(getattr(export_row, "yolo_train_job_id", 0) or 0)
    source_task_id = int(getattr(export_row, "task_id", 0) or 0)
    result.update(
        {
            "export_id": int(export_row.id),
            "source_train_job_id": source_train_job_id,
            "source_task_id": source_task_id,
            "source_export_status": str(
                getattr(export_row, "status", "") or ""
            ).upper(),
        }
    )
    if source_train_job_id <= 0:
        result["reason"] = "source_train_job_missing"
        return result

    job = crud_yolo_train_job.get(db=db, job_id=source_train_job_id)
    source_task = (
        crud_task.get(db=db, task_id=source_task_id) if source_task_id else None
    )
    source_category_id = int(getattr(job, "dataset_category_id", 0) or 0) if job else 0
    if not source_category_id and source_task is not None:
        source_category_id = int(getattr(source_task, "dataset_category_id", 0) or 0)
    if source_category_id:
        result["dataset_category_id"] = source_category_id
    if not job or job.status != YoloTrainStatus.COMPLETED.value:
        result["reason"] = "source_train_job_not_completed"
        return result
    try:
        weights_path = resolve_weights_path(job.weights_path)
    except Exception:
        result["reason"] = "weights_path_invalid"
        return result
    if not weights_path.is_file():
        result["reason"] = "weights_file_missing"
        result["weights_path"] = str(weights_path)
        return result

    labels, label_set_sig, class_order_sig = _frozen_job_contract(job, source_task)
    if not labels:
        result["reason"] = "model_labels_missing"
        return result
    result.update(
        {
            "ready": True,
            "weights_path": str(weights_path),
            "imgsz": int(job.imgsz or 640),
            "job_type": str(getattr(job, "job_type", None) or "yolo_detection"),
            "labels": list(labels),
            "label_set_signature": label_set_sig,
            "class_order_signature": class_order_sig,
        }
    )
    return result


def resolve_requested_candidate_for_task(
    *,
    db: Session,
    task,
    current_user: User,
    published_model_id: str,
) -> dict[str, Any] | None:
    """Return the exact published model candidate after task-contract checks."""
    task_config = task_service._parse_task_config(getattr(task, "config", None))
    if not _task_supports_rect_detection(task_config):
        return None
    resolved = resolve_published_model(db, published_model_id)
    if not resolved.get("ready"):
        return None
    task_contract = _task_contract_descriptor(db, task)
    labels = tuple(
        str(value).strip()
        for value in resolved.get("labels") or []
        if str(value).strip()
    )
    matched_labels, _, _ = _model_task_label_coverage(task_contract, labels)
    if not matched_labels:
        return None
    return {
        "model_kind": "trained",
        "model_id": int(resolved["source_train_job_id"]),
        "weights_path": resolve_weights_path(resolved["weights_path"]),
        "imgsz": int(resolved.get("imgsz") or 640),
        # A model may cover only part of the task contract.  Restrict the
        # writer to the intersection so it cannot emit labels this task does
        # not declare; the remaining task labels stay available for manual
        # annotation or another explicit run.
        "rect_labels": matched_labels,
        "job_type": resolved.get("job_type") or "yolo_detection",
        "published_model_id": str(published_model_id),
        "export_id": int(resolved["export_id"]),
        "source_train_job_id": int(resolved["source_train_job_id"]),
    }


def _trained_model_quality_rank(job) -> tuple[Any, ...]:
    """Deterministic auto-selection: validation quality, then newest job."""
    try:
        metrics = _training_val_metrics_for_job(job)
    except Exception:
        metrics = None
    map50_95 = (
        float(metrics.map50_95) if metrics and metrics.map50_95 is not None else -1.0
    )
    fitness = (
        float(metrics.fitness) if metrics and metrics.fitness is not None else -1.0
    )
    created_at = getattr(job, "created_at", None)
    try:
        created_value = created_at.timestamp() if created_at is not None else 0.0
    except (AttributeError, OSError, OverflowError, ValueError):
        created_value = 0.0
    return (
        metrics is not None and metrics.map50_95 is not None,
        map50_95,
        fitness,
        created_value,
        int(getattr(job, "id", 0) or 0),
    )


def _task_rect_signature(task) -> tuple[str, tuple[str, ...]]:
    return (
        task_service._task_rect_preset_slug(task),
        _normalize_label_signature(_task_rect_class_names(task)),
    )


def _imported_model_label_signature(row) -> tuple[str, ...]:
    from ultralytics import YOLO

    weights_path = resolve_weights_path(getattr(row, "weights_rel_path", None))
    model = YOLO(str(weights_path))
    return _normalize_label_signature(list(_yolo_names_map(model).values()))


def _model_imgsz_from_checkpoint(
    weights_path: str | Path | None, default: int = 640
) -> int:
    """Read the inference size saved with an imported Ultralytics checkpoint.

    Imported weights do not have a database ``imgsz`` column. Falling back to
    640 for every imported model silently changes the model's intended
    inference scale (model 418 was trained at 1280), which disproportionately
    loses small objects. Ultralytics exposes the training override through
    ``args``/``overrides``; accept both dict-like and namespace-like forms and
    keep a safe fallback for older or non-Ultralytics checkpoints.
    """
    fallback = int(default or 640)
    try:
        from ultralytics import YOLO

        model = YOLO(str(weights_path))
        raw_value: Any = None
        # Ultralytics stores the value from the training run on the wrapped
        # DetectionModel (``model.model.args``).  ``overrides`` and the top
        # level ``args`` are kept as fallbacks for older checkpoints and for
        # test doubles.  Reading only the top-level object can miss the
        # checkpoint's 1280 training size and silently force inference to 640.
        sources = [
            getattr(getattr(model, "model", None), "args", None),
            getattr(model, "overrides", None),
            getattr(model, "args", None),
        ]
        for source in sources:
            if isinstance(source, dict):
                raw_value = source.get("imgsz")
            elif source is not None:
                raw_value = getattr(source, "imgsz", None)
            if raw_value is not None:
                break
        if isinstance(raw_value, (list, tuple)):
            raw_value = raw_value[0] if raw_value else None
        value = int(float(raw_value)) if raw_value is not None else fallback
        return max(32, min(4096, value))
    except Exception as exc:
        logger.debug(
            "{} checkpoint_imgsz_unavailable weights={!r} fallback={} error={!r}",
            _LOG,
            str(weights_path),
            fallback,
            exc,
        )
        return fallback


def _imported_model_imgsz(row, default: int = 640) -> int:
    """Resolve an imported model's saved inference size without breaking old rows."""
    stored = getattr(row, "weights_rel_path", None)
    if not stored:
        return int(default or 640)
    try:
        weights_path = resolve_weights_path(stored)
    except Exception as exc:
        logger.debug(
            "{} imported_model_path_unavailable model_id={} error={!r}",
            _LOG,
            getattr(row, "id", None),
            exc,
        )
        return int(default or 640)
    return _model_imgsz_from_checkpoint(weights_path, default=default)


def resolve_imported_auto_candidate_for_task(
    *,
    db: Session,
    task,
    current_user: User,
    task_contract: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Select the newest compatible imported model with the best label coverage."""
    contract = task_contract or _task_contract_descriptor(db, task)
    unrestricted = is_task_superuser(current_user)
    imported_rows, _ = crud_yolo_imported_model.list_for_user(
        db,
        current_user.id,
        name=None,
        category=None,
        page=0,
        size=500,
        list_all_users_models=unrestricted,
        public_owner_ids=(
            [] if unrestricted else crud_yolo_imported_model.public_owner_ids(db)
        ),
    )
    best_candidate: dict[str, Any] | None = None
    best_rank: tuple[int, int] | None = None
    for row in imported_rows:
        try:
            imported_labels = _imported_model_label_signature(row)
            matched_labels, _, _ = _model_task_label_coverage(contract, imported_labels)
            if not matched_labels:
                continue
            candidate = {
                "model_kind": "imported",
                "model_id": int(row.id),
                "weights_path": resolve_weights_path(row.weights_rel_path),
                "imgsz": _imported_model_imgsz(row),
                "rect_labels": matched_labels,
                "job_type": "yolo_detection",
            }
            rank = (len(matched_labels), int(row.id))
            if best_rank is None or rank > best_rank:
                best_candidate = candidate
                best_rank = rank
        except Exception as exc:
            logger.opt(exception=exc).warning(
                "{} auto_candidate_skip_imported task_id={} model_id={}",
                _LOG,
                getattr(task, "id", None),
                getattr(row, "id", None),
            )
    return best_candidate


def resolve_auto_candidate_for_task(
    *,
    db: Session,
    task,
    current_user: User,
    prefer_imported: bool = False,
) -> dict[str, Any] | None:
    task_config = task_service._parse_task_config(getattr(task, "config", None))
    if not _task_supports_rect_detection(task_config):
        return None
    task_contract = _task_contract_descriptor(db, task)
    task_labels = task_contract["labels"]
    if not task_labels:
        return None

    if prefer_imported:
        imported_candidate = resolve_imported_auto_candidate_for_task(
            db=db,
            task=task,
            current_user=current_user,
            task_contract=task_contract,
        )
        if imported_candidate is not None:
            return imported_candidate

    unrestricted = is_task_superuser(current_user)
    builtin_admin = is_consistent_pool_admin(current_user)
    trained_rows, _ = crud_yolo_train_job.list_completed_models_for_user(
        db,
        current_user.id,
        task_name=None,
        page=0,
        size=500,
        task_access_unrestricted=unrestricted,
        task_access_builtin_admin=builtin_admin,
    )
    best_candidate: dict[str, Any] | None = None
    best_rank: tuple[Any, ...] | None = None
    for job, src_task in trained_rows:
        src_labels, src_set_sig, src_order_sig = _frozen_job_contract(job, src_task)
        matched_labels, _, _ = _model_task_label_coverage(task_contract, src_labels)
        if not matched_labels:
            continue

        job_type = str(getattr(job, "job_type", None) or "yolo_detection")
        try:
            candidate = {
                "model_kind": "trained",
                "model_id": int(job.id),
                "weights_path": resolve_weights_path(job.weights_path),
                "imgsz": int(job.imgsz or 640),
                "rect_labels": matched_labels,
                "job_type": job_type,
            }
            if job_type == PLATE_RECOGNITION_JOB_TYPE:
                artifacts, metadata = _resolve_plate_component_artifacts(job)
                candidate["plate_artifacts"] = artifacts
                candidate["plate_metadata"] = metadata
            elif job_type == PLATE_DETECTION_JOB_TYPE:
                pose_path, metadata = _resolve_plate_detection_artifact(job)
                candidate["plate_artifacts"] = {"pose": pose_path}
                candidate["plate_metadata"] = metadata
        except Exception as exc:
            logger.opt(exception=exc).warning(
                "{} auto_candidate_skip_trained task_id={} job_id={}",
                _LOG,
                getattr(task, "id", None),
                getattr(job, "id", None),
            )
            continue

        # Prefer a candidate that covers more of the task contract, then use
        # the existing validation-quality/newest-job ordering as the tie-break.
        rank = (len(matched_labels),) + _trained_model_quality_rank(job)
        if best_rank is None or rank > best_rank:
            best_candidate = candidate
            best_rank = rank
    if best_candidate is not None:
        return best_candidate

    if not prefer_imported:
        return resolve_imported_auto_candidate_for_task(
            db=db,
            task=task,
            current_user=current_user,
            task_contract=task_contract,
        )
    return None


def _resolve_weights_for_command(
    db: Session,
    cmd: BatchYoloPreannotateCommand,
    current_user: User,
) -> dict[str, Any]:
    if cmd.model_kind == "imported":
        if is_task_superuser(current_user):
            row = crud_yolo_imported_model.get_by_id(db, cmd.model_id)
        else:
            row = crud_yolo_imported_model.get_available_for_user(
                db,
                model_id=cmd.model_id,
                user_id=current_user.id,
                public_owner_ids=crud_yolo_imported_model.public_owner_ids(db),
            )
        if not row:
            raise LabelUException(
                code=ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return {
            "weights_path": resolve_weights_path(row.weights_rel_path),
            "imgsz": _imported_model_imgsz(row),
            "job_type": "yolo_detection",
            "plate_artifacts": {},
            "plate_metadata": {},
            "labels": list(_imported_model_label_signature(row)),
            "label_set_signature": None,
            "class_order_signature": None,
            "preset_slug": (row.category or "").strip(),
        }

    job = crud_yolo_train_job.get(db=db, job_id=cmd.model_id)
    if not job or job.status != YoloTrainStatus.COMPLETED.value:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="训练作业不存在或未完成，无法用于预标注",
        )
    src_task = crud_task.get(db=db, task_id=job.task_id)
    if not src_task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(src_task, current_user)
    job_type = str(getattr(job, "job_type", None) or "yolo_detection")
    plate_artifacts: dict[str, Path] = {}
    plate_metadata: dict[str, Any] = {}
    if job_type == PLATE_RECOGNITION_JOB_TYPE:
        plate_artifacts, plate_metadata = _resolve_plate_component_artifacts(job)
    elif job_type == PLATE_DETECTION_JOB_TYPE:
        pose_path, plate_metadata = _resolve_plate_detection_artifact(job)
        plate_artifacts = {"pose": pose_path}
    frozen_labels, frozen_set_sig, frozen_order_sig = _frozen_job_contract(
        job, src_task
    )
    return {
        "weights_path": resolve_weights_path(job.weights_path),
        "imgsz": int(job.imgsz or 640),
        "job_type": job_type,
        "plate_artifacts": plate_artifacts,
        "plate_metadata": plate_metadata,
        "labels": list(frozen_labels),
        "label_set_signature": frozen_set_sig,
        "class_order_signature": frozen_order_sig,
        "preset_slug": _task_rect_signature(src_task)[0],
    }


def _assert_model_matches_task(
    task, model_runtime: dict[str, Any], db: Session | None = None
) -> None:
    """Allow manual preannotation when model and task share at least one label."""
    task_contract = (
        _task_contract_descriptor(db, task)
        if db is not None
        else {
            "ordered_labels": list(_task_rect_class_names(task)),
            "labels": _task_rect_signature(task)[1],
            "label_set_signature": None,
            "class_order_signature": None,
        }
    )
    task_label_signature = task_contract["labels"]
    model_labels = tuple(model_runtime.get("labels") or [])
    matched, _, _ = _model_task_label_coverage(task_contract, model_labels)
    if matched:
        return
    raise LabelUException(
        code=ErrorCode.CODE_57033_YOLO_PREANNOTATE_MODEL_MISMATCH,
        status_code=status.HTTP_400_BAD_REQUEST,
        message=(
            "所选模型与当前任务没有可共用的标签："
            f"任务={list(task_label_signature)}，模型={list(model_labels)}"
        ),
    )


def _build_worker_payload(
    *,
    task_id: int,
    user_id: int,
    sample_ids: list[int],
    weights_path: Path,
    imgsz: int,
    conf: float,
    overwrite: bool,
    model_kind: str,
    model_id: int,
    model_runtime: dict[str, Any],
    apply_to_canvas_overwrite: bool | None = None,
    agnostic_nms: bool = False,
) -> dict[str, Any]:
    """Convert validated runtime state to JSON-safe durable worker input."""
    return {
        "version": 1,
        "task_id": int(task_id),
        "user_id": int(user_id),
        "sample_ids": [int(value) for value in sample_ids],
        "weights_path": str(weights_path),
        "imgsz": int(imgsz),
        "conf": float(conf),
        "overwrite": bool(overwrite),
        "apply_to_canvas_overwrite": (
            bool(overwrite)
            if apply_to_canvas_overwrite is None
            else bool(apply_to_canvas_overwrite)
        ),
        "agnostic_nms": bool(agnostic_nms),
        "model_kind": str(model_kind),
        "model_id": int(model_id),
        "job_type": str(model_runtime.get("job_type") or "yolo_detection"),
        "plate_artifacts": {
            str(key): str(value)
            for key, value in (model_runtime.get("plate_artifacts") or {}).items()
        },
        "plate_metadata": model_runtime.get("plate_metadata") or {},
    }


async def create_batch_job(
    db: Session,
    task_id: int,
    cmd: BatchYoloPreannotateCommand,
    current_user: User,
) -> YoloPreannotateJobResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not ensure_ultralytics_installed(reason="YOLO 预标注"):
        raise LabelUException(
            code=ErrorCode.CODE_57003_YOLO_TRAIN_ULTRALYTICS_MISSING,
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
        )

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    if task.media_type != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_57005_YOLO_TRAIN_UNSUPPORTED_MEDIA,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="YOLO 预标注仅支持图片任务",
        )

    if task.status == TaskStatus.DRAFT.value:
        raise LabelUException(
            code=ErrorCode.CODE_57001_YOLO_TRAIN_TASK_NOT_FINISHED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="任务仍为草稿，请先完成配置并导入样本后再预标注",
        )

    task_cfg = auto_label_mod.resolve_ai_task_config(db, task)
    if not _task_supports_rect_detection(task_cfg):
        raise LabelUException(
            code=ErrorCode.CODE_57017_YOLO_PREANNOTATE_NO_RECT,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="当前任务未配置 rectTool，不能使用 YOLO 检测框预标注；请使用任务已配置的标注工具。",
        )
    rect_labels = build_yolo_rect_class_names(task_cfg)
    if not rect_labels:
        raise LabelUException(
            code=ErrorCode.CODE_57017_YOLO_PREANNOTATE_NO_RECT,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    model_runtime = _resolve_weights_for_command(db, cmd, current_user)
    _assert_model_matches_task(task, model_runtime, db=db)
    weights_path = model_runtime["weights_path"]
    imgsz = int(cmd.imgsz) if cmd.imgsz is not None else int(model_runtime["imgsz"])

    explicit_ids = _explicit_target_sample_ids(cmd)
    if explicit_ids:
        uniq = explicit_ids
        rows = crud_sample.get_by_ids(db=db, sample_ids=uniq, task_id=task_id)
        by_id = {r.id: r for r in rows}
        missing = [i for i in uniq if i not in by_id]
        if missing:
            raise LabelUException(
                code=ErrorCode.CODE_57018_YOLO_PREANNOTATE_NO_SAMPLES,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"样本不存在、已删除或不属于该任务: {missing[:15]}",
            )
        targets = []
        for sid in uniq:
            s = by_id[sid]
            if s.state == SampleState.SKIPPED.value:
                continue
            targets.append(s)
        if not targets:
            raise LabelUException(
                code=ErrorCode.CODE_57018_YOLO_PREANNOTATE_NO_SAMPLES,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="所选样本均为已跳过状态，无法预标注",
            )
    else:
        targets = crud_sample.list_batch_auto_label_targets(
            db=db, task_id=task_id, include_done_samples=cmd.include_done_samples
        )
        if not targets:
            raise LabelUException(
                code=ErrorCode.CODE_57018_YOLO_PREANNOTATE_NO_SAMPLES,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=(
                    "当前没有符合批量预标注条件的样本（默认仅「未标注」）。"
                    "若需对已标注样本运行，请设置 include_done_samples=true"
                ),
            )

    sample_ids = [s.id for s in targets]

    # Persist the fully resolved execution input before a worker is started.
    # This is deliberately derived from the validated command/model runtime,
    # not from a later request lookup, so a process restart cannot change the
    # model, threshold or target sample set.
    worker_payload = _build_worker_payload(
        task_id=task_id,
        user_id=current_user.id,
        sample_ids=sample_ids,
        weights_path=weights_path,
        imgsz=imgsz,
        conf=float(cmd.conf),
        overwrite=cmd.overwrite,
        model_kind=cmd.model_kind,
        model_id=cmd.model_id,
        model_runtime=model_runtime,
        apply_to_canvas_overwrite=cmd.apply_to_canvas_overwrite,
        agnostic_nms=cmd.agnostic_nms,
    )

    logger.info(
        "{} create_job task_id={} task_name={!r} user_id={} explicit_ids={} "
        "target_count={} sample_ids={} model_kind={} model_id={} weights_path={!r} "
        "imgsz={} conf={} overwrite={} include_done_samples={}",
        _LOG,
        task_id,
        (task.name or "")[:200],
        current_user.id,
        explicit_ids,
        len(sample_ids),
        (
            sample_ids[:50]
            if len(sample_ids) <= 50
            else sample_ids[:50] + ["...(truncated)"]
        ),
        cmd.model_kind,
        cmd.model_id,
        str(weights_path),
        imgsz,
        float(cmd.conf),
        cmd.overwrite,
        cmd.include_done_samples,
    )

    with begin_transaction(db):
        assert_no_active_task_background_job(db=db, task_id=task_id, group=GROUP_GEOMETRY)
        from labelu.internal.application.service import (
            annotation_contract as annotation_contract_service,
        )

        task_contract = annotation_contract_service.current_contract(db, task_id)
        job = crud_yolo_preannotate_job.create(
            db=db,
            task_id=task_id,
            user_id=current_user.id,
            sample_count=len(sample_ids),
            model_kind=cmd.model_kind,
            model_id=cmd.model_id,
            task_contract_id=(task_contract.id if task_contract else None),
            task_contract_revision=(task_contract.revision if task_contract else None),
            labels_snapshot_json=(
                task_contract.labels_snapshot_json if task_contract else None
            ),
            label_set_signature=(
                task_contract.label_set_signature if task_contract else None
            ),
            payload_json=json.dumps(worker_payload, ensure_ascii=False),
        )
        job_id = job.id
        if not bool(
            getattr(settings, "YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False)
        ):
            # Compatibility mode: retain the existing API process executor
            # until production enables and manages the dedicated worker.
            crud_yolo_preannotate_job.update_status(
                db, job, AutoLabelStatus.PROCESSING.value
            )

    if bool(getattr(settings, "YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False)):
        logger.info("{} queued_for_dedicated_worker job_id={}", _LOG, job_id)
    else:
        asyncio.get_event_loop().run_in_executor(
            None,
            partial(
                _run_yolo_preannotate_sync,
                apply_to_canvas_overwrite=worker_payload["apply_to_canvas_overwrite"],
                agnostic_nms=worker_payload["agnostic_nms"],
            ),
            job_id,
            task_id,
            sample_ids,
            str(weights_path),
            imgsz,
            float(cmd.conf),
            cmd.overwrite,
            cmd.model_kind,
            cmd.model_id,
            current_user.id,
            model_runtime["job_type"],
            model_runtime["plate_artifacts"],
            model_runtime["plate_metadata"],
        )

    return get_job(db=db, task_id=task_id, job_id=job_id, current_user=current_user)


def get_job(
    db: Session, task_id: int, job_id: int, current_user: User
) -> YoloPreannotateJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    job = crud_yolo_preannotate_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57019_YOLO_PREANNOTATE_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    processed_count = job.processed_count or 0
    annotated_count = job.success_count or 0
    failed_count = job.failed_count or 0
    return YoloPreannotateJobResponse(
        id=job.id,
        task_id=job.task_id,
        status=job.status,
        sample_count=job.sample_count or 0,
        processed_count=processed_count,
        success_count=annotated_count,
        annotated_count=annotated_count,
        empty_count=max(processed_count - annotated_count - failed_count, 0),
        failed_count=failed_count,
        model_kind=job.model_kind,
        model_id=job.model_id,
        error_message=job.error_message,
        created_at=job.created_at,
    )


def get_active_job(
    db: Session, task_id: int, current_user: User
) -> YoloPreannotateJobResponse | None:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)

    job = crud_yolo_preannotate_job.get_active_by_task_id(db=db, task_id=task_id)
    if not job:
        return None
    processed_count = job.processed_count or 0
    annotated_count = job.success_count or 0
    failed_count = job.failed_count or 0
    return YoloPreannotateJobResponse(
        id=job.id,
        task_id=job.task_id,
        status=job.status,
        sample_count=job.sample_count or 0,
        processed_count=processed_count,
        success_count=annotated_count,
        annotated_count=annotated_count,
        empty_count=max(processed_count - annotated_count - failed_count, 0),
        failed_count=failed_count,
        model_kind=job.model_kind,
        model_id=job.model_id,
        error_message=job.error_message,
        created_at=job.created_at,
    )


def _predict_source_for_attachment(attachment) -> str:
    if getattr(attachment, "data_source_id", None) and getattr(
        attachment, "data_source", None
    ):
        return get_presigned_url(attachment.data_source, attachment.path)
    key = (attachment.path or "").lstrip("/")
    local = Path(settings.MEDIA_ROOT) / key
    if local.is_file():
        return str(local.resolve())
    return get_model_read_url(attachment.path)


def _yolo_names_map(yolo_model) -> dict[int, str]:
    n = getattr(yolo_model, "names", None)
    if isinstance(n, dict):
        return {int(k): str(v) for k, v in n.items()}
    if isinstance(n, (list, tuple)):
        return {i: str(x) for i, x in enumerate(n)}
    return {}


def _infer_tag_tool_results_from_rect_labels(
    rect_results: list[dict[str, Any]],
    task_cfg_merged: dict[str, Any],
) -> list[dict[str, Any]]:
    """根据检测框标签为全局 tagTool 生成预填（与前端 schema 一致，含 type:\"tag\"）。

    YOLO 不直接预测全局属性；当任务的 tagTool 某枚举维度的选项 ``value``/``key``
    与当前图中出现的拉框 ``label`` 字符串一致时（大小写不敏感），将该维度预填为该选项
    （多选项竞争时取匹配框数最多者）。
    """
    _, _, tag_dimensions = auto_label_mod._extract_tool_configs(task_cfg_merged)
    if not tag_dimensions or not rect_results:
        return []
    labels: list[str] = []
    for r in rect_results:
        lab = (r.get("label") or "").strip()
        if lab:
            labels.append(lab)
    if not labels:
        return []
    cnt = Counter(labels)

    def _hits_for_candidate(candidate: str) -> int:
        if not candidate:
            return 0
        c = candidate.strip()
        if not c:
            return 0
        keys = {c, c.lower(), c.replace(" ", "_"), c.replace(" ", "").lower()}
        return sum(cnt.get(k, 0) for k in keys)

    value_map: dict[str, list[str]] = {}
    for dim in tag_dimensions:
        fk = dim.get("field_key")
        if not fk:
            continue
        opts = dim.get("options") or []
        if not isinstance(opts, list):
            continue
        best_emit: str | None = None
        best_score = 0
        for o in opts:
            if not isinstance(o, dict):
                continue
            emit = str(o.get("value") or o.get("key") or "").strip()
            if not emit:
                continue
            cands = {
                c
                for c in (
                    emit,
                    str(o.get("key") or "").strip(),
                    str(o.get("value") or "").strip(),
                )
                if c
            }
            score = max((_hits_for_candidate(c) for c in cands), default=0)
            if score > best_score:
                best_score = score
                best_emit = emit
        if best_emit and best_score > 0:
            value_map[str(fk)] = [best_emit]
    if not value_map:
        return []
    merged = {
        "id": str(uuid.uuid4()),
        "order": 0,
        "label": "",
        "visible": True,
        "type": "tag",
        "valid": True,
        "isVisible": True,
        "value": value_map,
    }
    return expand_tag_tool_result_items([merged])


def _build_rect_payload(
    *,
    sample_name: str,
    task_cfg_merged: dict[str, Any],
    rect_results: list[dict[str, Any]],
    model_kind: str,
    model_id: int,
    weights_name: str,
    polygon_results: list[dict[str, Any]] | None = None,
    job_type: str = "yolo_detection",
    apply_to_canvas_overwrite: bool = False,
) -> dict[str, Any]:
    _, config_by_tool, _ = auto_label_mod._extract_tool_configs(task_cfg_merged)
    pre_annotation_config = {
        tool_name: tool_cfg.get("attributes", [])
        for tool_name, tool_cfg in config_by_tool.items()
    }
    grouped: dict[str, Any] = {}
    if rect_results and _task_supports_rect_detection(task_cfg_merged):
        grouped["rectTool"] = {"toolName": "rectTool", "result": rect_results}
    if polygon_results:
        grouped["polygonTool"] = {
            "toolName": "polygonTool",
            "result": polygon_results,
        }
    tag_results = _infer_tag_tool_results_from_rect_labels(
        rect_results, task_cfg_merged
    )
    if tag_results:
        grouped["tagTool"] = {"toolName": "tagTool", "result": tag_results}

    meta = {
        "source_type": YOLO_PRE_META,
        "model_kind": model_kind,
        "model_id": model_id,
        "weights_file": weights_name,
        "job_type": job_type,
        # Generated geometry remains a visible review layer on DONE samples
        # even when the batch policy protects existing formal annotations.
        "include_geometry_tools": True,
    }
    if apply_to_canvas_overwrite:
        meta.update(
            {
                "apply_to_canvas_overwrite": True,
                "include_geometry_tools": True,
                "overwrite_existing_annotations": True,
                "preserve_global_annotations": True,
            }
        )

    return {
        "sample_name": sample_name,
        "annotations": grouped,
        "config": pre_annotation_config,
        "meta": meta,
    }


def _plate_detections_to_annotation_results(
    detections: list[dict[str, Any]],
    *,
    rect_label: str,
    polygon_label: str,
    detection_only: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rect_results: list[dict[str, Any]] = []
    polygon_results: list[dict[str, Any]] = []
    for order, detection in enumerate(detections):
        x1 = float(detection["x1"])
        y1 = float(detection["y1"])
        x2 = float(detection["x2"])
        y2 = float(detection["y2"])
        rect = {
            "id": str(uuid.uuid4()),
            "order": order,
            "label": rect_label,
            "visible": True,
            "x": x1,
            "y": y1,
            "width": max(0.0, x2 - x1),
            "height": max(0.0, y2 - y1),
        }
        if not detection_only:
            rect["attributes"] = {
                "plateNumber": str(detection.get("plate_number") or ""),
                "color": str(detection.get("color") or ""),
                "plateType": str(detection.get("plate_type") or ""),
                "score": str(round(float(detection.get("confidence") or 0.0), 4)),
            }
        rect_results.append(rect)

        corners = detection.get("corners") or []
        if len(corners) != 4:
            continue
        points = [{"x": float(point["x"]), "y": float(point["y"])} for point in corners]
        # LabelU stores polygon vertices without a duplicated closing vertex;
        # the canvas closes the rendered edge itself.  Keep the legacy
        # composite profile's historical payload shape, but emit exactly four
        # points for the detection-only 4/4 geometry contract.
        if not detection_only:
            points.append(dict(points[0]))
        polygon_results.append(
            {
                "id": str(uuid.uuid4()),
                "order": order,
                "label": polygon_label,
                "visible": True,
                "type": "line",
                "points": points,
            }
        )
    return rect_results, polygon_results


def _load_rgb_image(source: str):
    from PIL import Image, ImageOps

    if str(source).startswith(("http://", "https://")):
        import httpx

        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            response = client.get(str(source))
            response.raise_for_status()
        image = Image.open(BytesIO(response.content))
    else:
        image = Image.open(str(source))
    return ImageOps.exif_transpose(image).convert("RGB")


def _delete_yolo_preannotations_for_sample(
    db: Session, task_id: int, sample_name: str
) -> int:
    rows, _ = crud_pre_annotation.list_by(
        db=db,
        task_id=task_id,
        sample_name=sample_name,
        page=0,
        size=500,
    )
    ids = [r.id for r in rows if _is_yolo_preannotation_row(r)]
    if ids:
        crud_pre_annotation.delete(db=db, pre_annotation_ids=ids)
    return len(ids)


def _sample_has_yolo_preannotation(db: Session, task_id: int, sample_name: str) -> bool:
    rows, _ = crud_pre_annotation.list_by(
        db=db, task_id=task_id, sample_name=sample_name, page=0, size=100
    )
    return any(_is_yolo_preannotation_row(r) for r in rows)


def sample_has_yolo_preannotation(
    db: Session, task_id: int, sample_name: str | None
) -> bool:
    """任务样本是否含 YOLO 写入的预标注（``meta.source_type == yolo_generated``）。"""
    if not sample_name:
        return False
    return _sample_has_yolo_preannotation(db, task_id, sample_name)


def _scalar_xyxy(box_row) -> tuple[float, float, float, float]:
    """box_row: 1x4 tensor / ndarray / list."""
    if hasattr(box_row, "tolist"):
        flat = box_row.tolist()
    else:
        flat = list(box_row)
    if flat and isinstance(flat[0], (list, tuple)):
        flat = flat[0]
    x1, y1, x2, y2 = [float(x) for x in flat[:4]]
    return x1, y1, x2, y2


def _to_scalar_float(t) -> float:
    if hasattr(t, "item"):
        return float(t.item())
    x = t.tolist() if hasattr(t, "tolist") else t
    if isinstance(x, list):
        x = x[0]
    return float(x)


def _build_detection_label_aliases(
    model_labels: Any,
    allowed_labels: set[str],
) -> dict[str, str]:
    """Resolve only explicit model-to-task compatibility aliases.

    The returned mapping is keyed by the raw class name from ``model.names``
    and is applied after YOLO inference, before the task label whitelist.  It
    deliberately does not infer arbitrary labels from substrings or class
    order, which would make a partial model write wrong task labels.
    """
    allowed_by_folded = {
        str(label).strip().casefold(): str(label).strip()
        for label in allowed_labels
        if str(label).strip()
    }
    aliases: dict[str, str] = {}
    for raw in model_labels or []:
        model_label = str(raw or "").strip()
        if not model_label or model_label in allowed_labels:
            continue
        for candidate in _MODEL_LABEL_COMPATIBILITY_ALIASES.get(
            model_label.casefold(), ()
        ):
            target = allowed_by_folded.get(str(candidate).casefold())
            if target:
                aliases[model_label] = target
                break
    return aliases


def _detections_to_rect_results(
    boxes,
    names_map: dict[int, str],
    allowed_labels: set[str],
    label_aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    rect_results: list[dict[str, Any]] = []
    if boxes is None or len(boxes) == 0:
        return rect_results
    xyxy = boxes.xyxy
    cls_t = boxes.cls
    conf_t = boxes.conf
    n = len(boxes)
    order_out = 0
    for i in range(n):
        x1, y1, x2, y2 = _scalar_xyxy(xyxy[i])
        cid = int(_to_scalar_float(cls_t[i]))
        cf = _to_scalar_float(conf_t[i])
        raw_name = names_map.get(cid, "")
        raw_label = raw_name.strip() if raw_name else ""
        label = (label_aliases or {}).get(raw_label, raw_label)
        if label not in allowed_labels:
            continue
        w = max(0.0, x2 - x1)
        h = max(0.0, y2 - y1)
        rect_results.append(
            {
                "id": str(uuid.uuid4()),
                "order": order_out,
                "label": label,
                "visible": True,
                "attributes": {"score": str(round(cf, 4))},
                "x": x1,
                "y": y1,
                "width": w,
                "height": h,
            }
        )
        order_out += 1
    return rect_results


def _run_yolo_preannotate_sync(
    job_id: int,
    task_id: int,
    sample_ids: list[int],
    weights_abs: str,
    imgsz: int,
    conf: float,
    overwrite: bool,
    model_kind: str,
    model_id: int,
    user_id: int,
    job_type: str = "yolo_detection",
    plate_artifacts: dict[str, Path] | None = None,
    plate_metadata: dict[str, Any] | None = None,
    lease_owner: str | None = None,
    lease_fencing_token: int | None = None,
    lease_seconds: int = 900,
    apply_to_canvas_overwrite: bool | None = None,
    agnostic_nms: bool = False,
) -> None:
    from labelu.internal.common.db import session_scope

    from ultralytics import YOLO

    try:
        with session_scope() as db:
            job = crud_yolo_preannotate_job.get(db=db, job_id=job_id)
            if not job:
                return
            task = crud_task.get(db=db, task_id=task_id)
            task_cfg_merged = auto_label_mod.resolve_ai_task_config(db, task)
            if not _task_supports_rect_detection(task_cfg_merged):
                raise RuntimeError(
                    "当前任务未配置 rectTool，停止应用 YOLO 检测框预标注"
                )
            if job.task_contract_id and job.labels_snapshot_json:
                current_contract_id = int(
                    getattr(task, "current_annotation_contract_id", 0) or 0
                )
                if current_contract_id != int(job.task_contract_id):
                    raise RuntimeError(
                        "任务标签契约在预标注排队期间已变化，旧结果已停止应用"
                    )
            allowed = set(build_yolo_rect_class_names(task_cfg_merged))

        logger.info(
            "{} worker_start job_id={} task_id={} user_id={} n_samples={} sample_ids={} "
            "weights={!r} imgsz={} conf={} overwrite={} model_kind={} model_id={} "
            "allowed_rect_labels={}",
            _LOG,
            job_id,
            task_id,
            user_id,
            len(sample_ids),
            (
                sample_ids[:50]
                if len(sample_ids) <= 50
                else sample_ids[:50] + ["...(truncated)"]
            ),
            weights_abs,
            imgsz,
            conf,
            overwrite,
            model_kind,
            model_id,
            sorted(allowed)[:80],
        )

        model = None
        names_map: dict[int, str] = {}
        if job_type not in {PLATE_RECOGNITION_JOB_TYPE, PLATE_DETECTION_JOB_TYPE}:
            model = YOLO(weights_abs)
            names_map = _yolo_names_map(model)
        label_aliases = _build_detection_label_aliases(names_map.values(), allowed)
        if label_aliases:
            logger.info(
                "{} worker_label_aliases job_id={} aliases={}",
                _LOG,
                job_id,
                label_aliases,
            )
        wname = Path(weights_abs).name

        for sample_id in sample_ids:
            try:
                if lease_owner is not None and lease_fencing_token is not None:
                    _renew_yolo_preannotate_lease(
                        job_id,
                        lease_owner,
                        lease_fencing_token,
                        lease_seconds,
                    )
                logger.info(
                    "{} sample_begin job_id={} task_id={} sample_id={}",
                    _LOG,
                    job_id,
                    task_id,
                    sample_id,
                )
                geometry_count = _process_one_sample(
                    task_id=task_id,
                    task_cfg_merged=task_cfg_merged,
                    sample_id=sample_id,
                    model=model,
                    names_map=names_map,
                    allowed_labels=allowed,
                    label_aliases=label_aliases,
                    imgsz=imgsz,
                    conf=conf,
                    overwrite=overwrite,
                    model_kind=model_kind,
                    model_id=model_id,
                    user_id=user_id,
                    weights_name=wname,
                    job_type=job_type,
                    plate_artifacts=plate_artifacts,
                    plate_metadata=plate_metadata,
                    apply_to_canvas_overwrite=(
                        bool(overwrite)
                        if apply_to_canvas_overwrite is None
                        else bool(apply_to_canvas_overwrite)
                    ),
                    agnostic_nms=agnostic_nms,
                    lease_guard=(
                        lambda: (
                            _assert_yolo_preannotate_lease(
                                job_id, lease_owner, lease_fencing_token
                            )
                            if lease_owner is not None
                            and lease_fencing_token is not None
                            else None
                        )
                    ),
                )
                with session_scope() as db:
                    job = crud_yolo_preannotate_job.get(db=db, job_id=job_id)
                    if job:
                        with begin_transaction(db):
                            if (
                                lease_owner is not None
                                and lease_fencing_token is not None
                            ):
                                if not crud_yolo_preannotate_job.increment_progress_fenced(
                                    db,
                                    job_id=job_id,
                                    lease_owner=lease_owner,
                                    fencing_token=lease_fencing_token,
                                    success=True,
                                    annotated=geometry_count > 0,
                                    lease_seconds=lease_seconds,
                                ):
                                    raise RuntimeError(
                                        f"yolo preannotation job {job_id} lease lost"
                                    )
                            else:
                                crud_yolo_preannotate_job.increment_progress(
                                    db,
                                    job,
                                    success=True,
                                    annotated=geometry_count > 0,
                                )
                logger.info(
                    "{} sample_ok job_id={} sample_id={} geometry_count={}",
                    _LOG,
                    job_id,
                    sample_id,
                    geometry_count,
                )
            except Exception as exc:
                logger.opt(exception=exc).error(
                    "{} sample_fail job_id={} sample_id={}",
                    _LOG,
                    job_id,
                    sample_id,
                )
                with session_scope() as db:
                    job = crud_yolo_preannotate_job.get(db=db, job_id=job_id)
                    if job:
                        with begin_transaction(db):
                            if (
                                lease_owner is not None
                                and lease_fencing_token is not None
                            ):
                                if not crud_yolo_preannotate_job.increment_progress_fenced(
                                    db,
                                    job_id=job_id,
                                    lease_owner=lease_owner,
                                    fencing_token=lease_fencing_token,
                                    success=False,
                                    lease_seconds=lease_seconds,
                                ):
                                    raise RuntimeError(
                                        f"yolo preannotation job {job_id} lease lost"
                                    )
                            else:
                                crud_yolo_preannotate_job.increment_progress(
                                    db, job, success=False
                                )

        with session_scope() as db:
            job = crud_yolo_preannotate_job.get(db=db, job_id=job_id)
            if job:
                with begin_transaction(db):
                    if lease_owner is not None and lease_fencing_token is not None:
                        if not crud_yolo_preannotate_job.finish_fenced(
                            db,
                            job_id=job_id,
                            lease_owner=lease_owner,
                            fencing_token=lease_fencing_token,
                            status=AutoLabelStatus.COMPLETED.value,
                        ):
                            raise RuntimeError(
                                f"yolo preannotation job {job_id} lease lost"
                            )
                    else:
                        crud_yolo_preannotate_job.update_status(
                            db, job, AutoLabelStatus.COMPLETED.value
                        )
        logger.info(
            "{} job_done job_id={} task_id={} processed_all", _LOG, job_id, task_id
        )
    except Exception as e:
        logger.opt(exception=e).error(
            "{} job_fatal job_id={} task_id={}", _LOG, job_id, task_id
        )
        try:
            with session_scope() as db:
                job = crud_yolo_preannotate_job.get(db=db, job_id=job_id)
                if job:
                    with begin_transaction(db):
                        if lease_owner is not None and lease_fencing_token is not None:
                            crud_yolo_preannotate_job.finish_fenced(
                                db,
                                job_id=job_id,
                                lease_owner=lease_owner,
                                fencing_token=lease_fencing_token,
                                status=AutoLabelStatus.FAILED.value,
                                error_message=str(e),
                            )
                        else:
                            crud_yolo_preannotate_job.update_status(
                                db,
                                job,
                                AutoLabelStatus.FAILED.value,
                                error_message=str(e),
                            )
        except Exception:
            logger.error("Failed to update yolo preannotate job {}", job_id)


def _renew_yolo_preannotate_lease(
    job_id: int, lease_owner: str, fencing_token: int, lease_seconds: int
) -> None:
    """Renew only the currently fenced lease; stale workers fail closed."""
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        with begin_transaction(db):
            if not crud_yolo_preannotate_job.renew_lease(
                db,
                job_id=job_id,
                lease_owner=lease_owner,
                fencing_token=fencing_token,
                lease_seconds=lease_seconds,
            ):
                raise RuntimeError(f"yolo preannotation job {job_id} lease lost")


def _assert_yolo_preannotate_lease(
    job_id: int, lease_owner: str, fencing_token: int
) -> None:
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        row = crud_yolo_preannotate_job.get(db=db, job_id=job_id)
        if (
            not row
            or row.status != AutoLabelStatus.PROCESSING.value
            or row.lease_owner != lease_owner
            or int(row.lease_fencing_token or 0) != int(fencing_token)
        ):
            raise RuntimeError(f"yolo preannotation job {job_id} lease lost")


def _process_one_sample(
    *,
    task_id: int,
    task_cfg_merged: dict[str, Any],
    sample_id: int,
    model,
    names_map: dict[int, str],
    allowed_labels: set[str],
    imgsz: int,
    conf: float,
    overwrite: bool,
    model_kind: str,
    model_id: int,
    user_id: int,
    weights_name: str,
    label_aliases: dict[str, str] | None = None,
    job_type: str = "yolo_detection",
    plate_artifacts: dict[str, Path] | None = None,
    plate_metadata: dict[str, Any] | None = None,
    apply_to_canvas_overwrite: bool = False,
    lease_guard: Callable[[], None] | None = None,
    agnostic_nms: bool = False,
) -> int:
    from labelu.internal.common.db import session_scope

    if label_aliases is None:
        # Automatic sample preannotation reuses this function directly and
        # does not have the durable batch worker's precomputed mapping.
        # Derive the same explicit compatibility aliases here so task-level,
        # single-image, and automatic paths produce identical labels.
        label_aliases = _build_detection_label_aliases(
            names_map.values(), allowed_labels
        )

    if job_type in {PLATE_RECOGNITION_JOB_TYPE, PLATE_DETECTION_JOB_TYPE}:
        task_cfg_merged = normalize_plate_recognition_task_config(
            task_cfg_merged,
            (
                DEFAULT_PLATE_DETECTION_CONFIG
                if job_type == PLATE_DETECTION_JOB_TYPE
                else DEFAULT_PLATE_RECOGNITION_CONFIG
            ),
        )

    with session_scope() as db:
        sample = crud_sample.get(db=db, sample_id=sample_id)
        if not sample or sample.task_id != task_id or not sample.file:
            raise ValueError("sample missing or no attachment")

        sample_name = sample.file.filename
        att = sample.file
        path_tail = (att.path or "")[-120:] if att else ""

        logger.info(
            "{} process_entry task_id={} sample_id={} sample_name={!r} inner_id={} state={} "
            "overwrite={} attachment_path_tail={!r} file_id={}",
            _LOG,
            task_id,
            sample_id,
            sample_name,
            getattr(sample, "inner_id", None),
            getattr(sample, "state", None),
            overwrite,
            path_tail,
            getattr(att, "id", None),
        )

        if not overwrite and _sample_has_yolo_preannotation(db, task_id, sample_name):
            logger.info(
                "{} process_skip task_id={} sample_id={} sample_name={!r} reason=no_overwrite_and_has_yolo_pre",
                _LOG,
                task_id,
                sample_id,
                sample_name,
            )
            return 0

        src = _predict_source_for_attachment(sample.file)
        src_kind = (
            "datasource_url"
            if getattr(sample.file, "data_source_id", None)
            else ("http" if str(src).startswith("http") else "local_or_media")
        )
        logger.info(
            "{} predict_src task_id={} sample_id={} kind={} src_len={}",
            _LOG,
            task_id,
            sample_id,
            src_kind,
            len(str(src)),
        )

    polygon_results: list[dict[str, Any]] = []
    preview_cls: list[str] = []
    if job_type in {PLATE_RECOGNITION_JOB_TYPE, PLATE_DETECTION_JOB_TYPE}:
        from labelu.internal.application.service.plate_recognition_infer import (
            infer_plate_detection,
            infer_plate_recognition,
        )

        if not plate_artifacts or not plate_metadata:
            raise RuntimeError("Plate model components are missing")
        image = _load_rgb_image(src)
        if job_type == PLATE_DETECTION_JOB_TYPE:
            detections = infer_plate_detection(
                image,
                weights=plate_artifacts["pose"],
                confidence=conf,
                imgsz=imgsz,
            )
        else:
            detections = infer_plate_recognition(
                image,
                artifacts=plate_artifacts,
                metadata=plate_metadata,
                confidence=conf,
                imgsz=imgsz,
            )
        raw_n = len(detections)
        pose_classes = (plate_metadata.get("pose") or {}).get("class_names") or []
        preferred_rect_label = str(
            pose_classes[0]
            if pose_classes
            else (
                DEFAULT_PLATE_DETECTION_CONFIG["rect_label"]
                if job_type == PLATE_DETECTION_JOB_TYPE
                else DEFAULT_PLATE_RECOGNITION_CONFIG["rect_label"]
            )
        ).strip()
        rect_label = (
            preferred_rect_label
            if preferred_rect_label in allowed_labels
            else str(
                (
                    DEFAULT_PLATE_DETECTION_CONFIG
                    if job_type == PLATE_DETECTION_JOB_TYPE
                    else DEFAULT_PLATE_RECOGNITION_CONFIG
                )["rect_label"]
            )
        )
        rect_results, polygon_results = _plate_detections_to_annotation_results(
            detections,
            rect_label=rect_label,
            polygon_label=str(
                (
                    DEFAULT_PLATE_DETECTION_CONFIG
                    if job_type == PLATE_DETECTION_JOB_TYPE
                    else DEFAULT_PLATE_RECOGNITION_CONFIG
                )["polygon_label"]
            ),
            detection_only=job_type == PLATE_DETECTION_JOB_TYPE,
        )
        preview_cls = [f"plate:{rect_label}"] * min(raw_n, 25)
    else:
        results = model.predict(
            source=src,
            conf=conf,
            imgsz=imgsz,
            verbose=False,
            agnostic_nms=agnostic_nms,
        )
        r0 = results[0]
        boxes = getattr(r0, "boxes", None)

        raw_n = 0
        if boxes is not None:
            try:
                raw_n = len(boxes)
                cls_t = boxes.cls
                for i in range(min(raw_n, 25)):
                    cid = int(_to_scalar_float(cls_t[i]))
                    preview_cls.append(f"{cid}:{names_map.get(cid, '?')}")
            except Exception as ex:
                raw_n = -1
                preview_cls = [f"len_err:{ex!r}"]

        rect_results = _detections_to_rect_results(
            boxes,
            names_map,
            allowed_labels,
            label_aliases=label_aliases,
        )
    written_labels = [r.get("label", "") for r in rect_results]

    logger.info(
        "{} predict_result task_id={} sample_id={} raw_boxes={} after_label_filter={} "
        "preview_raw_cls={} allowed_rect_labels(sample)={}",
        _LOG,
        task_id,
        sample_id,
        raw_n,
        len(rect_results),
        preview_cls,
        sorted(allowed_labels)[:60],
    )

    payload = _build_rect_payload(
        sample_name=sample_name,
        task_cfg_merged=task_cfg_merged,
        rect_results=rect_results,
        model_kind=model_kind,
        model_id=model_id,
        weights_name=weights_name,
        polygon_results=polygon_results,
        job_type=job_type,
        apply_to_canvas_overwrite=apply_to_canvas_overwrite,
    )

    # A takeover can occur while model inference is running.  Check the
    # generation immediately before the annotation write so the old worker
    # cannot persist a late result after its lease was fenced off.
    if lease_guard is not None:
        lease_guard()

    with session_scope() as db:
        ann_keys = list((payload.get("annotations") or {}).keys())
        meta = payload.get("meta") or {}

        _, _, tag_dims_dbg = auto_label_mod._extract_tool_configs(task_cfg_merged)
        tag_block = (payload.get("annotations") or {}).get("tagTool") or {}
        tag_items_n = len(tag_block.get("result") or [])
        rect_label_set = sorted(
            {
                str(r.get("label") or "").strip()
                for r in rect_results
                if (r.get("label") or "").strip()
            }
        )
        if not tag_dims_dbg:
            logger.info(
                "{} tag_preannotate task_id={} sample_id={} reason=no_enum_tag_dimensions "
                "(任务无合并后的 tagTool 枚举维度，或 DB 未配置全局维度/任务内 tagTool 无 options)",
                _LOG,
                task_id,
                sample_id,
            )
        elif tag_items_n == 0:
            preview_opts: list[str] = []
            for d in tag_dims_dbg[:2]:
                opts = d.get("options") or []
                if isinstance(opts, list):
                    for o in opts[:6]:
                        if isinstance(o, dict):
                            preview_opts.append(f"{o.get('value')!s}/{o.get('key')!s}")
            logger.info(
                "{} tag_preannotate task_id={} sample_id={} reason=no_label_option_match "
                "tag_dimensions={} rect_labels={} sample_option_values_keys_preview={}",
                _LOG,
                task_id,
                sample_id,
                len(tag_dims_dbg),
                rect_label_set,
                preview_opts[:12],
            )
        else:
            logger.info(
                "{} tag_preannotate task_id={} sample_id={} tag_dimensions={} tag_tool_items={}",
                _LOG,
                task_id,
                sample_id,
                len(tag_dims_dbg),
                tag_items_n,
            )

        deleted = 0
        with begin_transaction(db):
            if overwrite:
                deleted = _delete_yolo_preannotations_for_sample(
                    db, task_id, sample_name
                )
            crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task_id,
                        file_id=None,
                        sample_name=sample_name,
                        data=json.dumps(payload, ensure_ascii=False),
                        created_by=user_id,
                        updated_by=user_id,
                    )
                ],
            )

        logger.info(
            "{} process_persisted task_id={} sample_id={} sample_name={!r} deleted_prior_yolo_rows={} "
            "payload_sample_name={!r} annotation_tools={} rect_labels_written={} meta={}",
            _LOG,
            task_id,
            sample_id,
            sample_name,
            deleted,
            payload.get("sample_name"),
            ann_keys,
            written_labels,
            meta,
        )
    return len(rect_results) + len(polygon_results)
