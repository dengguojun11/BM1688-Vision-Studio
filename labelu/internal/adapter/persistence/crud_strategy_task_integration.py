from datetime import datetime
from typing import Any, Dict, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyPlatformCallbackReceipt,
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob

_UNSET = object()


def create(db: Session, row: StrategyTaskIntegration) -> StrategyTaskIntegration:
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_by_external_task_id(
    db: Session, external_task_id: str, *, lock: bool = False
) -> StrategyTaskIntegration | None:
    query = db.query(StrategyTaskIntegration).filter(
        StrategyTaskIntegration.external_task_id == external_task_id,
        StrategyTaskIntegration.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    return query.first()


def get_by_derived_train_job_id(
    db: Session, *, derived_train_job_id: int
) -> StrategyTaskIntegration | None:
    return (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.derived_train_job_id == int(derived_train_job_id),
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .order_by(
            StrategyTaskIntegration.updated_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .first()
    )


def get_by_derived_export_id(
    db: Session, *, derived_export_id: int
) -> StrategyTaskIntegration | None:
    return (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.derived_export_id == int(derived_export_id),
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .order_by(
            StrategyTaskIntegration.updated_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .first()
    )


def get_by_related_export_id(
    db: Session, *, export_id: int
) -> StrategyTaskIntegration | None:
    """Find the strategy task represented by an export or its edge job.

    Model-capability tasks own a newly-created derived export, while threshold
    experiments may reuse an existing export and only create an edge-validation
    job. Keep the export's own provenance intact and resolve the relationship
    from the integration pointers instead.
    """
    export_id = int(export_id)
    derived = get_by_derived_export_id(db, derived_export_id=export_id)
    if derived is not None:
        return derived

    threshold = (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.source_export_id == export_id,
            StrategyTaskIntegration.task_type == "threshold_experiment",
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .order_by(
            StrategyTaskIntegration.updated_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .first()
    )
    if threshold is not None:
        return threshold

    return (
        db.query(StrategyTaskIntegration)
        .join(
            YoloEdgeValidationJob,
            YoloEdgeValidationJob.source_integration_id == StrategyTaskIntegration.id,
        )
        .filter(
            YoloEdgeValidationJob.export_id == export_id,
            StrategyTaskIntegration.task_type == "threshold_experiment",
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .order_by(
            StrategyTaskIntegration.updated_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .first()
    )


def get_by_related_export_ids(
    db: Session, *, export_ids: Iterable[int]
) -> Dict[int, StrategyTaskIntegration]:
    """Resolve strategy-task markers for multiple exports with bounded queries."""
    normalized_ids = sorted({int(export_id) for export_id in export_ids if export_id})
    if not normalized_ids:
        return {}

    result: Dict[int, StrategyTaskIntegration] = {}
    derived_rows = (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.derived_export_id.in_(normalized_ids),
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .order_by(
            StrategyTaskIntegration.updated_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .all()
    )
    for row in derived_rows:
        result.setdefault(int(row.derived_export_id), row)

    remaining_ids = [
        export_id for export_id in normalized_ids if export_id not in result
    ]
    if not remaining_ids:
        return result

    source_rows = (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.source_export_id.in_(remaining_ids),
            StrategyTaskIntegration.task_type == "threshold_experiment",
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .order_by(
            StrategyTaskIntegration.updated_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .all()
    )
    for row in source_rows:
        result.setdefault(int(row.source_export_id), row)

    remaining_ids = [
        export_id for export_id in remaining_ids if export_id not in result
    ]
    if not remaining_ids:
        return result

    edge_rows = (
        db.query(YoloEdgeValidationJob.export_id, StrategyTaskIntegration)
        .join(
            StrategyTaskIntegration,
            YoloEdgeValidationJob.source_integration_id == StrategyTaskIntegration.id,
        )
        .filter(
            YoloEdgeValidationJob.export_id.in_(remaining_ids),
            StrategyTaskIntegration.task_type == "threshold_experiment",
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .order_by(
            StrategyTaskIntegration.updated_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .all()
    )
    for export_id, row in edge_rows:
        result.setdefault(int(export_id), row)
    return result


def get_platform_callback_receipt(
    db: Session,
    *,
    strategy_task_integration_id: int,
    callback_event_id: str,
) -> StrategyPlatformCallbackReceipt | None:
    return (
        db.query(StrategyPlatformCallbackReceipt)
        .filter(
            StrategyPlatformCallbackReceipt.strategy_task_integration_id
            == strategy_task_integration_id,
            StrategyPlatformCallbackReceipt.callback_event_id == callback_event_id,
        )
        .first()
    )


def create_platform_callback_receipt(
    db: Session,
    *,
    strategy_task_integration_id: int,
    callback_event_id: str,
    platform_status: str,
) -> StrategyPlatformCallbackReceipt:
    receipt = StrategyPlatformCallbackReceipt(
        strategy_task_integration_id=strategy_task_integration_id,
        callback_event_id=callback_event_id,
        platform_status=platform_status,
    )
    db.add(receipt)
    db.flush()
    return receipt


def list_rows(
    db: Session,
    *,
    task_type: str | None = None,
    platform_status: str | None = None,
    keyword: str | None = None,
    external_task_id: str | None = None,
    model_id: str | None = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[StrategyTaskIntegration], int]:
    query = db.query(StrategyTaskIntegration).filter(
        StrategyTaskIntegration.deleted_at.is_(None),
    )
    if task_type:
        query = query.filter(StrategyTaskIntegration.task_type == task_type)
    if platform_status:
        query = query.filter(StrategyTaskIntegration.platform_status == platform_status)
    if external_task_id:
        query = query.filter(
            StrategyTaskIntegration.external_task_id == str(external_task_id).strip()
        )
    normalized_model_id = str(model_id or "").strip()
    if normalized_model_id:
        # target_json is the canonical, database-portable snapshot for the
        # external target.  A bounded substring match keeps this compatible
        # with SQLite and MySQL without introducing a second model-id column.
        model_value = normalized_model_id
        token = f'"model_id": "{model_value}"'
        compact_token = f'"model_id":"{model_value}"'
        legacy_token = f'"modelId": "{model_value}"'
        compact_legacy_token = f'"modelId":"{model_value}"'
        query = query.filter(
            or_(
                StrategyTaskIntegration.target_json.ilike(
                    literal_like_pattern(token), escape=LIKE_ESCAPE
                ),
                StrategyTaskIntegration.target_json.ilike(
                    literal_like_pattern(compact_token), escape=LIKE_ESCAPE
                ),
                StrategyTaskIntegration.target_json.ilike(
                    literal_like_pattern(legacy_token), escape=LIKE_ESCAPE
                ),
                StrategyTaskIntegration.target_json.ilike(
                    literal_like_pattern(compact_legacy_token), escape=LIKE_ESCAPE
                ),
            )
        )
    normalized_keyword = str(keyword or "").strip()
    if normalized_keyword:
        token = literal_like_pattern(normalized_keyword)
        query = query.filter(
            or_(
                StrategyTaskIntegration.external_task_id.ilike(
                    token, escape=LIKE_ESCAPE
                ),
                StrategyTaskIntegration.task_name.ilike(token, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(
            StrategyTaskIntegration.created_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def update(
    db: Session, row: StrategyTaskIntegration, obj_in: dict[str, Any]
) -> StrategyTaskIntegration:
    for key, value in obj_in.items():
        setattr(row, key, value)
    db.flush()
    db.refresh(row)
    return row


def update_by_external_task_id(
    db: Session,
    *,
    external_task_id: str,
    obj_in: dict[str, Any],
) -> StrategyTaskIntegration | None:
    updates = {key: value for key, value in obj_in.items()}
    matched = (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.external_task_id == external_task_id,
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .update(updates, synchronize_session=False)
    )
    db.flush()
    if not matched:
        return None
    db.expire_all()
    return get_by_external_task_id(db=db, external_task_id=external_task_id)


def update_by_external_task_id_and_lease_owner(
    db: Session,
    *,
    external_task_id: str,
    lease_owner: str,
    obj_in: dict[str, Any],
    now: datetime | None = None,
) -> StrategyTaskIntegration | None:
    """Apply an automation snapshot only for the current lease generation."""
    current = now or datetime.now()
    matched = (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.external_task_id == external_task_id,
            StrategyTaskIntegration.deleted_at.is_(None),
            StrategyTaskIntegration.automation_lease_owner == lease_owner,
            StrategyTaskIntegration.automation_lease_expires_at > current,
        )
        .update(dict(obj_in), synchronize_session=False)
    )
    db.flush()
    if not matched:
        return None
    db.expire_all()
    return get_by_external_task_id(db=db, external_task_id=external_task_id)


def list_by_automation_stage(
    db: Session,
    *,
    automation_stage: str,
) -> list[StrategyTaskIntegration]:
    return (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.deleted_at.is_(None),
            StrategyTaskIntegration.automation_stage == automation_stage,
        )
        .order_by(
            StrategyTaskIntegration.updated_at.desc(),
            StrategyTaskIntegration.id.desc(),
        )
        .all()
    )


def update_automation_state(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    automation_stage: str | None = None,
    automation_status: str | None = None,
    automation_result_json: str | None = None,
    platform_status: str | None = None,
    platform_result_json: str | None = None,
    last_error: str | None | object = _UNSET,
    extra_updates: dict[str, Any] | None = None,
) -> StrategyTaskIntegration:
    updates: dict[str, Any] = {}
    if automation_stage is not None:
        updates[StrategyTaskIntegration.automation_stage.key] = automation_stage
    if automation_status is not None:
        updates[StrategyTaskIntegration.automation_status.key] = automation_status
    if automation_result_json is not None:
        updates[StrategyTaskIntegration.automation_result_json.key] = (
            automation_result_json
        )
    if platform_status is not None:
        updates[StrategyTaskIntegration.platform_status.key] = platform_status
    if platform_result_json is not None:
        updates[StrategyTaskIntegration.platform_result_json.key] = platform_result_json
    if last_error is not _UNSET:
        updates[StrategyTaskIntegration.last_error.key] = last_error
    if extra_updates:
        updates.update(extra_updates)
    return update(db=db, row=row, obj_in=updates)


def record_outbound_callback_result(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    callback_status: str,
    callback_attempts: int,
    callback_last_error: str | None,
    callback_url: str | None = None,
    callback_event_id: str | None = None,
    callback_next_retry_at: datetime | None = None,
) -> StrategyTaskIntegration:
    updates = {
        StrategyTaskIntegration.outbound_callback_status.key: callback_status,
        StrategyTaskIntegration.outbound_callback_attempts.key: int(callback_attempts),
        StrategyTaskIntegration.outbound_callback_last_error.key: callback_last_error,
        StrategyTaskIntegration.outbound_callback_next_retry_at.key: callback_next_retry_at,
    }
    if callback_url is not None:
        updates[StrategyTaskIntegration.outbound_callback_url.key] = callback_url
    if callback_event_id is not None:
        updates[StrategyTaskIntegration.outbound_callback_event_id.key] = (
            callback_event_id
        )
    return update(db=db, row=row, obj_in=updates)


def claim_automation_lease(
    db: Session,
    *,
    external_task_id: str,
    lease_owner: str,
    now: datetime,
    lease_expires_at: datetime,
) -> StrategyTaskIntegration | None:
    row = get_by_external_task_id(db, external_task_id, lock=True)
    if not row:
        return None
    current_owner = str(row.automation_lease_owner or "").strip()
    current_expiry = row.automation_lease_expires_at
    if current_owner and current_expiry and current_expiry > now:
        return None
    return update(
        db=db,
        row=row,
        obj_in={
            StrategyTaskIntegration.automation_lease_owner.key: lease_owner,
            StrategyTaskIntegration.automation_lease_expires_at.key: lease_expires_at,
            StrategyTaskIntegration.automation_attempts.key: int(
                row.automation_attempts or 0
            )
            + 1,
        },
    )


def release_automation_lease(
    db: Session,
    *,
    external_task_id: str,
    lease_owner: str,
) -> StrategyTaskIntegration | None:
    row = get_by_external_task_id(db, external_task_id, lock=True)
    if not row or str(row.automation_lease_owner or "").strip() != lease_owner:
        return row
    return update(
        db=db,
        row=row,
        obj_in={
            StrategyTaskIntegration.automation_lease_owner.key: None,
            StrategyTaskIntegration.automation_lease_expires_at.key: None,
        },
    )


def soft_delete(db: Session, row: StrategyTaskIntegration) -> StrategyTaskIntegration:
    row.deleted_at = datetime.now()
    db.flush()
    db.refresh(row)
    return row
