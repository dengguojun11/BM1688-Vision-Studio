from __future__ import annotations

import copy
import json
from typing import List, Tuple

from fastapi import status
from loguru import logger
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_annotation_taxonomy
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.application.command.annotation_taxonomy import (
    CreateGlobalTagDimensionCommand,
    CreateLabelPresetCommand,
    UpdateGlobalTagDimensionCommand,
    UpdateLabelPresetCommand,
)
from labelu.internal.application.response.annotation_taxonomy import (
    GlobalTagDimensionResponse,
    GlobalTagOptionResponse,
    LabelPresetItemResponse,
    LabelPresetResponse,
)
from labelu.internal.application.response.base import UserResp
from labelu.internal.application.response.task import TaskResponse
from labelu.internal.application.service import annotation_contract as contract_service
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.label_attributes import decode_label_attributes
from labelu.internal.common.plate_recognition_profile import (
    PLATE_DETECTION_PROFILE,
    PLATE_DETECTION_PRESET_SLUG,
    PLATE_RECOGNITION_PROFILE,
    dump_profile_config,
    normalize_plate_recognition_task_config,
    parse_profile_config,
    task_config_matches_profile,
)
from labelu.internal.common.task_access import user_has_task_access
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    GlobalTagDimension,
    LabelPreset,
    LabelPresetLabel,
)
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.task import MediaType
from labelu.internal.domain.models.task import TaskStatus
from labelu.internal.domain.models.user import User

_TASK_LABEL_TOOL_NAMES = (
    "rectTool",
    "polygonTool",
    "pointTool",
    "lineTool",
    "cuboidTool",
)


