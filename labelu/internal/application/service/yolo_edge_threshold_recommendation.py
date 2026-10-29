from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import status

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_sample,
    crud_task,
    crud_yolo_bm1688_export,
    crud_yolo_eval_report,
    crud_yolo_train_job,
)
from labelu.internal.application.command.yolo_edge_validation import (
    RecommendEdgeThresholdsCommand,
)
from labelu.internal.application.response.yolo_edge_validation import (
    EdgeThresholdRecommendationResponse,
    EdgeThresholdsResponse,
)
from labelu.internal.application.service.vision_multimodal_chat import (
    chat_multimodal_markdown,
)
from labelu.internal.application.service.yolo_bm1688_export import (
    get_export_for_download,
)
from labelu.internal.application.service.yolo_edge_validation import _sample_has_annotation
from labelu.internal.application.service.yolo_train_evaluation import _build_provider_specs
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.yolo_training_metrics import (
    final_metrics_from_metrics_summary,
    format_training_metrics_line,
    read_test_val_metrics_from_summary,
)
from labelu.internal.domain.models.user import User

_DEFAULT_NMS_THRES = 0.7
_AI_SYSTEM_PROMPT = (
    "你是 YOLO 边端部署与验证专家。"
    "请根据模型训练指标、历史 AI 评估摘要、以及本次待验证图片摘要，"
    "为边端验证推荐阈值。"
    "你必须只返回 JSON，不要输出 Markdown，不要额外解释。"
)


def _model_dump_compat(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        data = model.model_dump()
    elif hasattr(model, "dict"):
        data = model.dict()
    else:
        raise TypeError(f"Unsupported model type: {type(model)!r}")
    return data if isinstance(data, dict) else dict(data or {})


def _default_thresholds() -> EdgeThresholdsResponse:
    return EdgeThresholdsResponse(
        conf_thres=float(settings.YOLO_SOP_EVAL_CONF_THRES),
        nms_thres=_DEFAULT_NMS_THRES,
        match_iou_thres=float(settings.YOLO_SOP_EVAL_IOU_THRES),
        source="system_default",
        confidence_level="medium",
    )


def _safe_media_path(stored_path: str | None) -> Path | None:
    if not stored_path:
        return None
    root = Path(settings.MEDIA_ROOT).resolve()
    candidate = (root / str(stored_path)).resolve()
    if not str(candidate).startswith(str(root)):
        return None
    return candidate if candidate.is_file() else None


def _load_attachments_images_jpeg(*, attachments: list[Any], limit: int = 4) -> list[bytes]:
    try:
        from io import BytesIO

        from PIL import Image
    except ImportError:
        return []
    out: list[bytes] = []
    for attachment in attachments:
        local_path = _safe_media_path(getattr(attachment, "path", None))
        if not local_path:
            continue
        try:
            with Image.open(local_path) as im:
                im = im.convert("RGB")
                max_side = max(im.size)
                if max_side > 1024:
                    scale = 1024.0 / float(max_side)
                    im = im.resize(
                        (int(im.size[0] * scale), int(im.size[1] * scale)),
                        Image.Resampling.LANCZOS,
                    )
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=82)
                out.append(buf.getvalue())
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def _attachments_for_samples(db: Session, *, samples: list[Any]) -> list[Any]:
    attachment_ids = [int(sample.file_id) for sample in samples if getattr(sample, "file_id", None)]
    if not attachment_ids:
        return []
    attachments = {
        int(row.id): row for row in crud_attachment.get_by_ids(db, attachment_ids=attachment_ids)
    }
    ordered: list[Any] = []
    for sample in samples:
        attachment = attachments.get(int(getattr(sample, "file_id", 0) or 0))
        if attachment:
            ordered.append(attachment)
    return ordered


def _load_selected_images_jpeg(
    db: Session,
    *,
    samples: list[Any],
    attachments: list[Any],
    limit: int = 4,
) -> list[bytes]:
    merged: list[Any] = []
    seen: set[int] = set()
    for attachment in _attachments_for_samples(db, samples=samples) + list(attachments or []):
        attachment_id = int(getattr(attachment, "id", 0) or 0)
        if attachment_id <= 0 or attachment_id in seen:
            continue
        seen.add(attachment_id)
        merged.append(attachment)
    return _load_attachments_images_jpeg(attachments=merged, limit=limit)


def _compact_report_snippets(job_id: int, db: Session, limit: int = 2) -> str:
    bodies = crud_yolo_eval_report.latest_full_bodies_for_prior(db, job_id=job_id, limit=limit)
    if not bodies:
        return "无历史 AI 评估摘要"
    parts: list[str] = []
    for idx, body in enumerate(bodies, start=1):
        chunk = body.strip()
        if len(chunk) > 1000:
            chunk = chunk[:1000] + "\n(截断)"
        parts.append(f"[报告{idx}]\n{chunk}")
    return "\n\n".join(parts)


