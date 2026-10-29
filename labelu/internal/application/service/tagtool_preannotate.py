from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import UploadFile, status
from loguru import logger
from PIL import Image
import httpx
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_pre_annotation,
    crud_sample,
    crud_tagtool_classification_model,
    crud_tagtool_preannotate_job,
    crud_task,
)
from labelu.internal.application.command.tagtool_preannotate import (
    BatchTagtoolPreannotateCommand,
)
from labelu.internal.application.response.tagtool_preannotate import (
    TagtoolClassificationModelResponse,
    TagtoolPreannotateCandidateResponse,
    TagtoolPreannotateJobResponse,
    TagtoolPreannotateModelsResponse,
)
from labelu.internal.application.service import auto_label as auto_label_mod
from labelu.internal.application.service import vision_llm_chain
from labelu.internal.application.service.provider_job import bridge_legacy_preannotation
from labelu.internal.application.service.task_background_jobs import (
    assert_no_active_task_background_job,
)
from labelu.internal.application.service.yolo_train import _assert_task_access
from labelu.internal.common.ai_labeling_runtime import is_qwen_visual_provider
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.model_access_config import (
    has_configured_model_integration,
    request_headers,
    resolve_model_integration,
)
from labelu.internal.common.tag_tool_frontend_payload import (
    expand_tag_tool_result_items,
)
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.auto_label_job import AutoLabelStatus
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.user import User

ATTRIBUTE_NAMES = ["sceneType", "lighting", "weather", "quality", "angle", "source"]
TAGTOOL_PRE_META = "tagtool_cls_generated"
_LOG = "[tagtool-preannotate]"
_MODEL_CACHE: dict[tuple[str, str], tuple[Any, dict[str, list[str]]]] = {}


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _ensure_torchvision_available() -> None:
    try:
        import torch  # noqa: F401
        import torchvision  # noqa: F401
    except ImportError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_57020_TAGTOOL_CLASSIFICATION_DEPS_MISSING,
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            message=f"TagTool 分类预标注需要 torch 和 torchvision: {exc}",
        )


def validate_label_map(label_map: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(label_map, dict):
        return ["invalid:label_map"]
    for name in ATTRIBUTE_NAMES:
        values = label_map.get(name)
        if values is None:
            errors.append(f"missing:{name}")
        elif not isinstance(values, list) or not values:
            errors.append(f"empty:{name}")
        elif any(v is None or not str(v).strip() for v in values):
            errors.append(f"invalid:{name}")
    return errors


def _parse_label_map(raw: str | dict | None) -> dict[str, list[str]]:
    payload = json.loads(raw) if isinstance(raw, str) else raw
    errors = validate_label_map(payload)
    if errors:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="TagTool 分类模型 label_map 无效: " + ", ".join(errors),
        )
    return {name: [str(v) for v in payload[name]] for name in ATTRIBUTE_NAMES}


def _weights_upload_suffix_ok(filename: str) -> bool:
    return Path(filename.lower()).suffix in {".pt", ".pth"}