def _profile_config_for_response(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _preset_label_rows(db: Session, preset_id: int):
    return (
        db.query(LabelPresetLabel, AnnotationLabel)
        .join(AnnotationLabel, AnnotationLabel.id == LabelPresetLabel.label_id)
        .filter(
            LabelPresetLabel.preset_id == preset_id,
            AnnotationLabel.deleted_at.is_(None),
        )
        .order_by(LabelPresetLabel.sort_order, LabelPresetLabel.id)
        .all()
    )


def _preset_to_resp(db: Session, p: LabelPreset) -> LabelPresetResponse:
    normalized = _preset_label_rows(db, int(p.id))
    items = sorted(p.items, key=lambda x: (x.sort_order, x.id))
    if normalized:
        response_items = [
            LabelPresetItemResponse(
                id=relation.id,
                label_id=label.id,
                label_key=label.label_key,
                label_value=label.label_value,
                color=label.color,
                sort_order=relation.sort_order,
            )
            for relation, label in normalized
        ]
    else:
        by_value = {
            row.normalized_label_value: row
            for row in db.query(AnnotationLabel)
            .filter(
                AnnotationLabel.normalized_label_value.in_(
                    [str(item.label_value or "").strip().lower() for item in items]
                )
            )
            .all()
        }
        response_items = [
            LabelPresetItemResponse(
                id=i.id,
                label_id=(
                    by_value.get(str(i.label_value or "").strip().lower()).id
                    if by_value.get(str(i.label_value or "").strip().lower())
                    else None
                ),
                label_key=i.label_key,
                label_value=i.label_value,
                color=i.color,
                sort_order=i.sort_order,
            )
            for i in items
        ]
    return LabelPresetResponse(
        id=p.id,
        slug=p.slug,
        name=p.name,
        description=p.description,
        training_profile=p.training_profile,
        training_profile_config=_profile_config_for_response(p.training_profile_config),
        sort_order=p.sort_order,
        items=response_items,
        label_ids=[
            int(item.label_id) for item in response_items if item.label_id is not None
        ],
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _dimension_to_resp(d: GlobalTagDimension) -> GlobalTagDimensionResponse:
    opts = sorted(d.options, key=lambda x: (x.sort_order, x.id))
    return GlobalTagDimensionResponse(
        id=d.id,
        field_key=d.field_key,
        name_cn=d.name_cn,
        sort_order=d.sort_order,
        options=[
            GlobalTagOptionResponse(
                id=o.id,
                name_cn=o.name_cn,
                value_en=o.value_en,
                sort_order=o.sort_order,
            )
            for o in opts
        ],
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def _coerce_media_type(media_type) -> str | None:
    if media_type is None:
        return None
    if isinstance(media_type, str):
        return media_type
    val = getattr(media_type, "value", None)
    return str(val) if val is not None else str(media_type)


def _task_access_or_raise(db: Session, task_id: int, current_user: User):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not user_has_task_access(current_user, task):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return task


def _parse_config(raw: str | None) -> dict:
    if raw is None or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def _rect_tool_shell(attributes: list[dict]) -> dict:
    return {
        "isShowCursor": False,
        "showConfirm": False,
        "skipWhileNoDependencies": False,
        "drawOutsideTarget": False,
        "copyBackwardResult": False,
        "minWidth": 1,
        "minHeight": 1,
        "attributeConfigurable": True,
        "textConfigurable": True,
        "textCheckType": 4,
        "customFormat": "",
        "attributes": copy.deepcopy(attributes),
    }


def _rect_attribute_from_label(label: AnnotationLabel) -> dict:
    payload = {
        "labelId": int(label.id),
        "key": str(label.label_key),
        "value": str(label.label_value),
        "color": str(label.color),
    }
    nested = decode_label_attributes(label.attributes_json)
    if nested:
        payload["attributes"] = copy.deepcopy(nested)
    return payload


def _apply_canonical_label_details(cfg: dict, labels: list[AnnotationLabel]) -> None:
    by_value = {str(label.label_value): label for label in labels}
    for tool in cfg.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("tool") not in _TASK_LABEL_TOOL_NAMES:
            continue
        tool_config = tool.get("config") if isinstance(tool.get("config"), dict) else {}
        for item in tool_config.get("attributes") or []:
            if not isinstance(item, dict):
                continue
            label = by_value.get(str(item.get("value") or ""))
            if label is None:
                continue
            canonical = _rect_attribute_from_label(label)
            nested = canonical.pop("attributes", None)
            item.update(canonical)
            if nested:
                item["attributes"] = nested


def _task_label_tool_index(
    tools: list, preferred_tool_name: str | None = None
) -> tuple[int | None, str | None]:
    """Return the requested task-label tool, then fall back to legacy priority."""
    if preferred_tool_name in _TASK_LABEL_TOOL_NAMES:
        preferred_index = next(
            (
                i
                for i, tool in enumerate(tools)
                if isinstance(tool, dict) and tool.get("tool") == preferred_tool_name
            ),
            None,
        )
        if preferred_index is not None:
            return preferred_index, preferred_tool_name

    for tool_name in _TASK_LABEL_TOOL_NAMES:
        index = next(
            (
                i
                for i, tool in enumerate(tools)
                if isinstance(tool, dict) and tool.get("tool") == tool_name
            ),
            None,
        )
        if index is not None:
            return index, tool_name
    return None, None


def _task_contract_label_ids(tools: list) -> list[int]:
    label_ids: list[int] = []
    seen: set[int] = set()
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("tool") not in _TASK_LABEL_TOOL_NAMES:
            continue
        tool_config = tool.get("config") if isinstance(tool.get("config"), dict) else {}
        attributes = tool_config.get("attributes")
        if not isinstance(attributes, list):
            continue
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            try:
                label_id = int(attribute.get("labelId"))
            except (TypeError, ValueError):
                continue
            if label_id > 0 and label_id not in seen:
                seen.add(label_id)
                label_ids.append(label_id)
    return label_ids


def _merge_task_label_tool_config(existing: dict | None, attrs: list[dict]) -> dict:
    config = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    config["attributes"] = copy.deepcopy(attrs)
    return config


def _merge_rect_tool_config(existing: dict | None, attrs: list[dict]) -> dict:
    """与 LabelU-Kit rectTemplate 对齐的完整拉框 config，避免缺字段导致表单不同步。"""
    shell = _rect_tool_shell(attrs)
    if isinstance(existing, dict):
        mw = existing.get("minWidth")
        mh = existing.get("minHeight")
        if mw is not None:
            shell["minWidth"] = mw
        if mh is not None:
            shell["minHeight"] = mh
    return shell


def _enum_attr_from_dimension(dim: GlobalTagDimension) -> dict:
    opts = sorted(dim.options, key=lambda x: (x.sort_order, x.id))
    return {
        "key": dim.name_cn,
        "value": dim.field_key,
        "type": "enum",
        "required": False,
        "options": [{"key": o.name_cn, "value": o.value_en} for o in opts],
    }


def _tag_list_item_from_dimension(dim: GlobalTagDimension) -> dict:
    opts = sorted(dim.options, key=lambda x: (x.sort_order, x.id))
    return {
        "key": dim.name_cn,
        "value": dim.field_key,
        "isMulti": False,
        "subSelected": [
            {"key": o.name_cn, "value": o.value_en, "isDefault": False} for o in opts
        ],
    }


def merge_global_dimensions_into_cfg(cfg: dict, dims: list[GlobalTagDimension]) -> None:
    """将全局维度合并进 cfg（tagTool + tagList），与 apply_global_tags 逻辑一致。"""
    tools: list = cfg.setdefault("tools", [])
    blocks = [_enum_attr_from_dimension(d) for d in dims]

    tag_idx = next((i for i, t in enumerate(tools) if t.get("tool") == "tagTool"), None)
    if tag_idx is None:
        tools.append({"tool": "tagTool", "config": {"attributes": blocks}})
    else:
        tcfg = tools[tag_idx].setdefault("config", {})
        attrs: list = tcfg.setdefault("attributes", [])
        if not isinstance(attrs, list):
            attrs = []
            tcfg["attributes"] = attrs
        for block in blocks:
            vk = block.get("value")
            if not vk:
                continue
            idx = next(
                (
                    i
                    for i, a in enumerate(attrs)
                    if isinstance(a, dict) and a.get("value") == vk
                ),
                None,
            )
            if idx is None:
                attrs.append(copy.deepcopy(block))
                continue
            old = attrs[idx]
            old_opts = (
                old.get("options") if isinstance(old.get("options"), list) else []
            )
            if old.get("type") != "enum" or len(old_opts) == 0:
                attrs[idx] = copy.deepcopy(block)

    cfg.setdefault("commonAttributeConfigurable", True)

    tag_list = cfg.setdefault("tagList", [])
    if not isinstance(tag_list, list):
        tag_list = []
        cfg["tagList"] = tag_list
    for dim in dims:
        item = _tag_list_item_from_dimension(dim)
        vk = item.get("value")
        if not vk:
            continue
        idx = next(
            (
                i
                for i, t in enumerate(tag_list)
                if isinstance(t, dict) and t.get("value") == vk
            ),
            None,
        )
        if idx is None:
            tag_list.append(copy.deepcopy(item))
            continue
        old = tag_list[idx]
        old_sub = (
            old.get("subSelected") if isinstance(old.get("subSelected"), list) else []
        )
        if len(old_sub) == 0:
            tag_list[idx] = copy.deepcopy(item)


def build_rect_label_presets_json(
    db: Session,
    *,
    preset_rows: list[LabelPreset] | None = None,
) -> list[dict]:
    rows = preset_rows
    if rows is None:
        rows, _ = crud_annotation_taxonomy.label_preset_list(db, page=0, size=500)
    presets: list[dict] = []
    for p in sorted(rows, key=lambda x: (x.sort_order, x.id)):
        items = sorted(p.items, key=lambda x: (x.sort_order, x.id))
        presets.append(
            {
                "id": p.slug,
                "presetDbId": p.id,
                "name": p.name,
                "trainingProfile": p.training_profile,
                "trainingProfileConfig": _profile_config_for_response(
                    p.training_profile_config
                ),
                "attributes": [
                    {"key": i.label_key, "value": i.label_value, "color": i.color}
                    for i in items
                ],
            }
        )
    return presets


def enrich_task_config_json_for_client(
    db: Session,
    config: str | None,
    media_type,
    *,
    global_dimensions: list[GlobalTagDimension] | None = None,
    rect_label_presets: list[dict] | None = None,
) -> str | None:
    """
    仅供前端展示（GET 任务列表/详情），不写库。
    - 全局维度：合并进 tagTool / tagList（可直接用于标注）。
    - 拉框：仅注入 rectLabelPresets 供界面选择预设；任务没有任何几何工具时才补默认 rectTool。
    """
    if config is None:
        cfg: dict = {}
    elif not str(config).strip():
        return config
    else:
        try:
            parsed = json.loads(config)
            if not isinstance(parsed, dict):
                return config
            cfg = parsed
        except json.JSONDecodeError:
            return config

    try:
        dims = (
            global_dimensions
            if global_dimensions is not None
            else crud_annotation_taxonomy.global_dimensions_all_active(db)
        )
        merge_global_dimensions_into_cfg(cfg, dims)

        db_presets = (
            rect_label_presets
            if rect_label_presets is not None
            else build_rect_label_presets_json(db)
        )
        existing_raw = cfg.get("rectLabelPresets")
        extras: list = []
        db_slugs = {p["id"] for p in db_presets}
        if isinstance(existing_raw, list):
            for e in existing_raw:
                if isinstance(e, dict) and e.get("id") not in db_slugs:
                    extras.append(copy.deepcopy(e))
        cfg["rectLabelPresets"] = db_presets + extras

        mt = _coerce_media_type(media_type)
        if mt is None or str(mt).strip().upper() == MediaType.IMAGE.value:
            tools = cfg.setdefault("tools", [])
            label_tool_idx, label_tool_name = _task_label_tool_index(tools)
            if label_tool_idx is None:
                tools.append({"tool": "rectTool", "config": _rect_tool_shell([])})
            elif label_tool_name == "rectTool":
                rcfg = tools[label_tool_idx].setdefault("config", {})
                rattrs = rcfg.get("attributes")
                if not isinstance(rattrs, list):
                    rcfg["attributes"] = []
                rcfg.setdefault("attributeConfigurable", True)
    except Exception as e:
        logger.warning(
            "enrich_task_config_json_for_client failed, returning raw: {}", e
        )
        return config

    return json.dumps(cfg, ensure_ascii=False)


# ── CRUD：拉框预设 ──────────────────────────────────────────────────────


async def label_preset_list(
    db: Session, page: int, size: int, search: str | None = None
) -> Tuple[List[LabelPresetResponse], int]:
    rows, total = crud_annotation_taxonomy.label_preset_list(
        db, page=page, size=size, search=search
    )
    return [_preset_to_resp(db, r) for r in rows], total


async def label_preset_get(db: Session, preset_id: int) -> LabelPresetResponse:
    row = crud_annotation_taxonomy.label_preset_get(db, preset_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _preset_to_resp(db, row)


def _canonicalize_preset_items(db: Session, cmd_items, label_ids: list[int] | None):
    if label_ids:
        labels = contract_service.active_labels_by_ids(db, label_ids)
        return [
            {
                "label_id": row.id,
                "label_key": row.label_key,
                "label_value": row.label_value,
                "color": row.color,
                "sort_order": index,
            }
            for index, row in enumerate(labels)
        ]
    payload = _label_preset_items_payload(cmd_items)
    rows = []
    for index, item in enumerate(payload):
        normalized = item["label_value"].strip().lower()
        label = (
            db.query(AnnotationLabel)
            .filter(AnnotationLabel.normalized_label_value == normalized)
            .first()
        )
        if label is None:
            color_owner = (
                db.query(AnnotationLabel.id)
                .filter(func.lower(AnnotationLabel.color) == item["color"].lower())
                .first()
            )
            if color_owner:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=status.HTTP_409_CONFLICT,
                    message=f"color 已被其他标签使用: {item['color']}",
                )
            label = AnnotationLabel(
                label_key=item["label_key"],
                label_value=item["label_value"],
                normalized_label_value=normalized,
                annotation_kind="bbox",
                color=item["color"],
                status="active",
                version=1,
                sort_order=item.get("sort_order", index),
            )
            db.add(label)
            db.flush()
        rows.append({**item, "label_id": int(label.id)})
    return rows


def _replace_preset_relations(db: Session, preset_id: int, rows: list[dict]) -> None:
    db.query(LabelPresetLabel).filter(LabelPresetLabel.preset_id == preset_id).delete(
        synchronize_session=False
    )
    for index, item in enumerate(rows):
        db.add(
            LabelPresetLabel(
                preset_id=preset_id,
                label_id=int(item["label_id"]),
                sort_order=int(item.get("sort_order", index)),
            )
        )
    db.flush()


def _label_preset_items_payload(cmd_items) -> list[dict]:
    return [
        {
            "label_key": i.label_key,
            "label_value": i.label_value,
            "color": i.color,
            "sort_order": i.sort_order,
        }
        for i in cmd_items
    ]


def _duplicate_preset_message(existing: LabelPreset, requested_slug: str) -> str:
    hint = f"库中已有「{existing.slug}」(id={existing.id}"
    if existing.name:
        hint += f"，{existing.name}"
    hint += ")，请在列表中搜索并编辑，勿重复新建"
    if existing.slug.lower() != requested_slug.lower():
        hint += f"（与请求的 {requested_slug} 仅大小写不同）"
    return hint


async def label_preset_create(
    db: Session, cmd: CreateLabelPresetCommand
) -> LabelPresetResponse:
    normalized_items = _canonicalize_preset_items(db, cmd.items, cmd.label_ids)
    payload_items = [
        {key: row[key] for key in ("label_key", "label_value", "color", "sort_order")}
        for row in normalized_items
    ]
    slug = cmd.slug

    existing = crud_annotation_taxonomy.label_preset_find_by_slug_ci(
        db, slug, active_only=True
    )
    if existing:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=_duplicate_preset_message(existing, slug),
        )

    try:
        with begin_transaction(db):
            row = crud_annotation_taxonomy.label_preset_create(
                db,
                slug=slug,
                name=cmd.name,
                description=cmd.description,
                sort_order=cmd.sort_order,
                items=payload_items,
                training_profile=cmd.training_profile,
                training_profile_config=(
                    dump_profile_config(
                        parse_profile_config(
                            cmd.training_profile_config,
                            profile=cmd.training_profile,
                        )
                    )
                    if cmd.training_profile
                    else None
                ),
            )
            _replace_preset_relations(db, int(row.id), normalized_items)
    except IntegrityError:
        logger.warning("duplicate label preset slug (integrity): {}", slug)
        conflict = crud_annotation_taxonomy.label_preset_find_by_slug_ci(
            db, slug, active_only=True
        )
        if conflict:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=_duplicate_preset_message(conflict, slug),
            )
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"预设 slug「{slug}」已存在",
        )
    db.refresh(row)
    row = crud_annotation_taxonomy.label_preset_get(db, row.id)
    return _preset_to_resp(db, row)


