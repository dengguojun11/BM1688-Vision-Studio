"""CRUD/read projections for model series; resolution lives in model_series."""

from __future__ import annotations

import json
from typing import Any

from fastapi import status
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.application.command.model_series import (
    CreateModelSeriesCommand,
    UpdateModelSeriesCommand,
)
from labelu.internal.application.response.model_series import (
    ModelSeriesResponse,
    ModelVariantResponse,
)
from labelu.internal.application.service.model_series import _error, _series_visible
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.model_series import ModelSeries, ModelVariant
from labelu.internal.domain.models.user import User


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def variant_response(row: ModelVariant) -> ModelVariantResponse:
    return ModelVariantResponse(
        id=int(row.id),
        series_id=int(row.series_id),
        name=str(row.name),
        branch_key=str(row.branch_key),
        selection_contract_sha256=str(row.selection_contract_sha256),
        selection_contract=_json_object(row.selection_contract_json),
        class_contract=_json_object(row.class_contract_json),
        recipe=_json_object(row.recipe_json),
        parent_model_version_id=row.parent_model_version_id,
        status=str(row.status),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def series_response(db: Session, row: ModelSeries, *, include_variants: bool = True) -> ModelSeriesResponse:
    variants = []
    if include_variants:
        variants = [
            variant_response(item)
            for item in db.query(ModelVariant)
            .filter(
                ModelVariant.series_id == int(row.id),
                ModelVariant.deleted_at.is_(None),
            )
            .order_by(ModelVariant.id.asc())
            .all()
        ]
    count = (
        len(variants)
        if include_variants
        else db.query(ModelVariant.id)
        .filter(ModelVariant.series_id == int(row.id), ModelVariant.deleted_at.is_(None))
        .count()
    )
    return ModelSeriesResponse(
        id=int(row.id),
        owner_id=int(row.owner_id),
        name=str(row.name),
        description=row.description,
        model_family=row.model_family,
        status=str(row.status),
        variant_count=int(count),
        variants=variants,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create(db: Session, *, user: User, cmd: CreateModelSeriesCommand) -> ModelSeriesResponse:
    name = str(cmd.name or "").strip()
    existing = (
        db.query(ModelSeries)
        .filter(
            ModelSeries.owner_id == int(user.id),
            ModelSeries.name == name,
            ModelSeries.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if existing is not None:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="模型系列名称已存在",
        )
    row = ModelSeries(
        owner_id=int(user.id),
        name=name,
        description=cmd.description,
        model_family=cmd.model_family,
        status="ACTIVE",
    )
    try:
        with begin_transaction(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="模型系列名称已存在",
        ) from exc
    return series_response(db, row)


def list_series(
    db: Session,
    *,
    user: User,
    page: int = 0,
    size: int = 20,
    search: str | None = None,
    status_value: str | None = "ACTIVE",
) -> tuple[list[ModelSeriesResponse], int]:
    query = db.query(ModelSeries).filter(ModelSeries.deleted_at.is_(None))
    if not is_task_superuser(user):
        query = query.filter(ModelSeries.owner_id == int(user.id))
    if status_value:
        query = query.filter(ModelSeries.status == str(status_value).strip().upper())
    if str(search or "").strip():
        pattern = literal_like_pattern(str(search).strip())
        query = query.filter(
            or_(
                ModelSeries.name.ilike(pattern, escape=LIKE_ESCAPE),
                ModelSeries.description.ilike(pattern, escape=LIKE_ESCAPE),
                ModelSeries.model_family.ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(ModelSeries.updated_at.desc(), ModelSeries.id.desc())
        .offset(max(0, int(page)) * int(size))
        .limit(int(size))
        .all()
    )
    return [series_response(db, row, include_variants=False) for row in rows], total


def get(db: Session, *, user: User, series_id: int, include_variants: bool = True) -> ModelSeriesResponse:
    row = db.query(ModelSeries).filter(ModelSeries.id == int(series_id)).one_or_none()
    if row is None or not _series_visible(row, user):
        raise _error("模型系列不存在或无权访问", status.HTTP_404_NOT_FOUND)
    return series_response(db, row, include_variants=include_variants)


def update(db: Session, *, user: User, series_id: int, cmd: UpdateModelSeriesCommand) -> ModelSeriesResponse:
    row = db.query(ModelSeries).filter(ModelSeries.id == int(series_id)).one_or_none()
    if row is None or not _series_visible(row, user):
        raise _error("模型系列不存在或无权修改", status.HTTP_404_NOT_FOUND)
    if cmd.name is not None:
        duplicate = (
            db.query(ModelSeries)
            .filter(
                ModelSeries.owner_id == int(row.owner_id),
                ModelSeries.name == cmd.name,
                ModelSeries.id != int(row.id),
                ModelSeries.deleted_at.is_(None),
            )
            .first()
        )
        if duplicate is not None:
            raise _error("模型系列名称已存在", status.HTTP_409_CONFLICT)
        row.name = cmd.name
    for field in ("description", "model_family", "status"):
        value = getattr(cmd, field)
        if value is not None:
            setattr(row, field, value)
    with begin_transaction(db):
        db.add(row)
        db.flush()
    return series_response(db, row)