def _training_metrics_block(train_job) -> str:
    val_metrics = final_metrics_from_metrics_summary(getattr(train_job, "metrics_summary", None))
    test_metrics = read_test_val_metrics_from_summary(getattr(train_job, "metrics_summary", None))
    lines: list[str] = []
    if isinstance(val_metrics, dict):
        lines.append("训练验证集指标: " + (format_training_metrics_line(val_metrics) or "无"))
    if isinstance(test_metrics, dict) and str(test_metrics.get("status") or "") == "ok":
        lines.append(
            "训练测试集指标: "
            + (
                format_training_metrics_line(
                    {
                        "map50_95": test_metrics.get("map50_95"),
                        "map50": test_metrics.get("map50"),
                        "precision": test_metrics.get("precision"),
                        "recall": test_metrics.get("recall"),
                    }
                )
                or "无"
            )
        )
    if not lines:
        return "无可用训练指标"
    return "\n".join(lines)


def build_edge_threshold_recommendation_context(
    db: Session,
    *,
    export_row,
    task,
    train_job,
    samples: list[Any],
    attachments: list[Any],
    mode: str,
) -> tuple[str, dict[str, Any]]:
    labeled_count = sum(1 for sample in samples if _sample_has_annotation(sample))
    sample_count = len(samples)
    attachment_count = len(attachments)
    total_image_count = sample_count + attachment_count
    unlabeled_count = total_image_count - labeled_count
    model_filename = (
        Path(str(getattr(export_row, "bmodel_rel_path", "") or "")).name
        or Path(str(getattr(export_row, "onnx_rel_path", "") or "")).name
        or str(getattr(train_job, "model_weights", "") or "")
        or f"export-{export_row.id}"
    )
    context = {
        "task_id": int(task.id),
        "task_name": task.name,
        "export_id": int(export_row.id),
        "train_job_id": int(train_job.id),
        "model_filename": model_filename,
        "mode": mode,
        "imgsz": int(getattr(export_row, "imgsz", 640) or 640),
        "selected_image_count": total_image_count,
        "task_sample_count": sample_count,
        "external_attachment_count": attachment_count,
        "labeled_image_count": labeled_count,
        "unlabeled_image_count": unlabeled_count,
        "sample_ids": [int(sample.id) for sample in samples[:20]],
        "attachment_ids": [int(attachment.id) for attachment in attachments[:20]],
        "training_metrics": _training_metrics_block(train_job),
        "prior_ai_reports": _compact_report_snippets(int(train_job.id), db),
    }
    user_text = (
        "请为本次 YOLO 边端验证推荐阈值。\n"
        "返回 JSON 格式如下：\n"
        '{"recommended_thresholds":{"conf_thres":0.32,"nms_thres":0.70,"match_iou_thres":0.50,'
        '"reason":"推荐原因","confidence_level":"medium"}}\n'
        "约束：\n"
        "- conf_thres 范围 0.15~0.60\n"
        "- nms_thres 范围 0.30~0.80\n"
        "- match_iou_thres 范围 0.40~0.70\n"
        "- 当本次无标注图片时，不要主动激进调整 match_iou_thres，优先保持 0.50\n"
        "- confidence_level 仅允许 low / medium / high\n"
        "以下是本次上下文：\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )
    return user_text, context


def _parse_ai_threshold_payload(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    candidates: list[str] = [raw]
    for marker in ("```json", "```"):
        if marker in raw:
            chunks = raw.split(marker)
            for chunk in chunks:
                chunk = chunk.strip()
                if chunk.startswith("{") and chunk.endswith("}"):
                    candidates.append(chunk)
    first = raw.find("{")
    last = raw.rfind("}")
    if 0 <= first < last:
        candidates.append(raw[first : last + 1])
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        inner = obj.get("recommended_thresholds")
        if isinstance(inner, dict):
            return inner
        if any(key in obj for key in ("conf_thres", "nms_thres", "match_iou_thres")):
            return obj
    return None


def validate_ai_thresholds(
    payload: dict[str, Any] | None,
    *,
    labeled_image_count: int,
    provider_key: str | None,
) -> EdgeThresholdsResponse:
    defaults = _default_thresholds()
    if not isinstance(payload, dict):
        fallback_data = _model_dump_compat(defaults)
        fallback_data.update(
            source="system_fallback",
            reason="AI 推荐结果无法解析，已回退到系统默认阈值",
            confidence_level="low",
            provider_key=provider_key,
        )
        return EdgeThresholdsResponse(**fallback_data)

    def _float_or_default(key: str, default_value: float) -> float:
        try:
            return float(payload.get(key, default_value))
        except (TypeError, ValueError):
            return default_value

    conf = min(max(_float_or_default("conf_thres", defaults.conf_thres), 0.15), 0.60)
    nms = min(max(_float_or_default("nms_thres", defaults.nms_thres), 0.30), 0.80)
    match_iou = min(
        max(_float_or_default("match_iou_thres", defaults.match_iou_thres), 0.40),
        0.70,
    )
    reason = str(payload.get("reason") or "").strip() or None
    confidence_level = str(payload.get("confidence_level") or "medium").strip().lower()
    if confidence_level not in ("low", "medium", "high"):
        confidence_level = "medium"
    if labeled_image_count <= 0:
        match_iou = defaults.match_iou_thres
        suffix = "本次所选图片无标注，match_iou_thres 已保持系统默认值。"
        reason = (reason + " " + suffix).strip() if reason else suffix
    return EdgeThresholdsResponse(
        conf_thres=conf,
        nms_thres=nms,
        match_iou_thres=match_iou,
        source="ai_recommended",
        reason=reason,
        confidence_level=confidence_level,
        provider_key=provider_key,
    )


def request_edge_threshold_recommendation(
    *,
    provider_key: str,
    base_url: str,
    api_key: str,
    model: str,
    user_text: str,
    images_jpeg: list[bytes],
    labeled_image_count: int,
) -> EdgeThresholdRecommendationResponse:
    try:
        body = chat_multimodal_markdown(
            base_url=base_url,
            api_key=api_key,
            model=model,
            user_text=user_text,
            images_jpeg=images_jpeg,
            max_tokens=2048,
            temperature=0.15,
            system_prompt=_AI_SYSTEM_PROMPT,
        )
    except Exception as exc:
        fallback = validate_ai_thresholds(
            None,
            labeled_image_count=labeled_image_count,
            provider_key=provider_key,
        )
        fallback.reason = f"AI 推荐调用失败，已回退到系统默认阈值：{str(exc)[:240]}"
        return EdgeThresholdRecommendationResponse(
            recommended_thresholds=fallback,
            fallback_used=True,
        )

    thresholds = validate_ai_thresholds(
        _parse_ai_threshold_payload(body),
        labeled_image_count=labeled_image_count,
        provider_key=provider_key,
    )
    return EdgeThresholdRecommendationResponse(
        recommended_thresholds=thresholds,
        fallback_used=thresholds.source != "ai_recommended",
    )


def recommend_thresholds(
    db: Session,
    *,
    cmd: RecommendEdgeThresholdsCommand,
    current_user: User,
) -> EdgeThresholdRecommendationResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    export_row = get_export_for_download(db, export_id=cmd.export_id, current_user=current_user)
    if int(export_row.task_id) != int(cmd.task_id):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="所选模型与任务不匹配",
        )
    task = crud_task.get(db=db, task_id=cmd.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    train_job = crud_yolo_train_job.get(db, int(export_row.yolo_train_job_id))
    if not train_job:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    sample_ids = [int(sid) for sid in (cmd.sample_ids or []) if int(sid) > 0]
    attachment_ids = [int(aid) for aid in (cmd.attachment_ids or []) if int(aid) > 0]
    if not sample_ids and not attachment_ids:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请至少选择一张图片后再请求 AI 推荐阈值",
        )
    samples = crud_sample.get_by_ids(db, sample_ids=sample_ids, task_id=cmd.task_id)
    if len(samples) != len(set(sample_ids)):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="存在无效样本，或样本不属于所选任务",
        )
    attachments = crud_attachment.get_by_ids(db, attachment_ids=attachment_ids) if attachment_ids else []
    if len(attachments) != len(set(attachment_ids)) or any(
        int(getattr(attachment, "task_id", 0) or 0) != int(cmd.task_id)
        for attachment in attachments
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="external attachments invalid",
        )

    specs = _build_provider_specs()
    if not specs:
        raise LabelUException(
            code=ErrorCode.CODE_57007_YOLO_EVAL_VISION_NOT_CONFIGURED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    provider_key, base_url, api_key, model = specs[0]
    user_text, context = build_edge_threshold_recommendation_context(
        db,
        export_row=export_row,
        task=task,
        train_job=train_job,
        samples=samples,
        attachments=attachments,
        mode=cmd.mode,
    )
    images_jpeg = _load_selected_images_jpeg(
        db,
        samples=samples,
        attachments=attachments,
    )
    response = request_edge_threshold_recommendation(
        provider_key=provider_key,
        base_url=base_url,
        api_key=api_key,
        model=model,
        user_text=user_text,
        images_jpeg=images_jpeg,
        labeled_image_count=int(context["labeled_image_count"]),
    )
    if response.recommended_thresholds.reason:
        response.recommended_thresholds.reason = (
            response.recommended_thresholds.reason[:2000]
        )
    return response