async def label_preset_update(
    db: Session, preset_id: int, cmd: UpdateLabelPresetCommand
) -> LabelPresetResponse:
    row = crud_annotation_taxonomy.label_preset_get(db, preset_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    replace_items = None
    normalized_items = None
    if cmd.items is not None or cmd.label_ids is not None:
        normalized_items = _canonicalize_preset_items(
            db, cmd.items or [], cmd.label_ids
        )
        replace_items = [
            {
                key: item[key]
                for key in ("label_key", "label_value", "color", "sort_order")
            }
            for item in normalized_items
        ]
    update_training_profile = (
        "training_profile" in cmd.__fields_set__
        or "training_profile_config" in cmd.__fields_set__
    )
    effective_training_profile = (
        cmd.training_profile
        if "training_profile" in cmd.__fields_set__
        else row.training_profile
    )
    if not effective_training_profile:
        effective_profile_config = None
    elif "training_profile_config" in cmd.__fields_set__:
        effective_profile_config = dump_profile_config(
            parse_profile_config(
                cmd.training_profile_config,
                profile=effective_training_profile,
            )
        )
    else:
        effective_profile_config = row.training_profile_config
    with begin_transaction(db):
        crud_annotation_taxonomy.label_preset_update(
            db,
            row,
            name=cmd.name,
            description=cmd.description,
            sort_order=cmd.sort_order,
            replace_items=replace_items,
            training_profile=effective_training_profile,
            training_profile_config=effective_profile_config,
            update_training_profile=update_training_profile,
        )
        if normalized_items is not None:
            _replace_preset_relations(db, preset_id, normalized_items)
    row = crud_annotation_taxonomy.label_preset_get(db, preset_id)
    return _preset_to_resp(db, row)


async def label_preset_delete(db: Session, preset_id: int) -> None:
    row = crud_annotation_taxonomy.label_preset_get(db, preset_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    with begin_transaction(db):
        crud_annotation_taxonomy.label_preset_hard_delete(db, row)


# ── CRUD：全局维度 ───────────────────────────────────────────────────────


async def global_dimension_list(
    db: Session, page: int, size: int, search: str | None = None
) -> Tuple[List[GlobalTagDimensionResponse], int]:
    rows, total = crud_annotation_taxonomy.global_dimension_list(
        db, page=page, size=size, search=search
    )
    return [_dimension_to_resp(r) for r in rows], total


async def global_dimension_get(
    db: Session, dimension_id: int
) -> GlobalTagDimensionResponse:
    row = crud_annotation_taxonomy.global_dimension_get(db, dimension_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _dimension_to_resp(row)


async def global_dimension_create(
    db: Session, cmd: CreateGlobalTagDimensionCommand
) -> GlobalTagDimensionResponse:
    opts = [
        {
            "name_cn": o.name_cn,
            "value_en": o.value_en,
            "sort_order": o.sort_order,
        }
        for o in cmd.options
    ]
    try:
        with begin_transaction(db):
            row = crud_annotation_taxonomy.global_dimension_create(
                db,
                field_key=cmd.field_key.strip(),
                name_cn=cmd.name_cn.strip(),
                sort_order=cmd.sort_order,
                options=opts,
            )
    except IntegrityError:
        logger.warning("duplicate global dimension field_key: {}", cmd.field_key)
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    db.refresh(row)
    row = crud_annotation_taxonomy.global_dimension_get(db, row.id)
    return _dimension_to_resp(row)


async def global_dimension_update(
    db: Session, dimension_id: int, cmd: UpdateGlobalTagDimensionCommand
) -> GlobalTagDimensionResponse:
    row = crud_annotation_taxonomy.global_dimension_get(db, dimension_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    replace_options = None
    if cmd.options is not None:
        replace_options = [
            {
                "name_cn": o.name_cn,
                "value_en": o.value_en,
                "sort_order": o.sort_order,
            }
            for o in cmd.options
        ]
    with begin_transaction(db):
        crud_annotation_taxonomy.global_dimension_update(
            db,
            row,
            name_cn=cmd.name_cn,
            sort_order=cmd.sort_order,
            replace_options=replace_options,
        )
    row = crud_annotation_taxonomy.global_dimension_get(db, dimension_id)
    return _dimension_to_resp(row)


async def global_dimension_delete(db: Session, dimension_id: int) -> None:
    row = crud_annotation_taxonomy.global_dimension_get(db, dimension_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    with begin_transaction(db):
        crud_annotation_taxonomy.global_dimension_disable(db, row)


# ── 应用到任务配置 ────────────────────────────────────────────────────────


async def apply_label_preset_to_task(
    db: Session, task_id: int, preset_id: int, current_user: User
) -> TaskResponse:
    task = _task_access_or_raise(db, task_id, current_user)
    mt = _coerce_media_type(task.media_type)
    if mt is None or str(mt).strip().upper() != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    preset = crud_annotation_taxonomy.label_preset_get(db, preset_id)
    normalized_rows = _preset_label_rows(db, preset_id) if preset else []
    if not preset or (not normalized_rows and not preset.items):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Compatibility for presets created before label_preset_label existed. The
    # first real application upgrades the preset to canonical label relations.
    if not normalized_rows and preset.items:
        with begin_transaction(db):
            canonical_items = _canonicalize_preset_items(db, preset.items, None)
            _replace_preset_relations(db, preset_id, canonical_items)
        normalized_rows = _preset_label_rows(db, preset_id)

    cfg = _parse_config(task.config)
    preset_items = sorted(preset.items, key=lambda x: (x.sort_order, x.id))
    label_ids = [int(label.id) for _, label in normalized_rows]
    attrs = (
        [_rect_attribute_from_label(label) for _, label in normalized_rows]
        if normalized_rows
        else [
            {"key": i.label_key, "value": i.label_value, "color": i.color}
            for i in preset_items
        ]
    )

    if preset.training_profile in {PLATE_RECOGNITION_PROFILE, PLATE_DETECTION_PROFILE}:
        # 一期新任务只允许使用普通目标检测的矩形框。复合车牌预设
        # 依赖 polygonTool/四角点，历史任务仍可读取，但不能再应用到
        # 没有旧复合配置的新任务。
        existing_config = _parse_config(task.config)
        if (
            preset.training_profile == PLATE_RECOGNITION_PROFILE
            and (not task.config or not task_config_matches_profile(existing_config))
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message=(
                    "一期车牌训练只支持矩形框。请使用普通目标检测预设，"
                    "创建“车牌”或“数字位置”标签；历史复合车牌任务仅允许查看和续用。"
                ),
            )
        profile = parse_profile_config(
            preset.training_profile_config,
            profile=preset.training_profile,
        )
        rect_item = next(
            (
                item
                for item in preset_items
                if str(item.label_value or "").strip() == profile["rect_label"]
            ),
            preset_items[0],
        )
        cfg = normalize_plate_recognition_task_config(
            cfg,
            profile,
            label_key=rect_item.label_key,
            color=rect_item.color,
        )
        _apply_canonical_label_details(cfg, [label for _, label in normalized_rows])
        if preset.training_profile == PLATE_DETECTION_PROFILE:
            for tool in cfg.get("tools") or []:
                if not isinstance(tool, dict) or tool.get("tool") != "rectTool":
                    continue
                tool_config = tool.get("config")
                if not isinstance(tool_config, dict):
                    continue
                for item in tool_config.get("attributes") or []:
                    if isinstance(item, dict) and item.get("value") == profile["rect_label"]:
                        item["attributes"] = []
        if preset.training_profile == PLATE_DETECTION_PROFILE:
            cfg["defaultRectPresetId"] = PLATE_DETECTION_PRESET_SLUG
        else:
            cfg.pop("defaultRectPresetId", None)
    else:
        tools: list = cfg.setdefault("tools", [])
        rect_idx = next(
            (i for i, t in enumerate(tools) if t.get("tool") == "rectTool"), None
        )

        if rect_idx is None:
            tools.append(
                {"tool": "rectTool", "config": _merge_rect_tool_config(None, attrs)}
            )
        else:
            t = tools[rect_idx]
            prev = t.get("config") if isinstance(t.get("config"), dict) else {}
            t["config"] = _merge_rect_tool_config(prev, attrs)

        cfg.pop("defaultRectPresetId", None)

    config_str = json.dumps(cfg, ensure_ascii=False)
    with begin_transaction(db):
        if label_ids:
            contract_service.create_contract(
                db,
                task=task,
                label_ids=label_ids,
                created_by=current_user.id,
                source_preset_id=preset_id,
            )
        crud_task.update(
            db,
            task,
            {
                "config": config_str,
                "status": TaskStatus.CONFIGURED.value,
                "training_ready": None,
                "annotation_audit_json": None,
            },
        )

    from labelu.internal.application.service.auto_sample_preannotate import (
        schedule_auto_preannotate_for_task_samples,
    )

    schedule_auto_preannotate_for_task_samples(
        db=db,
        task_id=task_id,
        user_id=current_user.id,
        source_hint="label_preset_apply",
    )

    updated = crud_task.get(db=db, task_id=task_id)
    contract = contract_service.current_contract(db, task_id)
    return TaskResponse(
        id=updated.id,
        name=updated.name,
        description=updated.description,
        tips=updated.tips,
        media_type=updated.media_type,
        config=enrich_task_config_json_for_client(
            db,
            updated.config,
            updated.media_type,
        ),
        status=updated.status,
        created_at=updated.created_at,
        created_by=UserResp(
            id=updated.owner.id,
            username=updated.owner.username,
        ),
        current_annotation_contract_id=(contract.id if contract else None),
        annotation_contract_revision=(contract.revision if contract else None),
        label_set_signature=(contract.label_set_signature if contract else None),
        class_order_signature=(contract.class_order_signature if contract else None),
    )


async def apply_labels_to_task(
    db: Session,
    task_id: int,
    label_ids: list[int],
    current_user: User,
    expected_contract_revision: int | None = None,
    config: str | None = None,
    tool_name: str | None = None,
    scope_mode: str | None = None,
) -> TaskResponse:
    task = _task_access_or_raise(db, task_id, current_user)
    if str(_coerce_media_type(task.media_type) or "").upper() != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="只有图片任务支持几何工具标签契约",
        )
    existing = contract_service.current_contract(db, task_id)
    if expected_contract_revision is not None and (
        existing is None or int(existing.revision) != int(expected_contract_revision)
    ):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="任务标签契约已变化，请刷新后重试",
        )
    labels = contract_service.active_labels_by_ids(db, label_ids)
    dataset_contract = None
    if getattr(task, "dataset_label_contract_id", None):
        dataset_contract = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == int(task.dataset_label_contract_id))
            .one_or_none()
        )
    elif getattr(task, "dataset_id", None):
        dataset = (
            db.query(Dataset).filter(Dataset.id == int(task.dataset_id)).one_or_none()
        )
        if dataset is not None and getattr(dataset, "dataset_label_contract_id", None):
            dataset_contract = (
                db.query(DatasetLabelContract)
                .filter(
                    DatasetLabelContract.id == int(dataset.dataset_label_contract_id)
                )
                .one_or_none()
            )
            if dataset_contract is not None:
                task.dataset_label_contract_id = int(dataset_contract.id)
    resolved_scope = str(scope_mode or "").upper() or None
    if dataset_contract is not None:
        try:
            allowed_ids = {
                int(value)
                for value in json.loads(dataset_contract.label_ids_json or "[]")
            }
        except (TypeError, ValueError):
            allowed_ids = set()
        requested_ids = {int(value) for value in label_ids}
        if not requested_ids.issubset(allowed_ids):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="任务标签必须来自当前数据集标签契约",
            )
        if resolved_scope not in {"FULL", "SUBSET"}:
            resolved_scope = "FULL" if requested_ids == allowed_ids else "SUBSET"
        if resolved_scope == "FULL" and requested_ids != allowed_ids:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="全量任务必须使用数据集契约的全部标签；如只训练部分类别请选择标签子集",
            )
        task.task_label_scope_mode = resolved_scope
        task.dataset_label_contract_id = int(dataset_contract.id)
    else:
        resolved_scope = resolved_scope or "FULL"
    attrs = [_rect_attribute_from_label(row) for row in labels]
    cfg = _parse_config(config if config is not None else task.config)
    tools = cfg.setdefault("tools", [])
    requested_tool_name = str(tool_name or "").strip() or None
    if requested_tool_name and requested_tool_name not in _TASK_LABEL_TOOL_NAMES:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"不支持为该工具配置任务标签: {requested_tool_name}",
        )
    label_tool_idx, label_tool_name = _task_label_tool_index(tools, requested_tool_name)
    if requested_tool_name and label_tool_name != requested_tool_name:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"当前配置中不存在标注工具: {requested_tool_name}",
        )
    if label_tool_idx is None:
        tools.append(
            {"tool": "rectTool", "config": _merge_rect_tool_config(None, attrs)}
        )
    elif label_tool_name == "rectTool":
        previous = (
            tools[label_tool_idx].get("config")
            if isinstance(tools[label_tool_idx].get("config"), dict)
            else {}
        )
        tools[label_tool_idx]["config"] = _merge_rect_tool_config(previous, attrs)
    else:
        previous = (
            tools[label_tool_idx].get("config")
            if isinstance(tools[label_tool_idx].get("config"), dict)
            else {}
        )
        tools[label_tool_idx]["config"] = _merge_task_label_tool_config(previous, attrs)
    contract_label_ids = _task_contract_label_ids(tools) or label_ids
    contract_service.active_labels_by_ids(db, contract_label_ids)
    if dataset_contract is not None:
        final_ids = {int(value) for value in contract_label_ids}
        try:
            allowed_ids = {
                int(value)
                for value in json.loads(dataset_contract.label_ids_json or "[]")
            }
        except (TypeError, ValueError):
            allowed_ids = set()
        if not final_ids.issubset(allowed_ids):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="任务标注工具中的标签必须来自当前数据集标签契约",
            )
        if resolved_scope == "FULL" and final_ids != allowed_ids:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="FULL 任务必须覆盖数据集契约全部标签",
            )
    cfg.pop("defaultRectPresetId", None)
    with begin_transaction(db):
        contract_service.create_contract(
            db,
            task=task,
            label_ids=contract_label_ids,
            created_by=current_user.id,
            dataset_label_contract_id=(
                int(dataset_contract.id) if dataset_contract is not None else None
            ),
            label_scope_mode=resolved_scope,
        )
        crud_task.update(
            db,
            task,
            {
                "config": json.dumps(cfg, ensure_ascii=False),
                "status": TaskStatus.CONFIGURED.value,
                "training_ready": None,
                "annotation_audit_json": None,
            },
        )
    from labelu.internal.application.service.auto_sample_preannotate import (
        schedule_auto_preannotate_for_task_samples,
    )

    schedule_auto_preannotate_for_task_samples(
        db=db,
        task_id=task_id,
        user_id=current_user.id,
        source_hint="labels_apply",
    )

    updated = crud_task.get(db=db, task_id=task_id)
    contract = contract_service.current_contract(db, task_id)
    return TaskResponse(
        id=updated.id,
        name=updated.name,
        description=updated.description,
        tips=updated.tips,
        media_type=updated.media_type,
        config=updated.config,
        status=updated.status,
        created_at=updated.created_at,
        created_by=UserResp(id=updated.owner.id, username=updated.owner.username),
        current_annotation_contract_id=(contract.id if contract else None),
        annotation_contract_revision=(contract.revision if contract else None),
        label_set_signature=(contract.label_set_signature if contract else None),
        class_order_signature=(contract.class_order_signature if contract else None),
    )


