"""Idempotent system identities used by integrations and background workers."""

import json

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_user
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.user import User


OPEN_SYSTEM_USERNAME = "open@system"


def _backfill_strategy_provenance(db: Session, user: User) -> None:
    """Backfill only records with an explicit strategy-integration relation."""
    from labelu.internal.domain.models.experiment_dataset_import import (
        ExperimentDatasetImport,
    )
    from labelu.internal.domain.models.strategy_task_integration import (
        StrategyTaskIntegration,
    )
    from labelu.internal.domain.models.task import Task
    from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
    from labelu.internal.domain.models.yolo_edge_validation_job import (
        YoloEdgeValidationJob,
    )
    from labelu.internal.domain.models.yolo_train_job import YoloTrainJob

    integrations = (
        db.query(StrategyTaskIntegration)
        .filter(StrategyTaskIntegration.deleted_at.is_(None))
        .all()
    )
    if not integrations:
        return
    with begin_transaction(db):
        for integration in integrations:
            integration.source_type = "third_party"
            integration.source_system = "strategy_evolution"
            integration.source_external_package_id = (
                integration.source_package_id or integration.source_external_package_id
            )
            integration.created_by = integration.created_by or user.id
            integration.updated_by = user.id
            db.add(integration)

            lineage = {
                "source_type": "third_party",
                "source_system": "strategy_evolution",
                "source_external_task_id": integration.external_task_id,
                "source_external_package_id": integration.source_package_id,
                "source_integration_id": integration.id,
            }
            related_tasks: set[int] = set()
            if integration.labelu_task_id:
                related_tasks.add(int(integration.labelu_task_id))

            if integration.source_package_id:
                package = (
                    db.query(ExperimentDatasetImport)
                    .filter(
                        ExperimentDatasetImport.package_id
                        == integration.source_package_id,
                        ExperimentDatasetImport.deleted_at.is_(None),
                    )
                    .first()
                )
                if package:
                    for key, value in lineage.items():
                        setattr(package, key, value)
                    package.created_by = user.id
                    package.updated_by = user.id
                    db.add(package)
                    try:
                        related_tasks.update(
                            int(item)
                            for item in json.loads(package.formal_task_ids_json or "[]")
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                    if package.draft_task_id:
                        related_tasks.add(int(package.draft_task_id))

            if related_tasks:
                for task in db.query(Task).filter(Task.id.in_(related_tasks)).all():
                    for key, value in lineage.items():
                        setattr(task, key, value)
                    task.created_by = user.id
                    task.updated_by = user.id
                    db.add(task)

            for model, record_id in (
                (YoloTrainJob, integration.derived_train_job_id),
                (YoloBm1688Export, integration.derived_export_id),
                (YoloEdgeValidationJob, integration.derived_edge_validation_job_id),
            ):
                if not record_id:
                    continue
                record = db.query(model).filter(model.id == int(record_id)).first()
                if record:
                    for key, value in lineage.items():
                        setattr(record, key, value)
                    db.add(record)


def ensure_open_system_user(db: Session) -> User:
    """Return the non-interactive third-party service identity."""
    user = crud_user.get_user_by_username(db, username=OPEN_SYSTEM_USERNAME)
    if user:
        updates = {}
        if getattr(user, "account_type", None) != "service":
            updates["account_type"] = "service"
        if not bool(getattr(user, "is_service_account", False)):
            updates["is_service_account"] = True
        if bool(getattr(user, "interactive_login_enabled", True)):
            updates["interactive_login_enabled"] = False
        if updates:
            with begin_transaction(db):
                for key, value in updates.items():
                    setattr(user, key, value)
                db.add(user)
        _backfill_strategy_provenance(db, user)
        return user

    # A service identity is never used for interactive login, so its password
    # is an unusable sentinel rather than a credential shared with operators.
    service_user = User(
        username=OPEN_SYSTEM_USERNAME,
        hashed_password="!service-account-disabled!",
        account_type="service",
        is_service_account=True,
        interactive_login_enabled=False,
    )
    try:
        with begin_transaction(db):
            service_user = crud_user.create(db, service_user)
    except IntegrityError:
        db.rollback()
        user = crud_user.get_user_by_username(db, username=OPEN_SYSTEM_USERNAME)
        if user:
            return user
        raise
    logger.info("Created third-party service account {}", OPEN_SYSTEM_USERNAME)
    _backfill_strategy_provenance(db, service_user)
    return service_user
