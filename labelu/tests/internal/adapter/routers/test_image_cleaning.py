from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from labelu.internal.adapter.routers import image_cleaning_task as router
from labelu.internal.application.command.image_cleaning_task import (
    CreateSamplesWithImageCleaningCommand,
    ImageCleaningCommitCommand,
)
from labelu.internal.application.command.sample import CreateSampleCommand
from labelu.internal.application.response.image_cleaning_task import (
    ImageCleaningPreviewResponse,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException


def test_cleaning_route_deletes_duplicates_before_creating_samples(monkeypatch) -> None:
    events: list[str] = []
    preview = ImageCleaningPreviewResponse(
        active_preset="standard",
        threshold=0.95,
        policy_revision=1,
        algorithm_version="visual-v1",
        total_count=2,
        duplicate_count=1,
        keep_count=1,
        duplicate_attachment_ids=[2],
        keep_attachment_ids=[1],
        pairs=[{"keep_id": 1, "duplicate_id": 2, "similarity": 1.0}],
        fingerprint="f" * 64,
    )

    def fake_validate(*args, **kwargs):
        events.append("validate")
        return [1], preview

    def fake_delete(*args, **kwargs):
        events.append("delete")

    async def fake_create(*args, **kwargs):
        events.append("create")
        return SimpleNamespace(ids=[101])

    monkeypatch.setattr(
        router.cleaning_service, "validate_task_cleaning", fake_validate
    )
    monkeypatch.setattr(
        router.cleaning_service,
        "delete_task_cleaning_duplicates",
        fake_delete,
    )
    monkeypatch.setattr(router.sample_service, "create", fake_create)

    command = CreateSamplesWithImageCleaningCommand(
        samples=[CreateSampleCommand(file_id=1, data={"result": "{}"})],
        cleaning=ImageCleaningCommitCommand(
            attachment_ids=[1, 2],
            policy_revision=1,
            fingerprint="f" * 64,
        ),
    )
    response = asyncio.run(
        router.create_samples_with_image_cleaning(
            task_id=7,
            cmd=command,
            authorization=None,
            db=SimpleNamespace(),
            current_user=SimpleNamespace(id=1),
        )
    )

    assert events == ["validate", "delete", "create"]
    assert response.data.ids == [101]
    assert response.data.deleted_attachment_ids == [2]


def test_cleaning_route_can_finish_when_every_image_is_a_duplicate(monkeypatch) -> None:
    preview = ImageCleaningPreviewResponse(
        active_preset="standard",
        threshold=0.95,
        policy_revision=1,
        algorithm_version="visual-v1",
        total_count=2,
        duplicate_count=2,
        keep_count=0,
        duplicate_attachment_ids=[1, 2],
        keep_attachment_ids=[],
        pairs=[
            {"keep_id": 1, "duplicate_id": 2, "similarity": 1.0},
        ],
        fingerprint="f" * 64,
    )
    delete_calls: list[list[int]] = []

    monkeypatch.setattr(
        router.cleaning_service,
        "validate_task_cleaning",
        lambda *args, **kwargs: ([], preview),
    )
    monkeypatch.setattr(
        router.cleaning_service,
        "delete_task_cleaning_duplicates",
        lambda _db=None, _task_id=None, ids=None, **kwargs: delete_calls.append(
            list(ids if ids is not None else kwargs["duplicate_attachment_ids"])
        ),
    )

    async def fail_if_called(*args, **kwargs):
        raise AssertionError(
            "sample creation should be skipped for an all-duplicate batch"
        )

    monkeypatch.setattr(router.sample_service, "create", fail_if_called)
    command = CreateSamplesWithImageCleaningCommand(
        samples=[],
        cleaning=ImageCleaningCommitCommand(
            attachment_ids=[1, 2],
            policy_revision=1,
            fingerprint="f" * 64,
        ),
    )

    response = asyncio.run(
        router.create_samples_with_image_cleaning(
            task_id=7,
            cmd=command,
            authorization=None,
            db=SimpleNamespace(),
            current_user=SimpleNamespace(id=1),
        )
    )

    assert response.data.ids == []
    assert response.data.deleted_attachment_ids == [1, 2]
    assert delete_calls == [[1, 2]]


def test_cleaning_route_reports_deleted_ids_when_sample_creation_fails(
    monkeypatch,
) -> None:
    preview = ImageCleaningPreviewResponse(
        active_preset="standard",
        threshold=0.95,
        policy_revision=1,
        algorithm_version="visual-v1",
        total_count=2,
        duplicate_count=1,
        keep_count=1,
        duplicate_attachment_ids=[2],
        keep_attachment_ids=[1],
        pairs=[{"keep_id": 1, "duplicate_id": 2, "similarity": 1.0}],
        fingerprint="f" * 64,
    )

    monkeypatch.setattr(
        router.cleaning_service,
        "validate_task_cleaning",
        lambda *args, **kwargs: ([1], preview),
    )
    monkeypatch.setattr(
        router.cleaning_service,
        "delete_task_cleaning_duplicates",
        lambda *args, **kwargs: None,
    )

    async def fail_create(*args, **kwargs):
        raise LabelUException(
            code=ErrorCode.CODE_50000_TASK_ERROR,
            status_code=500,
            message="样本服务暂时不可用",
        )

    monkeypatch.setattr(router.sample_service, "create", fail_create)

    command = CreateSamplesWithImageCleaningCommand(
        samples=[CreateSampleCommand(file_id=1, data={"result": "{}"})],
        cleaning=ImageCleaningCommitCommand(
            attachment_ids=[1, 2],
            policy_revision=1,
            fingerprint="f" * 64,
        ),
    )
    with pytest.raises(LabelUException) as captured:
        asyncio.run(
            router.create_samples_with_image_cleaning(
                task_id=7,
                cmd=command,
                authorization=None,
                db=SimpleNamespace(),
                current_user=SimpleNamespace(id=1),
            )
        )

    error = captured.value
    assert error.status_code == 500
    assert "重复图片已物理删除" in error.msg
    assert error.extra["data"] == {
        "cleaning_partial": True,
        "deleted_attachment_ids": [2],
        "deleted_count": 1,
        "kept_count": 1,
        "samples_created": False,
    }
