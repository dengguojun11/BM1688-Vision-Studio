from __future__ import annotations

from labelu.internal.adapter.persistence import crud_training_task, crud_user
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.training_task import TrainingTask


def _task(*, owner_id: int, name: str, latest_job_id: int | None = None) -> TrainingTask:
    return TrainingTask(
        owner_id=owner_id,
        name=name,
        mode="label_dimension",
        status="PENDING",
        primary_task_id=None,
        selected_presets_snapshot_json="[]",
        selected_label_items_snapshot_json="[]",
        merged_class_names_snapshot_json="[]",
        source_tasks_snapshot_json="[]",
        ignored_tasks_snapshot_json="[]",
        latest_job_id=latest_job_id,
        latest_job_status="PENDING",
    )


def test_list_by_owner_searches_display_fields_and_treats_like_tokens_literally(db):
    owner = crud_user.get_user_by_username(db, "test@example.com")
    assert owner is not None

    literal = _task(owner_id=owner.id, name="100%_night", latest_job_id=123)
    other = _task(owner_id=owner.id, name="daylight", latest_job_id=456)
    with begin_transaction(db):
        db.add_all([literal, other])

    rows, total = crud_training_task.list_by_owner(
        db, owner.id, page=0, size=20, search="100%_"
    )

    assert total == 1
    assert [row.name for row in rows] == ["100%_night"]

    rows, total = crud_training_task.list_by_owner(
        db, owner.id, page=0, size=20, search="123"
    )
    assert total == 1
    assert rows[0].latest_job_id == 123
