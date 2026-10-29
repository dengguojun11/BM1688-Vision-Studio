from __future__ import annotations

import json
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_attachment, crud_sample
from labelu.internal.application.command.yolo_edge_validation import (
    RefineEdgeThresholdsCommand,
)
from labelu.internal.application.response.yolo_edge_validation import (
    EdgeThresholdRefinementResponse,
    EdgeThresholdsResponse,
)
from labelu.internal.application.service.yolo_edge_threshold_recommendation import (
    _default_thresholds,
    _load_selected_images_jpeg,
    _model_dump_compat,
    request_edge_threshold_recommendation,
)
from labelu.internal.application.service.yolo_edge_validation import (
    _decode_json,
    _job_request_payload,
    _job_result_json,
    _load_job_with_access,
)
from labelu.internal.application.service.yolo_train_evaluation import _build_provider_specs
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.user import User


def _thresholds_from_row_payload(payload: dict[str, Any] | None) -> EdgeThresholdsResponse:
    payload = payload if isinstance(payload, dict) else {}
    defaults = _default_thresholds()
    return EdgeThresholdsResponse(
        conf_thres=float(payload.get("conf_thres") or defaults.conf_thres),
        nms_thres=float(payload.get("nms_thres") or defaults.nms_thres),
        match_iou_thres=float(payload.get("match_iou_thres") or defaults.match_iou_thres),
        source=str(payload.get("source") or "").strip() or None,
        reason=str(payload.get("reason") or "").strip() or None,
        confidence_level=str(payload.get("confidence_level") or "").strip() or None,
        provider_key=str(payload.get("provider_key") or "").strip() or None,
    )


