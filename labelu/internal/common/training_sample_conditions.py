"""Shared validation and matching for global-tag training filters.

Training task previews and immutable dataset snapshots must apply exactly the
same semantics: values within one dimension are OR'ed, while dimensions are
AND'ed.  Keeping the helpers in ``common`` prevents the preview path from
drifting away from the snapshot path.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.annotation_taxonomy import (
    GlobalTagDimension,
    GlobalTagOption,
)


def normalize_sample_conditions(
    conditions: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    """Return a deterministic, trimmed condition mapping.

    The Pydantic commands already enforce the public size limits.  This helper
    remains defensive because it is also called by internal services and by
    historical replay paths.
    """

    normalized: dict[str, list[str]] = {}
    if not isinstance(conditions, Mapping):
        return normalized
    for raw_key, raw_values in conditions.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if isinstance(raw_values, (str, bytes)):
            values_iter = [raw_values]
        elif isinstance(raw_values, (list, tuple, set, frozenset)):
            values_iter = raw_values
        else:
            values_iter = []
        values: list[str] = []
        for raw_value in values_iter:
            value = str(raw_value or "").strip()
            if value and value not in values:
                values.append(value)
        if values:
            normalized[key] = values
    return normalized


def validate_sample_conditions(
    db: Session,
    conditions: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    """Validate dimension and option keys against the active global taxonomy."""

    normalized = normalize_sample_conditions(conditions)
    if not normalized:
        return {}
    rows = (
        db.query(GlobalTagDimension)
        .filter(
            GlobalTagDimension.field_key.in_(normalized),
            GlobalTagDimension.deleted_at.is_(None),
        )
        .all()
    )
    by_key = {str(row.field_key): row for row in rows}
    missing_keys = sorted(set(normalized) - set(by_key))
    if missing_keys:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"训练筛选维度不存在或已停用：{missing_keys}",
        )
    for key, values in normalized.items():
        allowed = {
            str(value)
            for (value,) in (
                db.query(GlobalTagOption.value_en)
                .filter(GlobalTagOption.dimension_id == int(by_key[key].id))
                .all()
            )
        }
        invalid = sorted(set(values) - allowed)
        if invalid:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"训练筛选维度 {key} 包含无效选项：{invalid}",
            )
    return normalized


def sample_condition_values(result: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """Extract all global-tag values from a LabelU ``tagTool`` result."""

    raw_tag_values: dict[str, list[str]] = {}
    if not isinstance(result, Mapping):
        return raw_tag_values
    tag_block = result.get("tagTool") or {}
    if not isinstance(tag_block, Mapping):
        return raw_tag_values
    items = tag_block.get("result") or []
    if not isinstance(items, (list, tuple)):
        return raw_tag_values
    for item in items:
        if not isinstance(item, Mapping) or not isinstance(item.get("value"), Mapping):
            continue
        for raw_key, raw_value in item["value"].items():
            values = raw_value if isinstance(raw_value, (list, tuple)) else [raw_value]
            normalized = [
                str(value).strip() for value in values if str(value or "").strip()
            ]
            if normalized:
                raw_tag_values.setdefault(str(raw_key), []).extend(normalized)
    return {
        key: list(dict.fromkeys(values)) for key, values in raw_tag_values.items()
    }


def sample_matches_conditions(
    result: Mapping[str, Any] | None,
    conditions: Mapping[str, list[str]] | None,
) -> tuple[bool, dict[str, str]]:
    """Match a sample against global conditions (OR within, AND across)."""

    normalized = normalize_sample_conditions(conditions)
    if not normalized:
        return True, {}
    values_by_key = sample_condition_values(result)
    matched: dict[str, str] = {}
    for key, values in normalized.items():
        allowed = set(values)
        hit = next(
            (candidate for candidate in values_by_key.get(key, []) if candidate in allowed),
            "",
        )
        if not hit:
            return False, matched
        matched[key] = hit
    return True, matched

