import asyncio

from labelu.internal.adapter.persistence import crud_attachment, crud_sample, crud_task
from labelu.internal.application.service import sample as sample_service
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.user import User


class _Storage:
    def __init__(self, *, thumbnail_exists: bool = True) -> None:
        self.deleted: list[str] = []
        self.thumbnail_exists = thumbnail_exists

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    def exists(self, key: str) -> bool:
        return self.thumbnail_exists and key.endswith("-thumbnail.jpg")


def _task_with_attachment(db, *, filename: str = "shared.jpg"):
    user = db.query(User).filter(User.username == "test@example.com").one()
    with begin_transaction(db):
        task = crud_task.create(
            db,
            Task(
                name="sample attachment lifecycle",
                media_type=MediaType.IMAGE.value,
                created_by=user.id,
                updated_by=user.id,
            ),
        )
        attachment = crud_attachment.create(
            db,
            TaskAttachment(
                task_id=task.id,
                filename=filename,
                path=f"upload/{filename}",
                created_by=user.id,
                updated_by=user.id,
            ),
        )
    return user, task, attachment


def test_delete_keeps_shared_attachment_until_last_active_sample(
    db, monkeypatch
) -> None:
    user, task, attachment = _task_with_attachment(db)
    with begin_transaction(db):
        samples = crud_sample.batch(
            db,
            [
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    data="{}",
                ),
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    data="{}",
                ),
            ],
        )

    storage = _Storage()
    monkeypatch.setattr(sample_service, "get_storage_backend", lambda: storage)

    asyncio.run(sample_service.delete(db, task.id, [samples[0].id], user))
    db.expire_all()
    assert storage.deleted == []
    assert db.query(TaskAttachment).filter(TaskAttachment.id == attachment.id).one().deleted_at is None
    assert crud_sample.count(db=db, task_id=task.id) == 1

    asyncio.run(sample_service.delete(db, task.id, [samples[1].id], user))
    db.expire_all()
    assert storage.deleted == ["upload/shared.jpg", "upload/shared-thumbnail.jpg"]
    assert db.query(TaskAttachment).filter(TaskAttachment.id == attachment.id).one().deleted_at is not None
    assert crud_sample.count(db=db, task_id=task.id) == 0


def test_delete_retires_unreferenced_attachment_row_after_removing_file(
    db, monkeypatch
) -> None:
    user, task, attachment = _task_with_attachment(db, filename="orphan.jpg")
    with begin_transaction(db):
        sample = crud_sample.batch(
            db,
            [
                TaskSample(
                    task_id=task.id,
                    file_id=attachment.id,
                    created_by=user.id,
                    updated_by=user.id,
                    data="{}",
                )
            ],
        )[0]

    storage = _Storage()
    monkeypatch.setattr(sample_service, "get_storage_backend", lambda: storage)

    asyncio.run(sample_service.delete(db, task.id, [sample.id], user))
    db.expire_all()
    row = db.query(TaskAttachment).filter(TaskAttachment.id == attachment.id).one()
    assert row.deleted_at is not None
    assert storage.deleted == ["upload/orphan.jpg", "upload/orphan-thumbnail.jpg"]
