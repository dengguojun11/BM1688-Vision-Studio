import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.application.service import annotation_stats as stats_service
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.sample_edit_log import SampleEditLog
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User


def test_annotation_stats_dashboard_counts_done_by_user_and_day(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "test@example.com",
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    assert user is not None

    task = Task(
        name="stats-task",
        description="",
        tips="",
        config='{"tools":[]}',
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.IMPORTED.value,
        created_by=user.id,
    )
    db.add(task)
    db.flush()

    att = TaskAttachment(
        filename="a.jpg",
        url="/media/a.jpg",
        path="/tmp/a.jpg",
        task_id=task.id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(att)
    db.flush()

    now = datetime.now()
    sample = TaskSample(
        inner_id=1,
        task_id=task.id,
        file_id=att.id,
        created_by=user.id,
        updated_by=user.id,
        created_at=now - timedelta(minutes=5),
        updated_at=now,
        state=SampleState.DONE.value,
        annotated_count=2,
        data="{}",
    )
    db.add(sample)
    db.flush()
    db.add(
        SampleEditLog(
            task_id=task.id,
            sample_id=sample.id,
            user_id=user.id,
            action="annotation_edit",
            previous_state=SampleState.NEW.value,
            new_state=SampleState.DONE.value,
            previous_annotated_count=0,
            new_annotated_count=2,
            previous_data="{}",
            new_data='{"result":{}}',
            created_at=now,
        )
    )
    db.commit()

    data = stats_service.get_annotation_stats_dashboard(
        db=db,
        current_user=user,
        task_id=task.id,
        date_from=now.date(),
        date_to=now.date(),
    )
    assert data.summary.total_done >= 1
    assert data.summary.today_done >= 1
    assert len(data.daily_by_user) >= 1
    assert len(data.sample_timings) >= 1
    assert data.sample_timings[0].duration_seconds >= 0


def test_annotation_stats_excludes_admin_user(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "test@example.com",
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    admin = db.query(User).filter(User.username == "admin@123.com").first()
    if not admin:
        admin = User(username="admin@123.com", hashed_password="x")
        db.add(admin)
        db.flush()

    task = Task(
        name="stats-exclude",
        description="",
        tips="",
        config='{"tools":[]}',
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.IMPORTED.value,
        created_by=user.id,
    )
    db.add(task)
    db.flush()

    att = TaskAttachment(
        filename="b.jpg",
        url="/media/b.jpg",
        path="/tmp/b.jpg",
        task_id=task.id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(att)
    db.flush()

    now = datetime.now()
    admin_sample = TaskSample(
        inner_id=1,
        task_id=task.id,
        file_id=att.id,
        created_by=admin.id,
        updated_by=admin.id,
        created_at=now - timedelta(minutes=2),
        updated_at=now,
        state=SampleState.DONE.value,
        annotated_count=1,
        data="{}",
    )
    db.add(admin_sample)
    db.flush()
    db.add(
        SampleEditLog(
            task_id=task.id,
            sample_id=admin_sample.id,
            user_id=admin.id,
            action="annotation_edit",
            previous_state=SampleState.NEW.value,
            new_state=SampleState.DONE.value,
            previous_annotated_count=0,
            new_annotated_count=1,
            previous_data="{}",
            new_data='{"result":{}}',
            created_at=now,
        )
    )
    db.commit()

    data = stats_service.get_annotation_stats_dashboard(
        db=db,
        current_user=user,
        task_id=task.id,
        date_from=now.date(),
        date_to=now.date(),
    )
    assert data.summary.total_done == 0
    assert not any(r.username == "admin@123.com" for r in data.daily_by_user)
    assert not any(r.username == "admin@123.com" for r in data.sample_timings)


def test_annotation_stats_credits_annotator_when_uploader_is_excluded_admin(
    db, monkeypatch
):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "test@example.com",
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    admin = db.query(User).filter(User.username == "admin@123.com").first()
    if not admin:
        admin = User(username="admin@123.com", hashed_password="x")
        db.add(admin)
        db.flush()

    worker = db.query(User).filter(User.username == "worker@7g.com").first()
    if not worker:
        worker = User(username="worker@7g.com", hashed_password="x")
        db.add(worker)
        db.flush()

    task = Task(
        name="stats-collab",
        description="",
        tips="",
        config='{"tools":[]}',
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.IMPORTED.value,
        created_by=admin.id,
    )
    db.add(task)
    db.flush()

    att = TaskAttachment(
        filename="c.jpg",
        url="/media/c.jpg",
        path="/tmp/c.jpg",
        task_id=task.id,
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(att)
    db.flush()

    now = datetime.now()
    sample = TaskSample(
        inner_id=1,
        task_id=task.id,
        file_id=att.id,
        created_by=admin.id,
        updated_by=admin.id,
        created_at=now - timedelta(minutes=3),
        updated_at=now,
        state=SampleState.DONE.value,
        annotated_count=1,
        data="{}",
    )
    db.add(sample)
    db.flush()
    sample.updaters.append(worker)
    db.add(
        SampleEditLog(
            task_id=task.id,
            sample_id=sample.id,
            user_id=worker.id,
            action="annotation_edit",
            previous_state=SampleState.NEW.value,
            new_state=SampleState.DONE.value,
            previous_annotated_count=0,
            new_annotated_count=1,
            previous_data="{}",
            new_data='{"result":{}}',
            created_at=now,
        )
    )
    db.commit()

    data = stats_service.get_annotation_stats_dashboard(
        db=db,
        current_user=user,
        task_id=task.id,
        date_from=now.date(),
        date_to=now.date(),
    )
    assert data.summary.total_done == 1
    assert data.summary.today_done == 1
    assert any(r.username == "worker@7g.com" for r in data.daily_by_user)
    assert not any(r.username == "admin@123.com" for r in data.daily_by_user)
    assert data.sample_timings[0].username == "worker@7g.com"


def test_annotation_stats_requires_superuser(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "admin@123.com",
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    with pytest.raises(LabelUException):
        stats_service.get_annotation_stats_dashboard(db=db, current_user=user)


def test_annotation_stats_dashboard_http_ok(
    client: TestClient, testuser_token_headers: dict, db: Session, monkeypatch
):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "test@example.com",
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    task = Task(
        name="stats-http",
        description="",
        tips="",
        config='{"tools":[]}',
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.IMPORTED.value,
        created_by=user.id,
    )
    db.add(task)
    db.flush()
    att = TaskAttachment(
        filename="http.jpg",
        url="/media/http.jpg",
        path="/tmp/http.jpg",
        task_id=task.id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(att)
    db.flush()
    now = datetime.now()
    sample = TaskSample(
        inner_id=1,
        task_id=task.id,
        file_id=att.id,
        created_by=user.id,
        updated_by=user.id,
        created_at=now - timedelta(minutes=2),
        updated_at=now,
        state=SampleState.DONE.value,
        annotated_count=1,
        data="{}",
    )
    db.add(sample)
    db.flush()
    db.add(
        SampleEditLog(
            task_id=task.id,
            sample_id=sample.id,
            user_id=user.id,
            action="annotation_edit",
            previous_state=SampleState.NEW.value,
            new_state=SampleState.DONE.value,
            previous_annotated_count=0,
            new_annotated_count=1,
            previous_data="{}",
            new_data='{"result":{}}',
            created_at=now,
        )
    )
    db.commit()

    r = client.get(
        f"{settings.API_V1_STR}/tasks/annotation-stats/dashboard",
        headers=testuser_token_headers,
        params={
            "date_from": str(now.date()),
            "date_to": str(now.date()),
            "sample_page": 0,
            "sample_size": 50,
        },
    )
    assert r.status_code == 200, r.text
    payload = r.json()["data"]
    assert payload["summary"]["total_done"] >= 1
    assert isinstance(payload["daily_by_user"], list)
    assert isinstance(payload["sample_timings"], list)


def test_sample_patch_only_logs_actual_annotation_changes(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "test@example.com",
    )
    from labelu.internal.application.command.sample import PatchSampleCommand
    from labelu.internal.application.service import sample as sample_service

    user = db.query(User).filter(User.username == "test@example.com").first()
    task = Task(
        name="stats-edit-log",
        description="",
        tips="",
        config='{"tools":[]}',
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.INPROGRESS.value,
        created_by=user.id,
    )
    db.add(task)
    db.flush()

    att = TaskAttachment(
        filename="edit.jpg",
        url="/media/edit.jpg",
        path="/tmp/edit.jpg",
        task_id=task.id,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(att)
    db.flush()

    sample = TaskSample(
        inner_id=1,
        task_id=task.id,
        file_id=att.id,
        created_by=user.id,
        updated_by=user.id,
        created_at=datetime.now() - timedelta(minutes=5),
        updated_at=datetime.now(),
        state=SampleState.DONE.value,
        annotated_count=1,
        data='{"result":"{\\"width\\":100,\\"rectTool\\":{\\"result\\":[{\\"id\\":\\"a\\"}]}}"}',
    )
    db.add(sample)
    db.commit()

    unchanged_cmd = PatchSampleCommand(
        data={"result": '{"width":100,"rectTool":{"result":[{"id":"a"}]}}'},
        state=SampleState.DONE.value,
        annotated_count=1,
    )
    asyncio.run(
        sample_service.patch(
            db=db,
            task_id=task.id,
            sample_id=sample.id,
            cmd=unchanged_cmd,
            current_user=user,
        )
    )
    assert db.query(SampleEditLog).filter(SampleEditLog.sample_id == sample.id).count() == 0

    changed_cmd = PatchSampleCommand(
        data={"result": '{"width":100,"rectTool":{"result":[{"id":"b"}]}}'},
        state=SampleState.DONE.value,
        annotated_count=1,
    )
    asyncio.run(
        sample_service.patch(
            db=db,
            task_id=task.id,
            sample_id=sample.id,
            cmd=changed_cmd,
            current_user=user,
        )
    )
    logs = db.query(SampleEditLog).filter(SampleEditLog.sample_id == sample.id).all()
    assert len(logs) == 1
    assert logs[0].user_id == user.id


def test_annotation_stats_uses_actual_edit_logs_for_daily_counts(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "test@example.com",
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    worker = db.query(User).filter(User.username == "worker2@7g.com").first()
    if not worker:
        worker = User(username="worker2@7g.com", hashed_password="x")
        db.add(worker)
        db.flush()

    task = Task(
        name="stats-edit-log-daily",
        description="",
        tips="",
        config='{"tools":[]}',
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.IMPORTED.value,
        created_by=user.id,
    )
    db.add(task)
    db.flush()

    now = datetime.now()
    for idx in range(2):
        att = TaskAttachment(
            filename=f"f{idx}.jpg",
            url=f"/media/f{idx}.jpg",
            path=f"/tmp/f{idx}.jpg",
            task_id=task.id,
            created_by=user.id,
            updated_by=worker.id,
        )
        db.add(att)
        db.flush()
        sample = TaskSample(
            inner_id=idx + 1,
            task_id=task.id,
            file_id=att.id,
            created_by=user.id,
            updated_by=worker.id,
            created_at=now - timedelta(minutes=10 + idx),
            updated_at=now - timedelta(minutes=idx),
            state=SampleState.DONE.value,
            annotated_count=1,
            data="{}",
        )
        db.add(sample)
        db.flush()
        db.add(
            SampleEditLog(
                task_id=task.id,
                sample_id=sample.id,
                user_id=worker.id,
                action="annotation_edit",
                previous_state=SampleState.NEW.value,
                new_state=SampleState.DONE.value,
                previous_annotated_count=0,
                new_annotated_count=1,
                previous_data="{}",
                new_data='{"result":{}}',
                created_at=now - timedelta(minutes=idx),
            )
        )
    db.commit()

    data = stats_service.get_annotation_stats_dashboard(
        db=db,
        current_user=user,
        task_id=task.id,
        date_from=now.date(),
        date_to=now.date(),
    )
    assert data.summary.total_done == 2
    assert any(
        row.username == "worker2@7g.com" and row.done_count == 2
        for row in data.daily_by_user
    )


def test_annotation_stats_dedupes_same_user_same_day_same_sample(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.task_access.settings.TASK_SUPERUSER_USERNAMES",
        "test@example.com",
    )
    user = db.query(User).filter(User.username == "test@example.com").first()
    worker = db.query(User).filter(User.username == "worker3@7g.com").first()
    if not worker:
        worker = User(username="worker3@7g.com", hashed_password="x")
        db.add(worker)
        db.flush()

    task = Task(
        name="stats-edit-log-dedupe",
        description="",
        tips="",
        config='{"tools":[]}',
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.IMPORTED.value,
        created_by=user.id,
    )
    db.add(task)
    db.flush()

    now = datetime.now()
    att = TaskAttachment(
        filename="same.jpg",
        url="/media/same.jpg",
        path="/tmp/same.jpg",
        task_id=task.id,
        created_by=user.id,
        updated_by=worker.id,
    )
    db.add(att)
    db.flush()

    sample = TaskSample(
        inner_id=1,
        task_id=task.id,
        file_id=att.id,
        created_by=user.id,
        updated_by=worker.id,
        created_at=now - timedelta(minutes=20),
        updated_at=now,
        state=SampleState.DONE.value,
        annotated_count=2,
        data="{}",
    )
    db.add(sample)
    db.flush()
    db.add(
        SampleEditLog(
            task_id=task.id,
            sample_id=sample.id,
            user_id=worker.id,
            action="annotation_edit",
            previous_state=SampleState.NEW.value,
            new_state=SampleState.DONE.value,
            previous_annotated_count=0,
            new_annotated_count=1,
            previous_data="{}",
            new_data='{"result":{"version":1}}',
            created_at=now - timedelta(minutes=5),
        )
    )
    db.add(
        SampleEditLog(
            task_id=task.id,
            sample_id=sample.id,
            user_id=worker.id,
            action="annotation_edit",
            previous_state=SampleState.DONE.value,
            new_state=SampleState.DONE.value,
            previous_annotated_count=1,
            new_annotated_count=2,
            previous_data='{"result":{"version":1}}',
            new_data='{"result":{"version":2}}',
            created_at=now,
        )
    )
    db.commit()

    data = stats_service.get_annotation_stats_dashboard(
        db=db,
        current_user=user,
        task_id=task.id,
        date_from=now.date(),
        date_to=now.date(),
    )
    assert data.summary.total_done == 1
    assert data.summary.today_done == 1
    assert any(
        row.username == "worker3@7g.com" and row.done_count == 1
        for row in data.daily_by_user
    )
    assert data.sample_timings_total == 1
