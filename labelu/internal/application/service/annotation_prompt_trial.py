"""Execute one contract-bound image prompt without creating annotation data."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from io import BytesIO
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.application.command.annotation_prompt_trial import (
    AnnotationPromptTrialCommand,
)
from labelu.internal.application.response.annotation_prompt_trial import (
    AnnotationPromptTrialResponse,
)
from labelu.internal.application.service import annotation_prompt as prompt_service
from labelu.internal.application.service import auto_label
from labelu.internal.application.service import vision_llm_chain
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.annotation_prompt import AnnotationPromptBinding
from labelu.internal.domain.models.user import User


def _error(message: str, http_status: int = status.HTTP_400_BAD_REQUEST) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30002_VALIDATION_ERROR,
        status_code=http_status,
        message=message,
    )


def _data_uri(image_bytes: bytes, content_type: str | None) -> str:
    mime = str(content_type or "image/jpeg").split(";", 1)[0].strip().lower()
    if not mime.startswith("image/"):
        # Browsers may send ``application/octet-stream`` when a file has no
        # trusted extension.  The image was decoded before this helper is
        # called, so infer only for that generic MIME and still reject an
        # explicitly non-image content type.
        if mime in {"", "application/octet-stream", "binary/octet-stream"}:
            try:
                from PIL import Image

                with Image.open(BytesIO(image_bytes)) as image:
                    image_format = str(image.format or "").lower()
            except Exception as exc:
                raise _error("试运行文件必须是图片") from exc
            mime = {
                "jpg": "image/jpeg",
                "jpeg": "image/jpeg",
                "png": "image/png",
                "webp": "image/webp",
                "gif": "image/gif",
                "bmp": "image/bmp",
                "tiff": "image/tiff",
            }.get(image_format, "")
        if not mime.startswith("image/"):
            raise _error("试运行文件必须是图片")
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"


def _prompt_row(
    db: Session,
    *,
    contract: Any,
    dataset_id: int,
    prompt_id: int,
) -> AnnotationPromptBinding:
    row = (
        db.query(AnnotationPromptBinding)
        .filter(
            AnnotationPromptBinding.id == int(prompt_id),
            AnnotationPromptBinding.dataset_id == int(dataset_id),
            AnnotationPromptBinding.dataset_label_contract_id == int(contract.id),
            AnnotationPromptBinding.contract_revision == int(contract.revision),
        )
        .one_or_none()
    )
    if row is None:
        raise _error("提示词不存在或不属于当前标签契约", status.HTTP_404_NOT_FOUND)
    if str(row.status or "").upper() in {
        prompt_service.INVALIDATED,
        prompt_service.SUPERSEDED,
    }:
        raise _error("当前提示词已失效，不能用于试运行", status.HTTP_409_CONFLICT)
    _assert_prompt_row_integrity(row, contract)
    return row


def _assert_prompt_row_integrity(row: AnnotationPromptBinding, contract: Any) -> None:
    """Fail closed when a stored artifact no longer matches its contract."""

    if (
        str(row.label_set_signature or "") != str(contract.label_set_signature or "")
        or str(row.class_order_signature or "")
        != str(contract.class_order_signature or "")
    ):
        raise _error("提示词与当前标签契约签名不一致，请重新生成", status.HTTP_409_CONFLICT)
    effective_prompt = str(row.effective_prompt or "")
    if prompt_service._prompt_sha256(effective_prompt) != str(row.prompt_sha256 or ""):
        raise _error("提示词校验失败，请重新生成", status.HTTP_409_CONFLICT)
    try:
        spec = json.loads(row.prompt_spec_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _error("提示词契约快照损坏，请重新生成", status.HTTP_409_CONFLICT) from exc
    if not isinstance(spec, dict):
        raise _error("提示词契约快照损坏，请重新生成", status.HTTP_409_CONFLICT)
    try:
        spec_matches_contract = (
            int(spec.get("contract_id") or 0) == int(contract.id)
            and int(spec.get("contract_revision") or 0) == int(contract.revision)
            and str(spec.get("label_set_signature") or "")
            == str(contract.label_set_signature or "")
            and str(spec.get("class_order_signature") or "")
            == str(contract.class_order_signature or "")
        )
    except (TypeError, ValueError):
        spec_matches_contract = False
    if not spec_matches_contract:
        raise _error("提示词契约快照与当前标签契约不一致，请重新生成", status.HTTP_409_CONFLICT)


def _latest_prompt_row(
    db: Session,
    *,
    contract: Any,
    dataset_id: int,
    operation: str,
    provider_key: str,
    model_name: str | None,
) -> AnnotationPromptBinding | None:
    rows = (
        db.query(AnnotationPromptBinding)
        .filter(
            AnnotationPromptBinding.dataset_id == int(dataset_id),
            AnnotationPromptBinding.dataset_label_contract_id == int(contract.id),
            AnnotationPromptBinding.contract_revision == int(contract.revision),
            AnnotationPromptBinding.operation == str(operation),
            AnnotationPromptBinding.provider_key == str(provider_key),
            AnnotationPromptBinding.status.in_([prompt_service.PUBLISHED, prompt_service.DRAFT]),
        )
        .order_by(
            AnnotationPromptBinding.updated_at.desc(), AnnotationPromptBinding.id.desc()
        )
        .all()
    )
    candidates = rows
    if model_name:
        exact = [
            row for row in rows if str(row.model_name or "") == str(model_name)
        ]
        # Prefer a model-pinned artifact, then allow the provider-wide
        # (un-pinned) artifact as the same fallback used by runtime lookup.
        candidates = exact or [row for row in rows if not row.model_name]
    if not candidates:
        return None
    for wanted_status in (prompt_service.PUBLISHED, prompt_service.DRAFT):
        selected = next(
            (row for row in candidates if str(row.status or "").upper() == wanted_status),
            None,
        )
        if selected is not None:
            _assert_prompt_row_integrity(selected, contract)
            return selected
    return None


def _prompt_binding_from_spec(
    *,
    row: AnnotationPromptBinding | None,
    contract: Any,
    spec: dict[str, Any],
    effective_prompt: str,
    dataset_id: int,
) -> dict[str, Any]:
    return {
        "id": int(row.id) if row is not None else None,
        "dataset_id": int(dataset_id),
        "contract_id": int(contract.id),
        "contract_revision": int(contract.revision),
        "label_set_signature": str(contract.label_set_signature),
        "class_order_signature": str(contract.class_order_signature),
        "operation": str(spec.get("operation") or ""),
        "provider_key": str(spec.get("provider_key") or ""),
        "model_name": spec.get("model_name"),
        "profile_key": str(spec.get("profile_key") or ""),
        "profile_version": str(spec.get("profile_version") or ""),
        "prompt_sha256": prompt_service._prompt_sha256(effective_prompt),
        "effective_prompt": effective_prompt,
        "prompt_spec": spec,
    }


def _task_config_from_spec(spec: dict[str, Any], operation: str) -> dict[str, Any]:
    if operation == "bbox_detection":
        attributes = []
        for row in spec.get("labels") or []:
            if not isinstance(row, dict):
                continue
            value = str(row.get("label_value") or row.get("name") or "").strip()
            if not value:
                continue
            attributes.append(
                {
                    "value": value,
                    "key": str(row.get("label_key") or row.get("display_name") or value),
                    "color": row.get("color"),
                }
            )
        return {"tools": [{"tool": "rectTool", "config": {"attributes": attributes}}]}

    dimensions = []
    for row in spec.get("tag_dimensions") or []:
        if isinstance(row, dict):
            dimensions.append(row)
    attributes = []
    for row in dimensions:
        attributes.append(
            {
                "value": row.get("field_key"),
                "key": row.get("name_cn") or row.get("field_key"),
                "type": "enum",
                "options": row.get("options") or [],
            }
        )
    return {"tools": [{"tool": "tagTool", "config": {"attributes": attributes}}]}


def _model_labels_from_spec(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate persisted contract rows to the model payload label shape."""

    labels: list[dict[str, Any]] = []
    for row in spec.get("labels") or []:
        if not isinstance(row, dict):
            continue
        value = str(row.get("label_value") or row.get("name") or "").strip()
        if not value:
            continue
        labels.append(
            {
                "name": value,
                "display_name": str(
                    row.get("label_key") or row.get("display_name") or value
                ),
                "tool": "rectTool",
                "description": row.get("description"),
                "attributes": row.get("attributes") or [],
            }
        )
    return labels


