from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.experiment_dataset_group import (
    ExperimentDatasetGroup,
)
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.third_party_call_audit import ThirdPartyCallAudit

_STRATEGY_TYPE_LABELS = {
    "threshold_experiment": "阈值实验",
    "model_capability": "模型优化",
}

_BUSINESS_TYPE_LABELS = {
    "dataset_creation": "任务数据集创建",
    "model_optimization": "模型优化",
    "threshold_experiment": "阈值实验",
    "strategy_task": "策略任务",
}

_OPERATION_BUSINESS_TYPES = {
    "experiment_dataset_import": "dataset_creation",
    "model_capability": "model_optimization",
    "threshold_experiment": "threshold_experiment",
    "strategy_evolution_task": "strategy_task",
    "strategy_task_import": "strategy_task",
    "strategy_platform_callback": "strategy_task",
    "strategy_status_query": "strategy_task",
}


def _business_type_for(operation: str | None) -> str:
    normalized = str(operation or "").strip().lower()
    return _OPERATION_BUSINESS_TYPES.get(normalized, "strategy_task")


def _business_type_label(operation: str | None) -> str:
    return _BUSINESS_TYPE_LABELS[_business_type_for(operation)]

_STRATEGY_STATUS_LABELS = {
    "accepted": "已接收",
    "waiting_for_model": "等待模型映射",
    "waiting_for_annotations": "等待标注样本",
    "model_training_created": "模型训练中",
    "export_created": "模型编译中",
    "edge_validation_created": "边端验证中",
    "publish_created": "模型发布中",
    "running": "执行中",
    "succeeded": "已完成",
    "failed": "失败",
    "comparison_failed": "质量对比未通过",
    "callback_failed": "结果回调失败",
    "cancelled": "已取消",
    "accepted_not_implemented": "已接收但未实现",
}

