import asyncio

from labelu.internal.adapter.persistence import crud_attachment, crud_sample, crud_task
from labelu.internal.application.command.attachment import AttachmentDeleteCommand
from labelu.internal.application.service import attachment as attachment_service
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.user import User


class _Storage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, key: str) -> None:
        self.deleted.append(key)

    def exists(self, key: str) -> bool:
        return key.endswith("-thumbnail.jpg")


def _task_with_attachment(db, *, filename: str = "attachment.jpg"):
    user = db.query(User).filter(User.username == "test@example.com").one()
    with begin_transaction(db):
        task = crud_task.create(
            db,
            Task(
                name="attachment delete lifecycle",
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


def test_delete_keeps_attachment_referenced_by_active_sample(db, monkeypatch) -> None:
    user, task, attachment = _task_with_attachment(db)
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
    monkeypatch.setattr(attachment_service, "get_storage_backend", lambda: storage)

    asyncio.run(
        attachment_service.delete(
            db,
            task.id,
            AttachmentDeleteCommand(attachment_ids=[attachment.id]),
            user,
        )
    )

    db.expire_all()
    assert storage.deleted == []
    assert crud_attachment.get(db=db, attachment_id=attachment.id) is not None
    assert crud_sample.get(db=db, sample_id=sample.id) is not None


def test_delete_retires_unreferenced_attachment_and_removes_objects(db, monkeypatch) -> None:
    user, task, attachment = _task_with_attachment(db, filename="unreferenced.jpg")
    storage = _Storage()
    monkeypatch.setattr(attachment_service, "get_storage_backend", lambda: storage)

    asyncio.run(
        attachment_service.delete(
            db,
            task.id,
            AttachmentDeleteCommand(attachment_ids=[attachment.id]),
            user,
        )
    )

    db.expire_all()
    assert crud_attachment.get(db=db, attachment_id=attachment.id) is None
    assert storage.deleted == ["upload/unreferenced.jpg", "upload/unreferenced-thumbnail.jpg"]