def _model_dimensions(raw_bytes: bytes, operation: str) -> tuple[int, int]:
    max_side = (
        getattr(settings, "AI_BBOX_VISION_MAX_IMAGE_SIDE", 2560)
        if operation == "bbox_detection"
        else getattr(settings, "AI_VISION_MAX_IMAGE_SIDE", 1536)
    )
    _, dimensions = vision_llm_chain._prepare_image_for_inference(
        raw_bytes, max_side=max_side
    )
    return int(dimensions[0]), int(dimensions[1])


async def run_trial(
    db: Session,
    dataset_id: int,
    revision: int,
    image_bytes: bytes,
    current_user: User,
    cmd: AnnotationPromptTrialCommand,
    *,
    image_content_type: str | None = None,
    image_name: str = "trial-image",
) -> AnnotationPromptTrialResponse:
    """Call the configured VLM for one uploaded image without persistence."""

    if not image_bytes:
        raise _error("试运行图片不能为空")
    max_size = int(getattr(settings, "UPLOAD_FILE_MAX_SIZE", 200_000_000) or 200_000_000)
    if len(image_bytes) > max_size:
        raise _error(f"试运行图片不能超过 {max_size} 字节")

    _, contract = prompt_service._contract_for_revision(
        db,
        int(dataset_id),
        int(revision),
        current_user,
        manage=False,
        prefer_current=True,
    )
    try:
        image_width, image_height = vision_llm_chain._image_dimensions(image_bytes)
    except Exception as exc:
        raise _error("试运行文件不是可识别的图片") from exc
    if image_width <= 0 or image_height <= 0:
        raise _error("试运行图片尺寸无效")

    override = cmd.prompt_override
    if cmd.prompt_text is not None:
        if override is not None and override != cmd.prompt_text:
            raise _error("prompt_override 与 prompt_text 内容不一致")
        override = cmd.prompt_text

    row = (
        _prompt_row(
            db,
            contract=contract,
            dataset_id=int(dataset_id),
            prompt_id=int(cmd.prompt_id),
        )
        if cmd.prompt_id is not None
        else _latest_prompt_row(
            db,
            contract=contract,
            dataset_id=int(dataset_id),
            operation=cmd.operation,
            provider_key=cmd.provider_key,
            model_name=cmd.model_name,
        )
    )

    operation = str(row.operation if row is not None else cmd.operation)
    provider_key = str(row.provider_key if row is not None else cmd.provider_key)
    model_name = row.model_name if row is not None else cmd.model_name
    profile_key = str(row.profile_key if row is not None else cmd.profile_key)
    profile_version = str(row.profile_version if row is not None else cmd.profile_version)
    if row is not None:
        if str(cmd.operation) != operation:
            raise _error("prompt_id 与 operation 不匹配", status.HTTP_409_CONFLICT)
        if str(cmd.provider_key) != provider_key:
            raise _error("prompt_id 与 provider_key 不匹配", status.HTTP_409_CONFLICT)
        if cmd.model_name and row.model_name and str(cmd.model_name) != str(row.model_name):
            raise _error("prompt_id 与 model_name 不匹配", status.HTTP_409_CONFLICT)

    spec, generated_prompt, generated_warnings = prompt_service._build_prompt_spec(
        contract,
        operation=operation,
        provider_key=provider_key,
        model_name=model_name,
        profile_key=profile_key,
        profile_version=profile_version,
        image_width=int(image_width),
        image_height=int(image_height),
    )
    effective_prompt = str(
        override
        if override is not None
        else (row.effective_prompt if row is not None else generated_prompt)
    )
    prompt_service._validate_effective_prompt(effective_prompt, spec)
    prompt_sha256 = prompt_service._prompt_sha256(effective_prompt)
    model_width, model_height = _model_dimensions(image_bytes, operation)
    rendered_prompt = prompt_service.render_prompt_template(
        effective_prompt, model_width, model_height
    )
    binding = _prompt_binding_from_spec(
        row=row,
        contract=contract,
        spec=spec,
        effective_prompt=effective_prompt,
        dataset_id=int(dataset_id),
    )
    model_payload = {
        "image_url": _data_uri(image_bytes, image_content_type),
        "task": {"id": None, "name": "annotation-prompt-trial"},
        "labels": _model_labels_from_spec(spec),
        "tag_dimensions": spec.get("tag_dimensions") or [],
        "constraints": spec.get("constraints") or {},
        "prompt": None,
        "contract_prompt": binding,
        "prompt_binding": binding,
    }

    operation_is_bbox = operation == "bbox_detection"
    start = time.perf_counter()
    try:
        model_response = await asyncio.to_thread(
            vision_llm_chain.call_vision_llm_chain,
            model_payload,
            provider_key="bbox_detection" if operation_is_bbox else "tagtool",
        )
    except LabelUException:
        raise
    except Exception as exc:
        raise LabelUException(
            code=ErrorCode.CODE_56003_AUTO_LABEL_MODEL_ERROR,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"AI 模型服务请求失败：{str(exc)}",
        ) from exc
    latency_ms = int((time.perf_counter() - start) * 1000)

    if not isinstance(model_response, dict):
        raise LabelUException(
            code=ErrorCode.CODE_56005_AUTO_LABEL_INVALID_RESPONSE,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message="AI 模型返回格式错误",
        )
    try:
        normalized = auto_label._normalize_results(
            model_data=model_response,
            sample_name=image_name or "trial-image",
            task_config=_task_config_from_spec(spec, operation),
            include_geometry_tools=operation_is_bbox,
            overwrite_existing_annotations=False,
            filter_by_labels=True,
            image_size=(int(image_width), int(image_height)),
            contract_prompt=binding,
        )
    except LabelUException:
        raise
    except Exception as exc:
        raise LabelUException(
            code=ErrorCode.CODE_56005_AUTO_LABEL_INVALID_RESPONSE,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message=f"AI 模型返回了无法解析的标注结果：{str(exc)}",
        ) from exc
    warnings = list(generated_warnings)
    if model_response.get("warning_message"):
        warnings.append(str(model_response["warning_message"]))
    return AnnotationPromptTrialResponse(
        status="COMPLETED",
        dataset_id=int(dataset_id),
        contract_id=int(contract.id),
        contract_revision=int(contract.revision),
        prompt_id=int(row.id) if row is not None else None,
        operation=operation,
        provider_key=provider_key,
        model_name=model_response.get("model") or model_name,
        profile_key=profile_key,
        profile_version=profile_version,
        prompt_sha256=prompt_sha256,
        effective_prompt=effective_prompt,
        rendered_prompt=rendered_prompt,
        image_width=int(image_width),
        image_height=int(image_height),
        model_image_width=model_width,
        model_image_height=model_height,
        latency_ms=latency_ms,
        model_response=model_response,
        normalized_result=normalized,
        warnings=warnings,
        binding=binding,
    )