_DATASET_STATUS_LABELS = {
    "PENDING": "已受理",
    "PROCESSING": "正在导入",
    "WAITING_FOR_SAMPLES": "等待更多图片",
    "WAITING_FOR_MODEL": "等待模型就绪",
    "COMPLETED": "已完成",
    "PARTIAL_COMPLETED": "部分完成",
    "FAILED": "失败",
}


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_int_list(value: str | None) -> list[int]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for item in parsed:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _first_message(*payloads: dict[str, Any], fallback: str = "") -> str:
    for payload in payloads:
        for key in ("message", "reason", "detail", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


def _strategy_next_action(row: StrategyTaskIntegration) -> tuple[str, str, str]:
    status = str(row.platform_status or "accepted").strip().lower()
    callback = str(row.outbound_callback_status or "").strip().lower()
    if status in {"failed", "comparison_failed", "callback_failed", "cancelled"}:
        if callback == "failed":
            return "blocked", "管理员", "检查回调地址和最后错误，确认后重试结果回调"
        return "blocked", "管理员", "查看失败原因和关联任务，处理阻塞后再决定是否重试"
    if status == "succeeded":
        if callback in {"pending", "retrying"}:
            if row.outbound_callback_next_retry_at:
                retry_at = row.outbound_callback_next_retry_at.strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                return "processing", "系统", f"等待系统在 {retry_at} 自动重试结果回调"
            return "processing", "系统", "等待系统自动发送或重试结果回调"
        return "completed", "无", "流程已结束，无需后续处理"
    if status == "waiting_for_model":
        return "waiting", "管理员", "发布或补全目标模型映射，系统随后自动继续"
    if status == "waiting_for_annotations":
        return "waiting", "标注人员", "完成符合条件的样本标注，系统随后自动继续"
    if status == "model_training_created":
        return "processing", "系统", "等待模型训练完成后自动进入编译"
    if status == "export_created":
        return "processing", "系统", "等待 BM1688 编译完成后自动进入边端验证"
    if status == "edge_validation_created":
        return "processing", "系统", "等待边端验证与质量对比完成"
    if status == "publish_created":
        return "processing", "系统", "等待模型发布完成并发送结果回调"
    if status == "accepted_not_implemented":
        return "blocked", "研发人员", "当前任务类型尚未接入自动执行链路"
    return "processing", "系统", "系统正在接收并推进任务，请关注阶段变化"


def _dataset_next_action(status: str) -> tuple[str, str, str]:
    normalized = str(status or "PENDING").strip().upper()
    if normalized == "WAITING_FOR_SAMPLES":
        return "waiting", "第三方", "继续提交同一聚合条件下的有效图片数据包"
    if normalized == "WAITING_FOR_MODEL":
        return "waiting", "管理员", "发布或补全目标模型映射，系统随后自动继续"
    if normalized in {"PENDING", "PROCESSING"}:
        return "processing", "系统", "等待系统完成数据校验、导入和任务创建"
    if normalized == "FAILED":
        return "blocked", "管理员", "检查数据包、S3 文件和错误原因，修复后重新提交"
    if normalized == "PARTIAL_COMPLETED":
        return "blocked", "管理员", "检查异常样本，确认是否需要补充或重新提交"
    return "completed", "无", "数据任务已创建，无需后续处理"


def _strategy_message(row: StrategyTaskIntegration) -> str:
    result = _json_object(row.platform_result_json)
    error = _json_object(row.platform_error_json)
    status = str(row.platform_status or "accepted").strip().lower()
    fallback = {
        "waiting_for_model": "待发布模型映射",
        "waiting_for_annotations": "待匹配已完成标注样本",
        "model_training_created": "已创建模型训练任务",
        "export_created": "已创建 BM1688 导出任务",
        "edge_validation_created": "已创建边端验证任务",
        "callback_failed": "结果回调失败",
    }.get(status, "")
    if row.last_error:
        return str(row.last_error)[:500]
    return _first_message(error, result, fallback=fallback)


def _normalize_strategy(row: StrategyTaskIntegration) -> dict[str, Any]:
    target = _json_object(row.target_json)
    result = _json_object(row.platform_result_json)
    progress = _json_object(row.platform_progress_json)
    health, owner, next_action = _strategy_next_action(row)
    status = str(row.platform_status or "accepted").strip().lower()
    return {
        "record_type": "strategy_task",
        "project_id": None,
        "record_type_label": "策略任务记录",
        "record_id": str(row.external_task_id),
        "source_system": str(row.source_system or "strategy_evolution"),
        "operation": str(row.task_type or "strategy_evolution_task"),
        "operation_label": _STRATEGY_TYPE_LABELS.get(row.task_type, row.task_type),
        "business_type": _business_type_for(row.task_type),
        "business_type_label": _business_type_label(row.task_type),
        "title": str(row.task_name or row.external_task_id),
        "schema_version": str(row.schema_version or ""),
        "external_task_id": str(row.external_task_id),
        "external_package_id": str(
            row.source_external_package_id or row.source_package_id or ""
        ),
        "current_stage": str(row.automation_stage or "accepted"),
        "status": status,
        "status_label": _STRATEGY_STATUS_LABELS.get(status, status or "未知"),
        "status_detail": _strategy_message(row),
        "health": health,
        "next_action_owner": owner,
        "next_action": next_action,
        "callback": {
            "status": str(row.outbound_callback_status or ""),
            "attempts": int(row.outbound_callback_attempts or 0),
            "next_retry_at": row.outbound_callback_next_retry_at,
            "last_error": str(row.outbound_callback_last_error or ""),
            "workflow_id": getattr(row, "callback_workflow_id", None),
        },
        "progress": {
            **progress,
            "selected_sample_count": result.get("selected_sample_count"),
            "imported_sample_count": int(row.imported_sample_count or 0),
            "imported_pre_annotation_count": int(
                row.imported_pre_annotation_count or 0
            ),
        },
        "target": target,
        "linked_resources": {
            "task_id": row.labelu_task_id,
            "source_train_job_id": row.source_train_job_id,
            "source_export_id": row.source_export_id,
            "train_job_id": row.derived_train_job_id,
            "export_id": row.derived_export_id,
            "edge_validation_job_id": row.derived_edge_validation_job_id,
        },
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "finished_at": row.platform_finished_at,
    }


def _normalize_dataset(
    row: ExperimentDatasetImport,
    group: ExperimentDatasetGroup | None,
) -> dict[str, Any]:
    status = str(row.status or "PENDING").strip().upper()
    health, owner, next_action = _dataset_next_action(status)
    required = int(
        group.min_sample_count
        if group is not None
        else settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT
    )
    valid = int(
        group.valid_sample_count if group is not None else row.valid_sample_count or 0
    )
    declared = int(
        group.declared_sample_count
        if group is not None
        else row.declared_sample_count or 0
    )
    formal_task_ids = _json_int_list(row.formal_task_ids_json)
    if not formal_task_ids and group is not None and group.formal_task_id:
        formal_task_ids = [int(group.formal_task_id)]
    model_resolution = _json_object(group.model_resolution_json) if group else {}
    manual_model_confirmation = bool(
        model_resolution.get("manual_confirmation_required")
    )
    if status == "WAITING_FOR_SAMPLES":
        detail = f"已接收 {valid} 张有效图片，达到 {required} 张后创建任务"
    elif status == "WAITING_FOR_MODEL":
        if manual_model_confirmation:
            reason = str(model_resolution.get("reason") or "模型未匹配")
            detail = (
                f"已创建草稿任务，待人工确认模型（{reason}）；"
                "确认前不会自动预标注或进入正式训练"
            )
            health, owner, next_action = (
                "waiting",
                "管理员",
                "确认第三方模型与本地模型的对应关系，确认后再进入正式训练",
            )
        else:
            detail = "数据已接收，等待发布模型映射就绪"
    elif status == "FAILED":
        detail = f"导入失败：{row.last_error or '未记录具体错误'}"
    elif status == "PROCESSING":
        detail = f"正在导入，已登记 {int(row.formal_sample_count or 0) + int(row.draft_sample_count or 0)}/{declared} 个样本"
    elif status in {"COMPLETED", "PARTIAL_COMPLETED"}:
        detail = f"已创建 {len(formal_task_ids)} 个正式任务，异常样本 {int(row.abnormal_sample_count or 0)} 个"
    else:
        detail = "请求已受理，等待后台处理"
    payload = _json_object(row.source_payload_json)
    return {
        "record_type": "dataset_import",
        "record_type_label": "数据集记录",
        "record_id": str(row.package_id),
        "source_system": str(row.source_system or "unknown"),
        "operation": "experiment_dataset_import",
        "operation_label": "任务数据集创建",
        "business_type": "dataset_creation",
        "business_type_label": "任务数据集创建",
        "title": str(row.package_id),
        "schema_version": str(payload.get("schema_version") or ""),
        "external_task_id": str(row.source_external_task_id or ""),
        "external_package_id": str(
            row.source_external_package_id or row.package_id or ""
        ),
        "current_stage": status,
        "status": status,
        "status_label": (
            "待人工确认模型"
            if manual_model_confirmation and status == "WAITING_FOR_MODEL"
            else _DATASET_STATUS_LABELS.get(status, status or "未知")
        ),
        "status_detail": detail,
        "health": health,
        "next_action_owner": owner,
        "next_action": next_action,
        "callback": None,
        "progress": {
            "declared_samples": declared,
            "valid_samples": valid,
            "required_samples": required,
            "formal_samples": int(row.formal_sample_count or 0),
            "draft_samples": int(row.draft_sample_count or 0),
            "abnormal_samples": int(row.abnormal_sample_count or 0),
            "formal_task_count": len(formal_task_ids),
        },
        "target": (
            payload.get("target") if isinstance(payload.get("target"), dict) else {}
        ),
        "model_resolution": model_resolution,
        "linked_resources": {
            "formal_task_ids": formal_task_ids,
            "draft_task_id": row.draft_task_id,
            "aggregation_group_id": row.aggregation_group_id,
        },
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "finished_at": row.finished_at,
    }


def _all_chains(db: Session) -> list[dict[str, Any]]:
    strategy_rows = (
        db.query(StrategyTaskIntegration)
        .filter(StrategyTaskIntegration.deleted_at.is_(None))
        .all()
    )
    dataset_rows = (
        db.query(ExperimentDatasetImport)
        .filter(ExperimentDatasetImport.deleted_at.is_(None))
        .all()
    )
    group_ids = {
        int(row.aggregation_group_id)
        for row in dataset_rows
        if row.aggregation_group_id is not None
    }
    groups = (
        db.query(ExperimentDatasetGroup)
        .filter(ExperimentDatasetGroup.id.in_(group_ids))
        .all()
        if group_ids
        else []
    )
    group_by_id = {int(group.id): group for group in groups}
    items = [_normalize_strategy(row) for row in strategy_rows]
    items.extend(
        _normalize_dataset(
            row,
            (
                group_by_id.get(int(row.aggregation_group_id))
                if row.aggregation_group_id is not None
                else None
            ),
        )
        for row in dataset_rows
    )
    items.sort(
        key=lambda item: item.get("updated_at")
        or item.get("created_at")
        or datetime.min,
        reverse=True,
    )
    return items


def list_chains(
    db: Session,
    *,
    source_system: str | None,
    record_type: str | None,
    business_type: str | None,
    health: str | None,
    status: str | None,
    keyword: str | None,
    page: int,
    size: int,
    project_id: int | None = None,
) -> dict[str, Any]:
    items = _all_chains(db)
    source_value = str(source_system or "").strip().lower()
    record_value = str(record_type or "").strip().lower()
    business_value = str(business_type or "").strip().lower()
    health_value = str(health or "").strip().lower()
    status_value = str(status or "").strip().lower()
    keyword_value = str(keyword or "").strip().lower()
    filtered = []
    for item in items:
        if project_id is not None and item.get("project_id") != int(project_id):
            continue
        if source_value and str(item["source_system"]).lower() != source_value:
            continue
        if record_value and str(item["record_type"]).lower() != record_value:
            continue
        if business_value and str(item["business_type"]).lower() != business_value:
            continue
        if health_value and str(item["health"]).lower() != health_value:
            continue
        if status_value and str(item["status"]).lower() != status_value:
            continue
        if keyword_value:
            haystack = " ".join(
                str(item.get(key) or "")
                for key in (
                    "record_id",
                    "title",
                    "source_system",
                    "external_task_id",
                    "external_package_id",
                    "status_detail",
                )
            ).lower()
            if keyword_value not in haystack:
                continue
        filtered.append(item)
    start = page * size
    return {
        "items": filtered[start : start + size],
        "total": len(filtered),
        "page": page,
        "size": size,
    }


def _normalize_audit(row: ThirdPartyCallAudit) -> dict[str, Any]:
    business_type = _business_type_for(row.operation)
    return {
        "id": int(row.id),
        "project_id": row.project_id,
        "request_id": str(row.request_id),
        "source_system": str(row.source_system or "unknown"),
        "operation": str(row.operation),
        "operation_label": (
            "任务数据集创建"
            if row.operation == "experiment_dataset_import"
            else _STRATEGY_TYPE_LABELS.get(row.operation, row.operation)
        ),
        "business_type": business_type,
        "business_type_label": _BUSINESS_TYPE_LABELS[business_type],
        "method": str(row.request_method),
        "path": str(row.request_path),
        "schema_version": str(row.schema_version or ""),
        "external_task_id": str(row.external_task_id or ""),
        "external_package_id": str(row.external_package_id or ""),
        "response_status": int(row.response_status),
        "outcome": str(row.outcome),
        "duration_ms": int(row.duration_ms or 0),
        "request_bytes": int(row.request_bytes or 0),
        "client_ip": str(row.client_ip or ""),
        "user_agent": str(row.user_agent or ""),
        "error_summary": str(row.error_summary or ""),
        "received_at": row.received_at,
    }


def list_audit_events(
    db: Session,
    *,
    source_system: str | None,
    operation: str | None,
    business_type: str | None,
    outcome: str | None,
    keyword: str | None,
    hours: int,
    page: int,
    size: int,
    project_id: int | None = None,
) -> dict[str, Any]:
    query = db.query(ThirdPartyCallAudit).filter(
        ThirdPartyCallAudit.received_at >= datetime.now() - timedelta(hours=hours),
        # Historical calls without a deterministic project are held for the
        # ownership review endpoint and must not appear in an operational
        # project dashboard by default.
        ThirdPartyCallAudit.project_id.is_not(None),
    )
    if project_id is not None:
        query = query.filter(ThirdPartyCallAudit.project_id == int(project_id))
    if source_system:
        query = query.filter(
            ThirdPartyCallAudit.source_system == source_system.strip().lower()
        )
    if operation:
        query = query.filter(ThirdPartyCallAudit.operation == operation.strip())
    if business_type:
        operations = [
            value
            for value, category in _OPERATION_BUSINESS_TYPES.items()
            if category == business_type.strip().lower()
        ]
        if operations:
            query = query.filter(ThirdPartyCallAudit.operation.in_(operations))
    if outcome:
        query = query.filter(ThirdPartyCallAudit.outcome == outcome.strip().lower())
    if keyword:
        term = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                ThirdPartyCallAudit.request_id.ilike(term, escape=LIKE_ESCAPE),
                ThirdPartyCallAudit.external_task_id.ilike(term, escape=LIKE_ESCAPE),
                ThirdPartyCallAudit.external_package_id.ilike(
                    term, escape=LIKE_ESCAPE
                ),
                ThirdPartyCallAudit.request_path.ilike(term, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(
            ThirdPartyCallAudit.received_at.desc(), ThirdPartyCallAudit.id.desc()
        )
        .offset(page * size)
        .limit(size)
        .all()
    )
    return {
        "items": [_normalize_audit(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
        "hours": hours,
    }


def summary(db: Session, *, project_id: int | None = None) -> dict[str, Any]:
    if project_id is not None:
        project_id = int(project_id)
    chains = _all_chains(db)
    if project_id is not None:
        chains = [item for item in chains if item.get("project_id") == project_id]
    health_counts = {
        name: 0 for name in ("processing", "waiting", "blocked", "completed")
    }
    callback_pending = 0
    for item in chains:
        health = str(item.get("health") or "processing")
        health_counts[health] = health_counts.get(health, 0) + 1
        callback = item.get("callback")
        if isinstance(callback, dict) and callback.get("status") in {
            "pending",
            "retrying",
            "failed",
        }:
            callback_pending += 1
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    calls = db.query(ThirdPartyCallAudit).filter(
        ThirdPartyCallAudit.received_at >= today,
        # NULL-tenant calls are counted by the ownership audit, not by the
        # normal operational dashboard.
        ThirdPartyCallAudit.project_id.is_not(None),
    )
    if project_id is not None:
        calls = calls.filter(ThirdPartyCallAudit.project_id == project_id)
    calls_today = calls.count()
    call_failures_today = calls.filter(
        ThirdPartyCallAudit.response_status >= 400
    ).count()
    return {
        "total_chains": len(chains),
        **health_counts,
        "callback_pending": callback_pending,
        "calls_today": calls_today,
        "call_failures_today": call_failures_today,
        "source_systems": sorted(
            {str(item.get("source_system") or "unknown") for item in chains}
            | {
                str(value[0] or "unknown")
                for value in db.query(ThirdPartyCallAudit.source_system)
                .distinct()
                .all()
            }
        ),
        "generated_at": datetime.now(),
    }


def get_chain_detail(
    db: Session,
    *,
    record_type: str,
    record_id: str,
    project_id: int | None = None,
) -> dict[str, Any] | None:
    if record_type == "strategy_task":
        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id == record_id,
                StrategyTaskIntegration.deleted_at.is_(None),
            )
            .first()
        )
        if not row:
            return None
        # Strategy records are no longer partitioned by project.  Keep the
        # legacy query parameter accepted for client compatibility, but do not
        # apply a retired project filter.
        chain = _normalize_strategy(row)
        raw = {
            "progress": _json_object(row.platform_progress_json),
            "result": _json_object(row.platform_result_json),
            "error": _json_object(row.platform_error_json),
        }
        timeline = [
            {
                "time": row.created_at,
                "label": "第三方任务已接收",
                "status": "accepted",
                "detail": f"schema {row.schema_version or '-'}",
            },
            {
                "time": row.updated_at,
                "label": chain["status_label"],
                "status": chain["status"],
                "detail": chain["status_detail"],
            },
        ]
    elif record_type == "dataset_import":
        row = (
            db.query(ExperimentDatasetImport)
            .filter(
                ExperimentDatasetImport.package_id == record_id,
                ExperimentDatasetImport.deleted_at.is_(None),
            )
            .first()
        )
        if not row:
            return None
        group = (
            db.query(ExperimentDatasetGroup)
            .filter(ExperimentDatasetGroup.id == row.aggregation_group_id)
            .first()
            if row.aggregation_group_id is not None
            else None
        )
        chain = _normalize_dataset(row, group)
        raw = {
            "model_resolution": (
                _json_object(group.model_resolution_json) if group else {}
            ),
            "source_summary": {
                "dataset_type": row.dataset_type,
                "dataset_date": row.dataset_date,
                "event_type": row.event_type,
                "model_id": row.model_id,
                "aggregation_key": row.aggregation_key,
            },
        }
        timeline = [
            {
                "time": row.created_at,
                "label": "数据包已接收",
                "status": "accepted",
                "detail": row.package_id,
            },
            {
                "time": row.updated_at,
                "label": chain["status_label"],
                "status": chain["status"],
                "detail": chain["status_detail"],
            },
        ]
    else:
        return None
    audit_query = db.query(ThirdPartyCallAudit)
    if project_id is not None:
        audit_query = audit_query.filter(ThirdPartyCallAudit.project_id == int(project_id))
    if record_type == "strategy_task":
        audit_query = audit_query.filter(
            ThirdPartyCallAudit.external_task_id == record_id
        )
    else:
        audit_query = audit_query.filter(
            ThirdPartyCallAudit.external_package_id == record_id
        )
    audit_rows = (
        audit_query.order_by(ThirdPartyCallAudit.received_at.desc()).limit(50).all()
    )
    normalized_audit_rows = [_normalize_audit(item) for item in audit_rows]
    return {
        "chain": chain,
        "timeline": [item for item in timeline if item.get("time") is not None],
        # call_events is the canonical name; audit_events is retained for
        # clients built against the original flexible-workflow contract.
        "call_events": normalized_audit_rows,
        "audit_events": normalized_audit_rows,
        "evidence": raw,
    }