def _truncate_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def build_edge_threshold_refinement_context(
    *,
    row,
    request_payload: dict[str, Any],
    result_json: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    execution_summary = (
        result_json.get("execution_summary")
        if isinstance(result_json.get("execution_summary"), dict)
        else {}
    )
    prediction_summary = (
        result_json.get("prediction_summary")
        if isinstance(result_json.get("prediction_summary"), dict)
        else {}
    )
    eval_summary = (
        result_json.get("eval_summary")
        if isinstance(result_json.get("eval_summary"), dict)
        else {}
    )
    execution = result_json.get("execution") if isinstance(result_json.get("execution"), dict) else {}
    pack = result_json.get("pack") if isinstance(result_json.get("pack"), dict) else {}
    thresholds = _thresholds_from_row_payload(_decode_json(getattr(row, "thresholds_json", None), {}))
    ai_thresholds = _decode_json(getattr(row, "ai_thresholds_json", None), None)

    context = {
        "job_id": int(getattr(row, "id", 0) or 0),
        "export_id": int(getattr(row, "export_id", 0) or 0),
        "task_id": int(getattr(row, "task_id", 0) or 0),
        "mode": str(getattr(row, "mode", "") or ""),
        "job_status": str(getattr(row, "status", "") or ""),
        "threshold_source": str(getattr(row, "threshold_source", "") or ""),
        "selected_sample_ids": request_payload.get("sample_ids") or [],
        "selected_attachment_ids": request_payload.get("attachment_ids") or [],
        "image_count": int(getattr(row, "image_count", 0) or 0),
        "labeled_image_count": int(getattr(row, "labeled_image_count", 0) or 0),
        "unlabeled_image_count": int(getattr(row, "unlabeled_image_count", 0) or 0),
        "base_thresholds": _model_dump_compat(thresholds),
        "first_stage_ai_thresholds": ai_thresholds if isinstance(ai_thresholds, dict) else None,
        "pack_summary": {
            "copied_count": pack.get("copied_count"),
            "labeled_count": pack.get("labeled_count"),
            "unlabeled_count": pack.get("unlabeled_count"),
        },
        "execution_summary": execution_summary,
        "prediction_summary": prediction_summary,
        "eval_summary": eval_summary,
        "execution_error": _truncate_text(execution.get("error"), limit=800) or None,
        "remote_stdout_tail": _truncate_text(execution.get("remote_stdout_tail"), limit=1200) or None,
        "remote_stderr_tail": _truncate_text(execution.get("remote_stderr_tail"), limit=800) or None,
    }

    user_text = (
        "请基于本次 YOLO 边端验证的真实执行与评估结果，给出第二阶段阈值修正建议。\n"
        "你必须只返回 JSON，不要输出 Markdown，不要额外解释。\n"
        "返回 JSON 格式如下：\n"
        '{"recommended_thresholds":{"conf_thres":0.30,"nms_thres":0.68,"match_iou_thres":0.50,'
        '"reason":"修正原因","confidence_level":"medium"}}\n'
        "约束：\n"
        "- conf_thres 范围 0.15~0.60\n"
        "- nms_thres 范围 0.30~0.80\n"
        "- match_iou_thres 范围 0.40~0.70\n"
        "- 若本次无标注图像或无有效评估结果，不要激进调整 match_iou_thres\n"
        "- confidence_level 仅允许 low / medium / high\n"
        "以下是本次真实边端验证摘要：\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )
    return user_text, context


def _load_refinement_images(
    db: Session,
    *,
    task_id: int,
    request_payload: dict[str, Any],
    max_images: int,
) -> list[bytes]:
    sample_ids = [
        int(sid)
        for sid in (request_payload.get("sample_ids") or [])
        if int(sid) > 0
    ]
    attachment_ids = [
        int(aid)
        for aid in (request_payload.get("attachment_ids") or [])
        if int(aid) > 0
    ]
    samples = crud_sample.get_by_ids(db, sample_ids=sample_ids, task_id=task_id) if sample_ids else []
    attachments = crud_attachment.get_by_ids(db, attachment_ids=attachment_ids)
    return _load_selected_images_jpeg(
        db,
        samples=samples,
        attachments=attachments,
        limit=max_images,
    )


def refine_thresholds(
    db: Session,
    *,
    job_id: int,
    cmd: RefineEdgeThresholdsCommand,
    current_user: User,
) -> EdgeThresholdRefinementResponse:
    row, _task = _load_job_with_access(db, job_id=job_id, current_user=current_user)
    if str(getattr(row, "status", "") or "") not in (
        "COMPLETED",
        "PARTIAL_COMPLETED",
        "FAILED",
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="当前边端验证任务尚无可用于二次修正的结果",
        )

    result_json = _job_result_json(row)
    if not isinstance(result_json.get("execution_summary"), dict):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="当前边端验证任务缺少结构化摘要，请先重新执行边端验证",
        )

    request_payload = _job_request_payload(row)
    base_thresholds = _thresholds_from_row_payload(
        _decode_json(getattr(row, "thresholds_json", None), {})
    )
    user_text, context = build_edge_threshold_refinement_context(
        row=row,
        request_payload=request_payload,
        result_json=result_json,
    )

    specs = _build_provider_specs()
    if not specs:
        raise LabelUException(
            code=ErrorCode.CODE_57007_YOLO_EVAL_VISION_NOT_CONFIGURED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    provider_key, base_url, api_key, model = specs[0]
    images_jpeg = []
    if bool(getattr(cmd, "include_visual_evidence", True)):
        images_jpeg = _load_refinement_images(
            db,
            task_id=int(getattr(row, "task_id", 0) or 0),
            request_payload=request_payload,
            max_images=int(getattr(cmd, "max_images", 4) or 4),
        )

    ai_response = request_edge_threshold_recommendation(
        provider_key=provider_key,
        base_url=base_url,
        api_key=api_key,
        model=model,
        user_text=user_text,
        images_jpeg=images_jpeg,
        labeled_image_count=int(context.get("labeled_image_count") or 0),
    )
    refined_thresholds = ai_response.recommended_thresholds
    refined_thresholds.source = "ai_refined" if not ai_response.fallback_used else refined_thresholds.source

    delta_summary = {
        "conf_delta": round(refined_thresholds.conf_thres - base_thresholds.conf_thres, 4),
        "nms_delta": round(refined_thresholds.nms_thres - base_thresholds.nms_thres, 4),
        "match_iou_delta": round(
            refined_thresholds.match_iou_thres - base_thresholds.match_iou_thres, 4
        ),
    }

    if refined_thresholds.reason:
        refined_thresholds.reason = refined_thresholds.reason[:2000]

    return EdgeThresholdRefinementResponse(
        base_thresholds=base_thresholds,
        refined_thresholds=refined_thresholds,
        delta_summary=delta_summary,
        fallback_used=ai_response.fallback_used,
    )
