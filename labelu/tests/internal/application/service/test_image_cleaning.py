"""Unit tests for the image-cleaning policy and preview helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from labelu.internal.application.command.image_cleaning import (
    PatchImageCleaningPolicyCommand,
)
from labelu.internal.application.command.image_cleaning_task import (
    CreateSamplesWithImageCleaningCommand,
    ImageCleaningCommitCommand,
    ImageCleaningPreviewCommand,
)
from labelu.internal.application.service import image_cleaning as service
from labelu.internal.application.service import image_cleaning_task as task_service
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.image_pipeline import staging
from labelu.internal.common.config import settings
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.media_upload import MediaUploadSession
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import MediaType, Task


def test_default_policy_has_three_bounded_presets() -> None:
    policy = service.normalize_policy()

    assert policy["active_preset"] == "standard"
    assert policy["revision"] == 1
    assert policy["algorithm_version"] == "visual-v1"
    assert policy["presets"] == {
        "simple": {"threshold": 0.98},
        "standard": {"threshold": 0.95},
        "strict": {"threshold": 0.90},
    }
    assert service.validate_policy(policy) == []


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("active_preset", "unknown", "active_preset_invalid"),
        ("presets", {"simple": {"threshold": 1.1}}, "preset_simple_threshold_invalid"),
        (
            "presets",
            {"simple": {"threshold": "nan"}},
            "preset_simple_threshold_invalid",
        ),
    ],
)
def test_policy_validation_rejects_invalid_values(
    field: str, value, reason: str
) -> None:
    raw = service.normalize_policy()
    raw[field] = value
    reasons = service.validate_policy(raw)
    assert reason in reasons


def test_patch_command_accepts_partial_policy() -> None:
    command = PatchImageCleaningPolicyCommand(
        active_preset="strict",
        presets={"strict": {"threshold": 0.9}},
    )
    assert command.active_preset == "strict"
    assert command.presets == {"strict": {"threshold": 0.9}}


def test_save_policy_uses_governance_provider_and_bumps_revision(monkeypatch) -> None:
    writes: list[tuple[str, dict]] = []
    current = service.DEFAULT_POLICY

    monkeypatch.setattr(
        service, "read_group_values", lambda _group: {"policy": current}
    )
    monkeypatch.setattr(service, "get_runtime_override", lambda _group, _item: object())

    def fake_write(group: str, values: dict, **kwargs) -> bool:
        writes.append((group, values))
        return True

    monkeypatch.setattr(service, "write_group_values", fake_write)
    saved = service.save_policy(
        PatchImageCleaningPolicyCommand(
            active_preset="strict",
            presets={"strict": {"threshold": 0.9}},
        )
    )

    assert saved["active_preset"] == "strict"
    assert saved["revision"] == 2
    assert writes[0][0] == service.IMAGE_CLEANING_POLICY_GROUP
    assert writes[0][1][service.IMAGE_CLEANING_POLICY_ITEM]["revision"] == 2


def test_save_identical_policy_keeps_revision(monkeypatch) -> None:
    writes: list[dict] = []
    current = service.normalize_policy()
    monkeypatch.setattr(
        service, "read_group_values", lambda _group: {"policy": current}
    )
    monkeypatch.setattr(service, "get_runtime_override", lambda _group, _item: object())
    monkeypatch.setattr(
        service, "write_group_values", lambda _group, values, **_: writes.append(values)
    )

    saved = service.save_policy({})

    assert saved["revision"] == 1
    assert writes[0]["policy"]["revision"] == 1


def test_save_ignores_client_revision_on_unchanged_policy(monkeypatch) -> None:
    writes: list[dict] = []
    current = service.normalize_policy()
    monkeypatch.setattr(
        service, "read_group_values", lambda _group: {"policy": current}
    )
    monkeypatch.setattr(service, "get_runtime_override", lambda _group, _item: object())
    monkeypatch.setattr(
        service, "write_group_values", lambda _group, values, **_: writes.append(values)
    )

    saved = service.save_policy({"revision": 99})

    assert saved["revision"] == 1
    assert writes[0]["policy"]["revision"] == 1


def test_save_policy_rejects_stale_expected_revision(monkeypatch) -> None:
    current = service.normalize_policy()
    monkeypatch.setattr(
        service, "read_group_values", lambda _group: {"policy": current}
    )
    monkeypatch.setattr(service, "get_runtime_override", lambda _group, _item: object())

    with pytest.raises(RuntimeError, match="已被其他管理人员更新"):
        service.save_policy({"expected_revision": 99})


def test_visual_similarity_and_preview_paths(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    different = tmp_path / "different.png"
    Image.new("RGB", (24, 24), (220, 20, 20)).save(first)
    Image.new("RGB", (24, 24), (220, 20, 20)).save(second)
    Image.new("RGB", (24, 24), (20, 20, 220)).save(different)

    assert service.visual_similarity(first, first) == 1.0
    assert 0.0 <= service.visual_similarity(first, different) <= 1.0

    # Keep the preview test deterministic while exercising the path-based
    # visual branch independently from OpenCV's histogram implementation.
    monkeypatch.setattr(
        service,
        "visual_similarity",
        lambda left, right: (
            0.1 if "different.png" in {Path(left).name, Path(right).name} else 0.97
        ),
    )
    preview = service.preview_paths(
        [
            {"id": 11, "path": first, "sha256": "sha-a"},
            {"id": 12, "path": second, "sha256": "sha-b"},
            {"id": 13, "path": different, "sha256": "sha-c"},
        ],
        0.95,
    )

    assert preview["total"] == 3
    assert preview["duplicate_ids"] == [12]
    assert preview["keep_ids"] == [11, 13]
    assert preview["deleted_count"] == 1
    assert preview["kept_count"] == 2
    assert preview["pairs"] == [{"keep_id": 11, "duplicate_id": 12, "similarity": 0.97}]
    assert len(preview["fingerprint"]) == 64


def test_remote_cleaning_path_is_materialized_as_a_temporary_file(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "remote.jpg"
    source.write_bytes(b"remote-image")

    class FakeRemoteStorage:
        is_remote = True

        def download_file(self, key: str, target_path: Path) -> None:
            assert key == "uploads/remote.jpg"
            target_path.write_bytes(source.read_bytes())

    attachment = SimpleNamespace(
        path="uploads/remote.jpg",
        filename="remote.jpg",
        data_source_id=None,
    )
    monkeypatch.setattr(
        task_service, "resolve_attachment_local_path", lambda _row: None
    )
    monkeypatch.setattr(
        task_service, "get_storage_backend", lambda: FakeRemoteStorage()
    )

    cleanup_paths: list[Path] = []
    materialized = task_service._resolve_cleaning_path(attachment, cleanup_paths)

    assert materialized is not None
    assert materialized.is_file()
    assert cleanup_paths == [materialized]
    for path in cleanup_paths:
        path.unlink(missing_ok=True)
    assert not materialized.exists()


def test_preview_skips_missing_and_referenced_attachments(
    db, tmp_path: Path, monkeypatch
) -> None:
    """A stale upload queue must not make the whole cleaning preview fail."""

    monkeypatch.setattr(settings, "MEDIA_ROOT", tmp_path)
    with begin_transaction(db):
        task = Task(
            name="cleaning-preview-skip",
            media_type=MediaType.IMAGE.value,
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        readable = TaskAttachment(
            task_id=task.id,
            filename="readable.jpg",
            path=f"{task.id}/readable.jpg",
            created_by=1,
            updated_by=1,
            sha256="c" * 64,
        )
        sample_attachment = TaskAttachment(
            task_id=task.id,
            filename="sample-reference.jpg",
            path=f"{task.id}/sample-reference.jpg",
            created_by=1,
            updated_by=1,
            sha256="c" * 64,
        )
        pre_annotation_attachment = TaskAttachment(
            task_id=task.id,
            filename="pre-reference.jpg",
            path=f"{task.id}/pre-reference.jpg",
            created_by=1,
            updated_by=1,
            sha256="c" * 64,
        )
        missing = TaskAttachment(
            task_id=task.id,
            filename="missing.jpg",
            path=f"{task.id}/missing.jpg",
            created_by=1,
            updated_by=1,
        )
        db.add_all([readable, sample_attachment, pre_annotation_attachment, missing])
        db.flush()
        db.add(
            TaskSample(
                task_id=task.id,
                file_id=sample_attachment.id,
                created_by=1,
                updated_by=1,
                data="{}",
            )
        )
        db.add(
            TaskPreAnnotation(
                task_id=task.id,
                sample_name="pre-reference.jpg",
                file_id=pre_annotation_attachment.id,
                created_by=1,
                updated_by=1,
                data="{}",
            )
        )
        task_id = int(task.id)
        readable_id = int(readable.id)
        sample_attachment_id = int(sample_attachment.id)
        pre_annotation_attachment_id = int(pre_annotation_attachment.id)
        missing_id = int(missing.id)

    for filename in ("readable.jpg", "sample-reference.jpg", "pre-reference.jpg"):
        path = tmp_path / str(task_id) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (24, 24), (220, 20, 20)).save(path)

    response = task_service.preview_task_images(
        db=db,
        task_id=task_id,
        cmd=ImageCleaningPreviewCommand(
            attachment_ids=[
                missing_id,
                sample_attachment_id,
                pre_annotation_attachment_id,
                readable_id,
            ]
        ),
        current_user=None,
    )

    assert response.candidate_attachment_ids == [readable_id]
    assert response.skipped_attachment_ids == [
        missing_id,
        sample_attachment_id,
        pre_annotation_attachment_id,
    ]
    assert response.skipped_count == 3
    assert response.total_count == 1
    assert response.duplicate_attachment_ids == [readable_id]
    assert response.keep_attachment_ids == []
    assert len(response.pairs) == 1
    assert response.pairs[0].model_dump() == {
        "keep_id": sample_attachment_id,
        "duplicate_id": readable_id,
        "similarity": 1.0,
    }


def test_preview_fingerprint_changes_with_revision_and_identity() -> None:
    items = [{"id": 1, "sha256": "abc"}]
    first = service.preview_paths(items, 0.95, revision=1)
    second = service.preview_paths(items, 0.95, revision=2)
    changed = service.preview_paths([{"id": 2, "sha256": "abc"}], 0.95, revision=1)

    assert first["fingerprint"] != second["fingerprint"]
    assert first["fingerprint"] != changed["fingerprint"]


def test_preview_fingerprint_uses_file_content_when_sha256_is_missing(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.jpg"
    second_path = tmp_path / "second.jpg"
    first_path.write_bytes(b"same-content")
    second_path.write_bytes(b"same-content")

    first = service.preview_paths(
        [{"id": 1, "path": first_path, "filename": first_path.name}], 0.95
    )
    second = service.preview_paths(
        [{"id": 1, "path": second_path, "filename": second_path.name}], 0.95
    )

    assert first["fingerprint"] == second["fingerprint"]


def test_cleaning_commit_allows_an_all_duplicate_batch() -> None:
    command = CreateSamplesWithImageCleaningCommand(
        samples=[],
        cleaning=ImageCleaningCommitCommand(
            attachment_ids=[1, 2],
            policy_revision=1,
            fingerprint="f" * 64,
        ),
    )

    assert command.samples == []
    assert task_service._normalized_ids([], allow_empty=True) == []


def test_cleaning_commit_rejects_duplicate_keep_ids(monkeypatch) -> None:
    preview = task_service.ImageCleaningPreviewResponse(
        active_preset="standard",
        threshold=0.95,
        policy_revision=1,
        algorithm_version="visual-v1",
        total_count=1,
        duplicate_count=0,
        keep_count=1,
        duplicate_attachment_ids=[],
        keep_attachment_ids=[1],
        pairs=[],
        fingerprint="f" * 64,
    )
    monkeypatch.setattr(task_service, "_preview", lambda *args, **kwargs: (preview, []))

    with pytest.raises(LabelUException, match="重复附件"):
        task_service.validate_task_cleaning(
            db=None,
            task_id=1,
            cmd=ImageCleaningCommitCommand(
                attachment_ids=[1],
                policy_revision=1,
                fingerprint="f" * 64,
            ),
            current_user=None,
            submitted_keep_ids=[1, 1],
        )


def test_physical_delete_clears_upload_session_and_storage(
    db, tmp_path: Path, monkeypatch
) -> None:
    source_path = tmp_path / "duplicate.jpg"
    source_path.write_bytes(b"duplicate")
    with begin_transaction(db):
        task = Task(
            name="cleaning-test",
            media_type=MediaType.IMAGE.value,
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        attachment = TaskAttachment(
            task_id=task.id,
            filename=source_path.name,
            path="1/duplicate.jpg",
            created_by=1,
            updated_by=1,
            sha256="a" * 64,
        )
        db.add(attachment)
        db.flush()
        upload = MediaUploadSession(
            session_id="cleaning-session",
            task_id=task.id,
            filename=source_path.name,
            total_size=source_path.stat().st_size,
            chunk_size=source_path.stat().st_size,
            total_chunks=1,
            received_chunks_json="[0]",
            status="COMPLETED",
            attachment_id=attachment.id,
            created_by=1,
        )
        db.add(upload)
        db.flush()
        task_id = int(task.id)
        attachment_id = int(attachment.id)
        upload_id = int(upload.id)

    class FakeStorage:
        def __init__(self):
            self.deleted: list[str] = []

        def delete(self, key: str) -> None:
            self.deleted.append(key)

        def exists(self, key: str) -> bool:
            return key.endswith("-thumbnail.jpg")

    storage = FakeStorage()
    monkeypatch.setattr(task_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_ENABLED", False)
    staged: list[tuple[int, list[int]]] = []
    monkeypatch.setattr(
        staging,
        "clear_staging_for_attachments",
        lambda task, ids: staged.append((task, list(ids))),
    )

    task_service._physically_delete(db, task_id, [attachment_id])

    db.expire_all()
    assert (
        db.query(TaskAttachment).filter(TaskAttachment.id == attachment_id).first()
        is None
    )
    upload_row = (
        db.query(MediaUploadSession).filter(MediaUploadSession.id == upload_id).one()
    )
    assert upload_row.attachment_id is None
    assert storage.deleted == ["1/duplicate.jpg", "1/duplicate-thumbnail.jpg"]
    assert staged == [(task_id, [attachment_id])]


def test_physical_delete_keeps_database_row_when_storage_delete_fails(
    db, monkeypatch
) -> None:
    with begin_transaction(db):
        task = Task(
            name="cleaning-storage-failure",
            media_type=MediaType.IMAGE.value,
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        attachment = TaskAttachment(
            task_id=task.id,
            filename="storage-failure.jpg",
            path="1/storage-failure.jpg",
            created_by=1,
            updated_by=1,
            sha256="b" * 64,
        )
        db.add(attachment)
        db.flush()
        task_id = int(task.id)
        attachment_id = int(attachment.id)

    class FailingStorage:
        def delete(self, _key: str) -> None:
            raise OSError("storage unavailable")

        def exists(self, _key: str) -> bool:
            return False

    monkeypatch.setattr(task_service, "get_storage_backend", lambda: FailingStorage())

    with pytest.raises(LabelUException, match="物理文件删除失败"):
        task_service._physically_delete(db, task_id, [attachment_id])

    db.expire_all()
    assert db.query(TaskAttachment).filter(TaskAttachment.id == attachment_id).one()