async def apply_global_tags_to_task(
    db: Session,
    task_id: int,
    current_user: User,
    dimension_ids: list[int] | None = None,
) -> TaskResponse:
    task = _task_access_or_raise(db, task_id, current_user)
    if dimension_ids is not None and len(dimension_ids) == 0:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    dims = crud_annotation_taxonomy.global_dimensions_all_active(db)
    if dimension_ids is not None:
        id_set = set(dimension_ids)
        dims = [d for d in dims if d.id in id_set]
        if len(dims) != len(id_set):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_404_NOT_FOUND,
            )

    cfg = _parse_config(task.config)
    merge_global_dimensions_into_cfg(cfg, dims)

    config_str = json.dumps(cfg, ensure_ascii=False)
    with begin_transaction(db):
        crud_task.update(
            db,
            task,
            {
                "config": config_str,
                "status": TaskStatus.CONFIGURED.value,
            },
        )

    from labelu.internal.application.service.auto_sample_preannotate import (
        schedule_auto_preannotate_for_task_samples,
    )

    schedule_auto_preannotate_for_task_samples(
        db=db,
        task_id=task_id,
        user_id=current_user.id,
        source_hint="global_tags_apply",
    )

    updated = crud_task.get(db=db, task_id=task_id)
    return TaskResponse(
        id=updated.id,
        name=updated.name,
        description=updated.description,
        tips=updated.tips,
        media_type=updated.media_type,
        config=updated.config,
        status=updated.status,
        created_at=updated.created_at,
        created_by=UserResp(
            id=updated.owner.id,
            username=updated.owner.username,
        ),
    )
