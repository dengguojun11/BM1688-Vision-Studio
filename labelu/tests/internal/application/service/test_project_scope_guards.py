from types import SimpleNamespace

import pytest

from labelu.internal.application.command.edge_device_job import CreateEdgeDeviceJobCommand
from labelu.internal.application.command.yolo_edge_validation import (
    CreateYoloEdgeValidationJobCommand,
)
from labelu.internal.application.service import board_agent, edge_device_job, yolo_edge_validation
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.project_scope import ProjectScopeError, assert_project_chain
from labelu.internal.domain.models.export_job import ExportJob, ExportStatus
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.task import MediaType, Task
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _project(db, name: str):
    with begin_transaction(db):
        row = Project(name=name, owner_id=1, created_by=1, updated_by=1)
        db.add(row)
        db.flush()
        db.refresh(row)
        return row


def _task(db, project_id: int | None, name: str):
    with begin_transaction(db):
        row = Task(
            name=name,
            media_type=MediaType.IMAGE.value,
            project_id=project_id,
            created_by=1,
            updated_by=1,
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return row


def test_project_chain_rejects_cross_project_and_known_unscoped_mix():
    assert assert_project_chain([7, 7], context="test") == 7
    assert assert_project_chain([None, None], context="legacy") is None
    with pytest.raises(ProjectScopeError):
        assert_project_chain([7, 8], context="cross-project")
    with pytest.raises(ProjectScopeError):
        assert_project_chain([7, None], context="known-and-legacy")


def test_retired_edge_device_job_control_plane_fails_closed_before_parent_lookup(db):
    with begin_transaction(db):
        export = ExportJob(
            task_id=None,
            created_by=1,
            export_type="dataset",
            status=ExportStatus.COMPLETED.value,
            sample_count=0,
            processed_count=0,
        )
        db.add(export)
        db.flush()
        db.refresh(export)

    with pytest.raises(LabelUException) as caught:
        import asyncio

        asyncio.run(
            edge_device_job.create(
                db,
                cmd=CreateEdgeDeviceJobCommand(export_job_id=export.id),
                current_user=SimpleNamespace(id=1),
            )
        )
    # 一期不再允许旧的 edge-device-jobs 控制面写入；父任务一致性由
    # 新的 BM1688 验证链路负责，因此旧入口应在任何数据库副作用前直接退休。
    assert caught.value.status_code == 403


def test_edge_validation_rejects_export_task_parent_mismatch(db, monkeypatch):
    task = _task(db, _project(db, "edge-scope").id, "edge-task")
    export = SimpleNamespace(id=99, task_id=task.id + 1)
    monkeypatch.setattr(
        yolo_edge_validation,
        "get_export_for_download",
        lambda *args, **kwargs: export,
    )
    monkeypatch.setattr(
        yolo_edge_validation.crud_task,
        "get",
        lambda *args, **kwargs: task,
    )
    cmd = CreateYoloEdgeValidationJobCommand(
        export_id=export.id,
        task_id=task.id,
        sample_ids=[1],
    )

    with pytest.raises(LabelUException) as caught:
        import asyncio

        asyncio.run(
            yolo_edge_validation.create_job(
                db,
                cmd=cmd,
                current_user=SimpleNamespace(id=1),
            )
        )
    assert caught.value.status_code == 409


def test_board_validation_rejects_mismatched_export_train_job_lineage(db):
    project = _project(db, "board-scope")
    task = _task(db, project.id, "board-task")
    other_task = _task(db, project.id, "board-other-task")
    task_id = int(task.id)
    other_task_id = int(other_task.id)
    with begin_transaction(db):
        train_job = YoloTrainJob(
            task_id=other_task_id,
            project_id=project.id,
            created_by=1,
            model_weights="yolov11n.pt",
            status="COMPLETED",
        )
        db.add(train_job)
        db.flush()
        export = YoloBm1688Export(
            user_id=1,
            task_id=task_id,
            yolo_train_job_id=train_job.id,
            status="COMPLETED",
        )
        db.add(export)
        db.flush()
        db.refresh(train_job)
        db.refresh(export)

    with pytest.raises(LabelUException) as caught:
        board_agent._assert_board_lineage(
            db,
            export_id=export.id,
            task_id=task_id,
            yolo_train_job_id=train_job.id,
        )
    assert caught.value.status_code == 409


def test_board_validation_rejects_missing_train_job_parent(db):
    """Malformed legacy rows must produce a lineage conflict, not TypeError."""

    project = _project(db, "board-missing-parent")
    task = _task(db, project.id, "board-task")
    task_id = int(task.id)
    with begin_transaction(db):
        train_job = YoloTrainJob(
            task_id=None,
            project_id=project.id,
            created_by=1,
            model_weights="yolov11n.pt",
            status="COMPLETED",
        )
        db.add(train_job)
        db.flush()
        export = YoloBm1688Export(
            user_id=1,
            task_id=task_id,
            yolo_train_job_id=train_job.id,
            status="COMPLETED",
        )
        db.add(export)
        db.flush()
        db.refresh(train_job)
        db.refresh(export)

    with pytest.raises(LabelUException) as caught:
        board_agent._assert_board_lineage(
            db,
            export_id=export.id,
            task_id=task_id,
            yolo_train_job_id=train_job.id,
        )
    assert caught.value.status_code == 409