def _safe_model_path(stored: str | None) -> Path:
    if not stored:
        raise LabelUException(
            code=ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    p = Path(stored)
    if not p.is_absolute():
        p = Path(settings.MEDIA_ROOT) / p
    resolved = p.resolve()
    root = Path(settings.MEDIA_ROOT).resolve()
    if not _path_is_relative_to(resolved, root):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if not resolved.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return resolved


def _model_to_response(row) -> TagtoolClassificationModelResponse:
    try:
        label_map = _parse_label_map(row.label_map_json)
        heads = list(label_map.keys())
    except Exception:
        heads = []
    return TagtoolClassificationModelResponse(
        id=row.id,
        display_name=row.display_name or "",
        category=getattr(row, "category", None) or "",
        original_filename=row.original_filename or "",
        file_size=int(row.file_size) if row.file_size is not None else None,
        label_heads=heads,
        created_at=row.created_at,
    )


def _validated_remote_tagtool_integration(
    integration: dict[str, Any],
) -> dict[str, Any]:
    """Recheck saved/queued connections at the actual model request boundary."""
    if vision_llm_chain.has_managed_volcengine_config():
        integration = vision_llm_chain._configured_model_for("tagtool") or {}
    if integration.get("blocked"):
        raise ValueError("页面火山模型已禁用或未通过连通性检查，请到系统管理检查")
    if is_qwen_visual_provider(
        str(integration.get("endpoint") or ""),
        str(integration.get("model") or ""),
        str(integration.get("name") or ""),
    ):
        raise ValueError("千问已从视觉识别链移除，请在系统管理配置火山 Coding Plan")
    endpoint = vision_llm_chain._safe_visual_base_url(
        str(integration.get("endpoint") or "").strip(),
        str(integration.get("model") or "").strip(),
        str(integration.get("provider_key") or integration.get("name") or ""),
    )
    if not endpoint:
        raise ValueError("全局标签远程模型没有配置服务地址")
    result = {**integration, "endpoint": endpoint}
    if endpoint == "https://ark.cn-beijing.volces.com/api/coding/v3":
        result["request_protocol"] = "openai_compatible"
    return result


def _remote_tagtool_runtime() -> dict[str, Any] | None:
    """Return the checked remote global-tag model configuration.

    Remote TagTool follows a small, provider-neutral contract: POST a multipart
    image under ``file`` and include ``model`` and ``labels`` fields.  The
    response may use ``predictions``, ``result`` or ``data`` as its object
    container; :func:`_parse_remote_predictions` normalizes these forms.
    """
    integration = (
        vision_llm_chain._configured_model_for("tagtool")
        if vision_llm_chain.has_managed_volcengine_config()
        else resolve_model_integration("tagtool")
    )
    if integration:
        try:
            return _validated_remote_tagtool_integration(integration)
        except ValueError as exc:
            raise LabelUException(
                code=ErrorCode.CODE_56004_AUTO_LABEL_NOT_CONFIGURED,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=str(exc),
            ) from exc
    if has_configured_model_integration("tagtool"):
        raise LabelUException(
            code=ErrorCode.CODE_56004_AUTO_LABEL_NOT_CONFIGURED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="全局标签远程模型已配置，但尚未通过连通性检查，请到系统管理→模型接入配置执行检查",
        )
    return None


def _auth_headers(integration: dict[str, Any]) -> dict[str, str]:
    return request_headers(integration)


def _prediction_value(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        label = value.get("label", value.get("value", value.get("name")))
        if label is None:
            return None
        result = {"label": str(label)}
        confidence = value.get(
            "confidence", value.get("score", value.get("probability"))
        )
        if confidence is not None:
            try:
                result["confidence"] = float(confidence)
            except (TypeError, ValueError):
                pass
        return result
    if value is None:
        return None
    return {"label": str(value)}


def _parse_remote_predictions(
    payload: Any,
    *,
    allowed_labels: dict[str, set[str]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Normalize common remote classification response shapes.

    Accepted examples::

        {"predictions": {"weather": {"label": "sunny", "confidence": .9}}}
        {"result": {"sceneType": "street"}}
        {"data": {"attributes": {"quality": {"value": "clear"}}}}
    """
    if not isinstance(payload, dict):
        raise ValueError("远程全局标签模型返回格式必须是 JSON 对象")
    container: Any = payload.get("predictions")
    if container is None:
        container = payload.get("result")
    if container is None:
        container = payload.get("data")
    if isinstance(container, dict) and isinstance(container.get("attributes"), dict):
        container = container["attributes"]
    if not isinstance(container, dict):
        container = payload
    out: dict[str, dict[str, Any]] = {}
    names = list(allowed_labels or {}) or list(ATTRIBUTE_NAMES)
    for name in names:
        pred = _prediction_value(container.get(name))
        if pred:
            out[name] = pred
    # Some services return a list of {name/field_key, label/value, confidence}.
    items = payload.get("results") or payload.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("field_key", item.get("name", item.get("key")))
            if name in names:
                pred = _prediction_value(item)
                if pred:
                    out[str(name)] = pred
    if not out:
        raise ValueError("远程全局标签模型未返回任务需要的标签维度")
    return out


def _chat_url(base: str) -> str:
    raw = str(base or "").rstrip("/")
    if raw.endswith("/chat/completions"):
        return raw
    return f"{raw}/chat/completions"


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
        )
    return str(content or "")


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fenced:
        try:
            value = json.loads(fenced.group(1).strip())
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        value = json.loads(raw[start : end + 1])
        if isinstance(value, dict):
            return value
    raise ValueError("全局标签模型返回内容不是有效 JSON")


def _predict_remote_openai_image(
    integration: dict[str, Any],
    *,
    image_path: Path,
    allowed_labels: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    """Call an OpenAI-compatible multimodal service for global tags.

    The configured 18081 service exposes ``/v1/chat/completions`` rather than
    the historical multipart TagTool contract.  Keep both protocols available
    so older deployments continue working while the service can be selected in
    the admin page without code or environment changes.
    """
    integration = _validated_remote_tagtool_integration(integration)
    endpoint = _chat_url(str(integration.get("endpoint") or "").strip())
    model = str(integration.get("model") or "").strip()
    image_b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    choices = {key: sorted(values) for key, values in allowed_labels.items() if values}
    prompt = (
        "请根据图片为每个全局标签维度选择一个值。只输出 JSON 对象，键必须是给定维度，"
        "值可以是字符串或 {label, confidence}。不得输出说明、Markdown 或新增选项。\n"
        "允许的维度和值："
        + json.dumps(choices, ensure_ascii=False, separators=(",", ":"))
    )
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": max(
            1, min(int(integration.get("max_new_tokens") or 1024), 32768)
        ),
        "temperature": 0.1,
    }
    headers = {"Content-Type": "application/json"}
    headers.update(_auth_headers(integration))
    timeout = max(1, min(int(integration.get("timeout_seconds") or 300), 3600))
    with httpx.Client(
        timeout=float(timeout),
        verify=bool(integration.get("verify_tls", False)),
        follow_redirects=False,
    ) as client:
        response = client.post(endpoint, json=body, headers=headers)
    if response.status_code < 200 or response.status_code >= 300:
        detail = response.text[:500].strip()
        raise ValueError(
            f"全局标签模型服务返回 HTTP {response.status_code}{(': ' + detail) if detail else ''}"
        )
    try:
        payload = response.json()
        content = _message_content_text(
            ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
        )
        parsed = _extract_json_object(content)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("全局标签模型服务返回的不是可解析 JSON") from exc
    return _parse_remote_predictions(parsed, allowed_labels=allowed_labels)


def _checkpoint_label_map(path: Path) -> dict[str, list[str]]:
    _ensure_torchvision_available()
    import torch

    checkpoint = torch.load(path, map_location="cpu")
    if not isinstance(checkpoint, dict):
        raise ValueError("checkpoint is not a dict")
    return _parse_label_map(checkpoint.get("label_map"))


async def create_imported_model(
    db: Session,
    current_user: User,
    *,
    file: UploadFile,
    display_name: Optional[str],
    category: Optional[str],
    label_map_json: Optional[str],
) -> TagtoolClassificationModelResponse:
    orig_name = (file.filename or "tagtool.pt").strip() or "tagtool.pt"
    if not _weights_upload_suffix_ok(orig_name):
        raise LabelUException(
            code=ErrorCode.CODE_57012_YOLO_IMPORT_INVALID_FILE,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="仅支持上传扩展名为 .pt 或 .pth 的 TagTool 分类权重文件",
        )

    subdir = Path("tagtool_cls") / "imported" / f"user-{current_user.id}"
    dest_dir = (Path(settings.MEDIA_ROOT) / subdir).resolve()
    root = Path(settings.MEDIA_ROOT).resolve()
    if not _path_is_relative_to(dest_dir, root):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    dest_dir.mkdir(parents=True, exist_ok=True)

    full_path = dest_dir / f"{uuid.uuid4().hex}.pt"
    max_sz = int(settings.UPLOAD_FILE_MAX_SIZE)
    total = 0
    try:
        with full_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_sz:
                    raise LabelUException(
                        code=ErrorCode.CODE_57012_YOLO_IMPORT_INVALID_FILE,
                        status_code=status.HTTP_400_BAD_REQUEST,
                        message=f"文件过大，单文件上限为 {max_sz} 字节",
                    )
                out.write(chunk)
        checkpoint_map = _checkpoint_label_map(full_path)
        if label_map_json and label_map_json.strip():
            supplied = _parse_label_map(json.loads(label_map_json))
            if supplied != checkpoint_map:
                raise LabelUException(
                    code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    message="上传的 label_map.json 与 checkpoint 内 label_map 不一致",
                )
        label_map = checkpoint_map
    except LabelUException:
        if full_path.is_file():
            full_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if full_path.is_file():
            full_path.unlink(missing_ok=True)
        logger.opt(exception=exc).error("save imported tagtool model failed")
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"TagTool 分类权重无法读取或缺少 label_map: {exc}",
        )

    rel = full_path.relative_to(root).as_posix()
    dn = (display_name or "").strip() or Path(orig_name).stem
    cat = (category or "").strip()[:128]
    with begin_transaction(db):
        row = crud_tagtool_classification_model.create(
            db,
            user_id=current_user.id,
            display_name=dn[:128],
            category=cat,
            original_filename=orig_name[:256],
            weights_rel_path=rel[:512],
            label_map_json=json.dumps(label_map, ensure_ascii=False),
            file_size=total,
        )
    return _model_to_response(row)


def list_imported_models(
    db: Session,
    current_user: User,
    *,
    name: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[TagtoolClassificationModelResponse], int]:
    rows, total = crud_tagtool_classification_model.list_for_user(
        db,
        current_user.id,
        name=name,
        category=category,
        search=search,
        page=max(int(page), 0),
        size=min(max(int(size), 1), 100),
        list_all_users_models=True,
    )
    return [_model_to_response(r) for r in rows], total


def _get_model_row_for_user(db: Session, current_user: User, model_id: int):
    row = crud_tagtool_classification_model.get_by_id(db, model_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_57013_YOLO_IMPORT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="未找到该 TagTool 分类模型或无权访问",
        )
    return row


def get_model_download(
    db: Session, current_user: User, model_id: int
) -> tuple[Path, str]:
    row = _get_model_row_for_user(db, current_user, model_id)
    path = _safe_model_path(row.weights_rel_path)
    fn = (row.original_filename or f"tagtool-model-{model_id}.pt").strip()
    return path, fn or f"tagtool-model-{model_id}.pt"


def delete_imported_model(db: Session, current_user: User, model_id: int) -> None:
    row = _get_model_row_for_user(db, current_user, model_id)
    if row.user_id != current_user.id and not is_task_superuser(current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="共享 TagTool 分类模型仅上传者或任务超级用户可删除",
        )
    stored = row.weights_rel_path or ""
    path = Path(stored)
    if not path.is_absolute():
        path = Path(settings.MEDIA_ROOT) / path
    resolved = path.resolve()
    root = Path(settings.MEDIA_ROOT).resolve()
    safe_file = _path_is_relative_to(resolved, root) and resolved.is_file()
    with begin_transaction(db):
        crud_tagtool_classification_model.delete_row(db, row)
    if safe_file:
        try:
            resolved.unlink()
        except OSError:
            logger.warning("failed to delete tagtool model file {}", resolved)


def list_models_for_task(
    db: Session,
    task_id: int,
    current_user: User,
    *,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
) -> TagtoolPreannotateModelsResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    page = max(int(page), 0)
    size = min(max(int(size), 1), 100)
    normalized_search = str(search or "").strip()
    rows, _ = crud_tagtool_classification_model.list_for_user(
        db,
        current_user.id,
        search=normalized_search or None,
        page=0,
        # Compatibility filtering is intentionally performed after the query;
        # paginate the combined imported/remote candidate list exactly once.
        size=5000,
        list_all_users_models=True,
    )
    items: list[TagtoolPreannotateCandidateResponse] = []
    for row in rows:
        label_map = _parse_label_map(row.label_map_json)
        items.append(
            TagtoolPreannotateCandidateResponse(
                kind="imported",
                id=row.id,
                title=(row.display_name or "").strip() or f"TagTool 分类模型 #{row.id}",
                detail=row.original_filename or "",
                category=(row.category or "").strip() or "未分类",
                label_heads=list(label_map.keys()),
            )
        )
    # The remote service is a selectable model source alongside uploaded
    # checkpoints.  Only a checked, enabled configuration is offered so a
    # stale endpoint cannot be selected and fail later in a worker.
    try:
        integration = _remote_tagtool_runtime()
    except LabelUException:
        integration = None
    if integration and _tagtool_candidate_matches_search(
        {
            "id": 0,
            "title": integration.get("name") or "远程全局标签模型",
            "detail": integration.get("endpoint") or "",
            "category": "服务器模型",
            "provider_key": "tagtool",
        },
        normalized_search,
    ):
        items.insert(
            0,
            TagtoolPreannotateCandidateResponse(
                kind="remote",
                id=0,
                provider_key="tagtool",
                title=(integration.get("name") or "远程全局标签模型"),
                detail=(integration.get("endpoint") or "")
                + (
                    " · " + str(integration.get("model"))
                    if integration.get("model")
                    else ""
                ),
                category="服务器模型",
                label_heads=list(
                    _configured_tag_option_values(
                        auto_label_mod.resolve_ai_task_config(db, task)
                    ).keys()
                ),
            ),
        )
    total = len(items)
    start = page * size
    page_items = items[start : start + size]
    return TagtoolPreannotateModelsResponse(
        items=page_items,
        total=total,
        page=page,
        size=len(page_items),
    )


def _tagtool_candidate_matches_search(candidate: Any, search: str | None) -> bool:
    """Match task-level TagTool candidates with the shared literal search rule."""
    term = str(search or "").strip().casefold()
    if not term:
        return True
    values = [
        candidate.get("id") if isinstance(candidate, dict) else getattr(candidate, "id", None),
        candidate.get("title") if isinstance(candidate, dict) else getattr(candidate, "title", None),
        candidate.get("detail") if isinstance(candidate, dict) else getattr(candidate, "detail", None),
        candidate.get("category") if isinstance(candidate, dict) else getattr(candidate, "category", None),
        candidate.get("provider_key") if isinstance(candidate, dict) else getattr(candidate, "provider_key", None),
    ]
    if isinstance(candidate, dict):
        values.extend(candidate.get("label_heads") or [])
    else:
        values.extend(getattr(candidate, "label_heads", []) or [])
    return any(term in str(value).casefold() for value in values if value is not None)


def _configured_tag_option_values(
    task_cfg_merged: dict[str, Any],
) -> dict[str, set[str]]:
    _, _, tag_dims = auto_label_mod._extract_tool_configs(task_cfg_merged)
    out: dict[str, set[str]] = {}
    for dim in tag_dims:
        fk = dim.get("field_key")
        if not fk:
            continue
        vals: set[str] = set()
        opts = dim.get("options") or []
        if isinstance(opts, list):
            for opt in opts:
                if not isinstance(opt, dict):
                    continue
                val = opt.get("value")
                if val is not None and str(val).strip():
                    vals.add(str(val))
        if vals:
            out[str(fk)] = vals
    return out


def resolve_auto_candidate_for_task(
    *,
    db: Session,
    task,
    current_user: User,
    task_cfg_merged: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve an imported classifier that can complete every task tag dimension."""
    task_config = task_cfg_merged or auto_label_mod.resolve_ai_task_config(db, task)
    allowed = _configured_tag_option_values(task_config)
    if not allowed:
        return None
    rows, _ = crud_tagtool_classification_model.list_for_user(
        db,
        current_user.id,
        page=0,
        size=500,
        list_all_users_models=True,
    )
    for row in rows:
        try:
            label_map = _parse_label_map(row.label_map_json)
            # Every possible prediction for a required head must be valid in
            # the task contract.  Otherwise one automatic pass could silently
            # produce incomplete global tags that need manual repair.
            if any(
                field not in label_map or not set(label_map[field]).issubset(options)
                for field, options in allowed.items()
            ):
                continue
            weights_path = _safe_model_path(row.weights_rel_path)
            return {
                "model_kind": "imported",
                "model_id": int(row.id),
                "weights_path": weights_path,
                "label_map": label_map,
                "device": "",
            }
        except Exception as exc:
            logger.opt(exception=exc).warning(
                "{} auto_candidate_skip_imported task_id={} model_id={}",
                _LOG,
                getattr(task, "id", None),
                getattr(row, "id", None),
            )
    return None


def build_tagtool_preannotation_payload(
    *,
    sample_name: str,
    task_cfg_merged: dict[str, Any],
    predictions: dict[str, dict[str, Any]],
    model_kind: str,
    model_id: int,
    weights_name: str,
) -> dict[str, Any]:
    _, config_by_tool, _ = auto_label_mod._extract_tool_configs(task_cfg_merged)
    pre_annotation_config = {
        tool_name: tool_cfg.get("attributes", [])
        for tool_name, tool_cfg in config_by_tool.items()
    }
    allowed = _configured_tag_option_values(task_cfg_merged)
    value_map: dict[str, list[str]] = {}
    confidences: dict[str, str] = {}
    for attr in allowed:
        pred = predictions.get(attr) or {}
        label = pred.get("label")
        if attr not in allowed or label is None:
            continue
        sval = str(label)
        if sval not in allowed[attr]:
            continue
        value_map[attr] = [sval]
        if pred.get("confidence") is not None:
            confidences[attr] = str(round(float(pred["confidence"]), 6))
    grouped: dict[str, Any] = {}
    if value_map:
        item = {
            "id": str(uuid.uuid4()),
            "order": 0,
            "label": "",
            "visible": True,
            "type": "tag",
            "valid": True,
            "isVisible": True,
            "value": value_map,
        }
        grouped["tagTool"] = {
            "toolName": "tagTool",
            "result": expand_tag_tool_result_items([item]),
        }
    else:
        raise ValueError("no tagTool predictions match task config")
    return {
        "sample_name": sample_name,
        "annotations": grouped,
        "config": pre_annotation_config,
        "meta": {
            "source_type": TAGTOOL_PRE_META,
            "model_kind": model_kind,
            "model_id": model_id,
            "weights_file": weights_name,
            "confidences": confidences,
        },
    }


def _explicit_target_sample_ids(
    cmd: BatchTagtoolPreannotateCommand,
) -> list[int] | None:
    combined: list[int] = []
    if cmd.sample_ids:
        combined.extend(int(x) for x in cmd.sample_ids)
    if cmd.sample_id is not None:
        combined.append(int(cmd.sample_id))
    if not combined:
        return None
    seen: set[int] = set()
    out: list[int] = []
    for sid in combined:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def filter_explicit_sample_targets(cmd, rows: list) -> list:
    ids = _explicit_target_sample_ids(cmd)
    if not ids:
        return []
    by_id = {int(r.id): r for r in rows}
    out = []
    for sid in ids:
        row = by_id.get(int(sid))
        if row is not None and row.state != SampleState.SKIPPED.value:
            out.append(row)
    return out


def is_tagtool_preannotation_data(raw: Any) -> bool:
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return (parsed.get("meta") or {}).get("source_type") == TAGTOOL_PRE_META
    except Exception:
        return False


def _row_is_tagtool_preannotation(row: TaskPreAnnotation) -> bool:
    return is_tagtool_preannotation_data(row.data)


def _sample_has_tagtool_preannotation(
    db: Session, task_id: int, sample_name: str
) -> bool:
    rows, _ = crud_pre_annotation.list_by(
        db=db, task_id=task_id, sample_name=sample_name, page=0, size=100
    )
    return any(_row_is_tagtool_preannotation(r) for r in rows)


def _delete_tagtool_preannotations_for_sample(
    db: Session, task_id: int, sample_name: str
) -> int:
    rows, _ = crud_pre_annotation.list_by(
        db=db, task_id=task_id, sample_name=sample_name, page=0, size=500
    )
    ids = [r.id for r in rows if _row_is_tagtool_preannotation(r)]
    if ids:
        crud_pre_annotation.delete(db=db, pre_annotation_ids=ids)
    return len(ids)


def _job_to_response(job) -> TagtoolPreannotateJobResponse:
    return TagtoolPreannotateJobResponse(
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


def list_jobs_for_task(
    db: Session,
    task_id: int,
    current_user: User,
    *,
    search: str | None = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[TagtoolPreannotateJobResponse], int]:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    rows, total = crud_tagtool_preannotate_job.list_by_task_id(
        db,
        task_id,
        search=search,
        page=max(int(page), 0),
        size=min(max(int(size), 1), 100),
    )
    return [_job_to_response(r) for r in rows], total


async def create_batch_job(
    db: Session,
    task_id: int,
    cmd: BatchTagtoolPreannotateCommand,
    current_user: User,
) -> TagtoolPreannotateJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    if task.media_type != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_56001_AUTO_LABEL_UNSUPPORTED_MEDIA,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="TagTool 分类预标注仅支持图片任务",
        )
    if task.status == TaskStatus.DRAFT.value:
        raise LabelUException(
            code=ErrorCode.CODE_57001_YOLO_TRAIN_TASK_NOT_FINISHED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="任务仍为草稿，请先完成配置并导入样本后再预标注",
        )
    task_cfg = auto_label_mod.resolve_ai_task_config(db, task)
    if not _configured_tag_option_values(task_cfg):
        raise LabelUException(
            code=ErrorCode.CODE_56002_AUTO_LABEL_NO_LABELS_CONFIGURED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="任务未配置 tagTool 全局枚举维度，无法写入全局标签预标注",
        )
    integration = None
    row = None
    if cmd.model_kind == "remote":
        integration = _remote_tagtool_runtime()
        if integration is None:
            raise LabelUException(
                code=ErrorCode.CODE_56004_AUTO_LABEL_NOT_CONFIGURED,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="未配置已检查通过的全局标签远程模型，请到系统管理→模型接入配置新增并检查",
            )
    else:
        row = _get_model_row_for_user(db, current_user, cmd.model_id)
        _parse_label_map(row.label_map_json)
        _ensure_torchvision_available()

    explicit_ids = _explicit_target_sample_ids(cmd)
    if explicit_ids:
        rows = crud_sample.get_by_ids(db=db, sample_ids=explicit_ids, task_id=task_id)
        by_id = {r.id: r for r in rows}
        missing = [sid for sid in explicit_ids if sid not in by_id]
        if missing:
            raise LabelUException(
                code=ErrorCode.CODE_57018_YOLO_PREANNOTATE_NO_SAMPLES,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"样本不存在、已删除或不属于该任务: {missing[:15]}",
            )
        targets = filter_explicit_sample_targets(cmd, rows)
    else:
        targets = crud_sample.list_batch_auto_label_targets(
            db=db, task_id=task_id, include_done_samples=cmd.include_done_samples
        )
    if not targets:
        raise LabelUException(
            code=ErrorCode.CODE_57018_YOLO_PREANNOTATE_NO_SAMPLES,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="当前没有符合 TagTool 分类预标注条件的样本",
        )

    sample_ids = [s.id for s in targets]
    with begin_transaction(db):
        assert_no_active_task_background_job(db=db, task_id=task_id)
        job = crud_tagtool_preannotate_job.create(
            db,
            task_id=task_id,
            user_id=current_user.id,
            sample_count=len(sample_ids),
            model_kind=cmd.model_kind,
            model_id=cmd.model_id,
            payload_json=json.dumps(
                {
                    "sample_ids": sample_ids,
                    "weights_rel_path": row.weights_rel_path if row else "",
                    "label_map_json": row.label_map_json if row else "",
                    "overwrite": bool(cmd.overwrite),
                    "model_kind": cmd.model_kind,
                    "model_id": int(cmd.model_id or 0),
                    "provider_key": cmd.provider_key
                    or ("tagtool" if cmd.model_kind == "remote" else ""),
                    "user_id": int(current_user.id),
                    "device": (cmd.device or "").strip(),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        job_id = job.id
        if not settings.PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED:
            crud_tagtool_preannotate_job.update_status(
                db, job, AutoLabelStatus.PROCESSING.value
            )
    if not settings.PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED:
        asyncio.get_event_loop().run_in_executor(
            None,
            _run_tagtool_preannotate_sync,
            job_id,
            task_id,
            sample_ids,
            row.weights_rel_path if row else "",
            row.label_map_json if row else "",
            cmd.overwrite,
            cmd.model_kind,
            cmd.model_id,
            current_user.id,
            (cmd.device or "").strip(),
            cmd.provider_key or ("tagtool" if cmd.model_kind == "remote" else ""),
        )
    return get_job(db=db, task_id=task_id, job_id=job_id, current_user=current_user)


def get_job(
    db: Session,
    task_id: int,
    job_id: int,
    current_user: User,
) -> TagtoolPreannotateJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    job = crud_tagtool_preannotate_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_57019_YOLO_PREANNOTATE_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="未找到该 TagTool 分类预标注作业",
        )
    return _job_to_response(job)


def get_active_job(
    db: Session,
    task_id: int,
    current_user: User,
) -> TagtoolPreannotateJobResponse | None:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _assert_task_access(task, current_user)
    job = crud_tagtool_preannotate_job.get_active_by_task_id(db=db, task_id=task_id)
    if not job:
        return None
    return _job_to_response(job)


def _load_model(weights_path: Path, label_map: dict[str, list[str]], device_hint: str):
    import torch
    from torch import nn
    from torchvision.models import resnet18

    class MultiHeadResNet18(nn.Module):
        def __init__(self, num_classes: dict[str, int]) -> None:
            super().__init__()
            backbone = resnet18(weights=None)
            in_features = backbone.fc.in_features
            backbone.fc = nn.Identity()
            self.backbone = backbone
            self.heads = nn.ModuleDict(
                {
                    name: nn.Linear(in_features, num_classes[name])
                    for name in ATTRIBUTE_NAMES
                }
            )

        def forward(self, x):
            features = self.backbone(x)
            return {name: head(features) for name, head in self.heads.items()}

    device = torch.device(
        device_hint if device_hint else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    key = (str(weights_path), str(device))
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached[0], device
    checkpoint = torch.load(weights_path, map_location=device)
    model = MultiHeadResNet18(
        {name: len(label_map[name]) for name in ATTRIBUTE_NAMES}
    ).to(device)
    model.load_state_dict(checkpoint["model"])
    model.eval()
    _MODEL_CACHE[key] = (model, label_map)
    return model, device


def _predict_image(
    weights_path: Path,
    label_map: dict[str, list[str]],
    image_path: Path,
    device_hint: str,
) -> dict[str, dict[str, Any]]:
    import torch
    from torchvision import transforms

    model, device = _load_model(weights_path, label_map, device_hint)
    transform = transforms.Compose(
        [
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ]
    )
    image = Image.open(image_path).convert("RGB")
    tensor = transform(image).unsqueeze(0).to(device)
    with torch.no_grad():
        outputs = model(tensor)
    result: dict[str, dict[str, Any]] = {}
    for name in ATTRIBUTE_NAMES:
        probs = torch.softmax(outputs[name][0], dim=0)
        index = int(probs.argmax().cpu())
        result[name] = {
            "label": label_map[name][index],
            "confidence": float(probs[index].cpu()),
        }
    return result


def _predict_remote_image(
    integration: dict[str, Any],
    *,
    image_path: Path,
    sample_name: str,
    task_id: int,
    allowed_labels: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    """Call a configured remote global-tag model for one image."""
    integration = _validated_remote_tagtool_integration(integration)
    endpoint = str(integration.get("endpoint") or "").strip()
    if not endpoint:
        raise ValueError("全局标签远程模型没有配置服务地址")
    labels = {key: sorted(values) for key, values in allowed_labels.items() if values}
    protocol = str(integration.get("request_protocol") or "auto").strip().lower()
    if protocol == "openai_compatible" or (
        protocol == "auto"
        and (
            endpoint.rstrip("/").endswith("/v1")
            or endpoint.rstrip("/").endswith("/chat/completions")
        )
    ):
        return _predict_remote_openai_image(
            integration,
            image_path=image_path,
            allowed_labels=allowed_labels,
        )
    image_bytes = image_path.read_bytes()
    data = {
        "model": str(integration.get("model") or "").strip(),
        "task_id": str(task_id),
        "labels": json.dumps(labels, ensure_ascii=False, separators=(",", ":")),
    }
    files = {
        "file": (
            sample_name or image_path.name,
            image_bytes,
            "application/octet-stream",
        )
    }
    timeout = max(1, min(int(integration.get("timeout_seconds") or 300), 3600))
    with httpx.Client(
        timeout=float(timeout),
        verify=bool(integration.get("verify_tls", False)),
        follow_redirects=False,
    ) as client:
        response = client.post(
            endpoint, data=data, files=files, headers=_auth_headers(integration)
        )
    if response.status_code < 200 or response.status_code >= 300:
        detail = response.text[:500].strip()
        raise ValueError(
            f"全局标签模型服务返回 HTTP {response.status_code}{(': ' + detail) if detail else ''}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("全局标签模型服务返回的不是 JSON") from exc
    return _parse_remote_predictions(payload, allowed_labels=allowed_labels)


def _local_image_path_for_attachment(attachment) -> Path:
    if getattr(attachment, "data_source_id", None):
        raise ValueError("TagTool 分类预标注暂不支持外部数据源图片本地推理")
    key = (attachment.path or "").lstrip("/")
    path = (Path(settings.MEDIA_ROOT) / key).resolve()
    root = Path(settings.MEDIA_ROOT).resolve()
    if not _path_is_relative_to(path, root) or not path.is_file():
        raise FileNotFoundError(path)
    return path


def _run_tagtool_preannotate_sync(
    job_id: int,
    task_id: int,
    sample_ids: list[int],
    weights_rel_path: str,
    label_map_json: str,
    overwrite: bool,
    model_kind: str,
    model_id: int,
    user_id: int,
    device_hint: str,
    provider_key: str = "",
    lease_owner: str | None = None,
    lease_fencing_token: int | None = None,
    lease_seconds: int | None = None,
) -> None:
    from labelu.internal.common.db import session_scope

    try:
        with session_scope() as db:
            job = crud_tagtool_preannotate_job.get(db=db, job_id=job_id)
            if not job:
                return
            task = crud_task.get(db=db, task_id=task_id)
            task_cfg_merged = auto_label_mod.resolve_ai_task_config(db, task)
            integration = None
            weights_path = None
            label_map = None
            if model_kind == "remote":
                integration = _remote_tagtool_runtime()
                if integration is None:
                    raise ValueError("全局标签远程模型未配置或未通过连通性检查")
            else:
                weights_path = _safe_model_path(weights_rel_path)
                label_map = _parse_label_map(label_map_json)

        for sample_id in sample_ids:
            if lease_owner is not None and lease_fencing_token is not None:
                with session_scope() as db:
                    with begin_transaction(db):
                        renewed = crud_tagtool_preannotate_job.renew_fenced(
                            db,
                            job_id=job_id,
                            lease_owner=lease_owner,
                            lease_fencing_token=lease_fencing_token,
                            lease_seconds=int(
                                lease_seconds
                                or settings.PROVIDER_PREANNOTATE_WORKER_LEASE_SECONDS
                            ),
                        )
                    current = crud_tagtool_preannotate_job.get(db=db, job_id=job_id)
                    if (
                        not renewed
                        or current is None
                        or current.status != AutoLabelStatus.PROCESSING.value
                        or current.lease_owner != lease_owner
                        or int(current.lease_fencing_token or 0)
                        != int(lease_fencing_token)
                    ):
                        return
            try:
                _process_one_sample(
                    job_id=job_id,
                    task_id=task_id,
                    task_cfg_merged=task_cfg_merged,
                    sample_id=sample_id,
                    weights_path=weights_path,
                    label_map=label_map,
                    overwrite=overwrite,
                    model_kind=model_kind,
                    model_id=model_id,
                    user_id=user_id,
                    device_hint=device_hint,
                    provider_key=provider_key
                    or ("tagtool" if model_kind == "remote" else ""),
                    integration=integration,
                    lease_owner=lease_owner,
                    lease_fencing_token=lease_fencing_token,
                )
                with session_scope() as db:
                    job = crud_tagtool_preannotate_job.get(db=db, job_id=job_id)
                    if job:
                        with begin_transaction(db):
                            if (
                                lease_owner is not None
                                and lease_fencing_token is not None
                            ):
                                crud_tagtool_preannotate_job.increment_progress_fenced(
                                    db,
                                    job_id=job_id,
                                    lease_owner=lease_owner,
                                    lease_fencing_token=lease_fencing_token,
                                    success=True,
                                )
                            else:
                                crud_tagtool_preannotate_job.increment_progress(
                                    db, job, success=True
                                )
            except Exception:
                logger.opt(exception=True).error(
                    "{} sample_fail job_id={} sample_id={}", _LOG, job_id, sample_id
                )
                with session_scope() as db:
                    job = crud_tagtool_preannotate_job.get(db=db, job_id=job_id)
                    if job:
                        with begin_transaction(db):
                            if (
                                lease_owner is not None
                                and lease_fencing_token is not None
                            ):
                                crud_tagtool_preannotate_job.increment_progress_fenced(
                                    db,
                                    job_id=job_id,
                                    lease_owner=lease_owner,
                                    lease_fencing_token=lease_fencing_token,
                                    success=False,
                                )
                            else:
                                crud_tagtool_preannotate_job.increment_progress(
                                    db, job, success=False
                                )
        with session_scope() as db:
            job = crud_tagtool_preannotate_job.get(db=db, job_id=job_id)
            if job:
                with begin_transaction(db):
                    if lease_owner is not None and lease_fencing_token is not None:
                        crud_tagtool_preannotate_job.finish_fenced(
                            db,
                            job_id=job_id,
                            lease_owner=lease_owner,
                            lease_fencing_token=lease_fencing_token,
                            status=AutoLabelStatus.COMPLETED.value,
                        )
                    else:
                        crud_tagtool_preannotate_job.update_status(
                            db, job, AutoLabelStatus.COMPLETED.value
                        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "{} job_fatal job_id={} task_id={}", _LOG, job_id, task_id
        )
        try:
            with session_scope() as db:
                job = crud_tagtool_preannotate_job.get(db=db, job_id=job_id)
                if job:
                    with begin_transaction(db):
                        if lease_owner is not None and lease_fencing_token is not None:
                            crud_tagtool_preannotate_job.finish_fenced(
                                db,
                                job_id=job_id,
                                lease_owner=lease_owner,
                                lease_fencing_token=lease_fencing_token,
                                status=AutoLabelStatus.FAILED.value,
                                error_message=str(exc),
                            )
                        else:
                            crud_tagtool_preannotate_job.update_status(
                                db,
                                job,
                                AutoLabelStatus.FAILED.value,
                                error_message=str(exc),
                            )
        except Exception:
            logger.error("failed to update tagtool preannotate job {}", job_id)


def _process_one_sample(
    *,
    job_id: int | None = None,
    task_id: int,
    task_cfg_merged: dict[str, Any],
    sample_id: int,
    weights_path: Path | None,
    label_map: dict[str, list[str]] | None,
    overwrite: bool,
    model_kind: str,
    model_id: int,
    user_id: int,
    device_hint: str,
    provider_key: str = "",
    integration: dict[str, Any] | None = None,
    lease_owner: str | None = None,
    lease_fencing_token: int | None = None,
) -> None:
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        sample = crud_sample.get(db=db, sample_id=sample_id)
        if not sample or sample.task_id != task_id or not sample.file:
            raise ValueError("sample missing or no attachment")
        sample_name = sample.file.filename
        if not overwrite and _sample_has_tagtool_preannotation(
            db, task_id, sample_name
        ):
            return
        image_path = _local_image_path_for_attachment(sample.file)

    if model_kind == "remote":
        runtime = integration or resolve_model_integration(provider_key or "tagtool")
        if not runtime:
            raise ValueError("全局标签远程模型未配置或未通过连通性检查")
        predictions = _predict_remote_image(
            runtime,
            image_path=image_path,
            sample_name=sample_name,
            task_id=task_id,
            allowed_labels=_configured_tag_option_values(task_cfg_merged),
        )
        weights_name = str(
            runtime.get("model") or runtime.get("endpoint") or "远程模型"
        )
        payload_model_id = 0
    else:
        if weights_path is None or label_map is None:
            raise ValueError("本地 TagTool 模型参数缺失")
        predictions = _predict_image(weights_path, label_map, image_path, device_hint)
        weights_name = weights_path.name
        payload_model_id = model_id
    payload = build_tagtool_preannotation_payload(
        sample_name=sample_name,
        task_cfg_merged=task_cfg_merged,
        predictions=predictions,
        model_kind=model_kind,
        model_id=payload_model_id,
        weights_name=weights_name,
    )
    with session_scope() as db:
        if lease_owner is not None and lease_fencing_token is not None:
            current = (
                crud_tagtool_preannotate_job.get(db=db, job_id=job_id)
                if job_id is not None
                else None
            )
            if (
                current is None
                or current.status != AutoLabelStatus.PROCESSING.value
                or current.lease_owner != lease_owner
                or int(current.lease_fencing_token or 0) != int(lease_fencing_token)
            ):
                raise RuntimeError("TagTool 预标注作业租约已失效，拒绝写入结果")
        with begin_transaction(db):
            if overwrite:
                _delete_tagtool_preannotations_for_sample(db, task_id, sample_name)
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
        # Keep the legacy canvas projection intact while translating the
        # immutable result into the 7GAI provider/review boundary.  This is a
        # best-effort bridge so disabled or partially migrated providers do
        # not make the established TagTool worker fail.
        try:
            bridge_legacy_preannotation(
                db,
                task_id=task_id,
                sample_id=sample_id,
                payload=payload,
                provider_key="tagtool_cls",
                created_by=user_id,
            )
        except Exception:
            logger.opt(exception=True).warning(
                "{} provider bridge failed task_id={} sample_id={}",
                _LOG,
                task_id,
                sample_id,
            )
