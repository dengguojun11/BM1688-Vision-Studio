import json

from labelu.internal.application.service import provider_job
from labelu.internal.application.service.provider_registry import ensure_seeded
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.ingestion import MediaAsset
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.provider_job import PredictionSet, ProviderJob
from labelu.internal.domain.models.provider_registry import ProviderRegistry
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import MediaType, Task


def _task(db, *, project_id: int | None):
    with begin_transaction(db):
        task = Task(
            name="legacy-bridge-task",
            media_type=MediaType.IMAGE.value,
            project_id=project_id,
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        sample = TaskSample(
            task_id=task.id,
            inner_id=1,
            data=json.dumps({"fileNames": ["sample.jpg"]}),
            created_by=1,
            updated_by=1,
        )
        db.add(sample)
        db.flush()
        db.refresh(task)
        db.refresh(sample)
        return task, sample


def _project(db):
    with begin_transaction(db):
        project = Project(name="legacy-bridge-project", owner_id=1, created_by=1, updated_by=1)
        db.add(project)
        db.flush()
        db.refresh(project)
        return project


def test_legacy_preannotation_creates_pending_provider_snapshot_without_formal_write(db):
    project = _project(db)
    task, sample = _task(db, project_id=project.id)
    payload = {"meta": {"source_type": "ai_generated"}, "items": [{"tool": "rectTool"}]}

    result = provider_job.bridge_legacy_preannotation(
        db,
        task_id=task.id,
        sample_id=sample.id,
        payload=payload,
        provider_key="vision_llm",
        created_by=1,
    )

    assert result is not None
    assert not hasattr(result, "project_id")
    assert result.sample_id == sample.id
    assert result.review_status == "PENDING"
    assert result.source == "LEGACY"
    assert db.query(ProviderJob).count() == 1
    assert db.query(PredictionSet).count() == 1
    assert db.query(TaskPreAnnotation).count() == 0


def test_legacy_preannotation_bridge_is_idempotent_for_same_payload(db):
    project = _project(db)
    task, sample = _task(db, project_id=project.id)
    payload = {"items": [{"label": "person", "score": 0.9}]}
    kwargs = dict(
        task_id=task.id,
        sample_id=sample.id,
        payload=payload,
        provider_key="tagtool_cls",
        created_by=1,
    )

    first = provider_job.bridge_legacy_preannotation(db, **kwargs)
    second = provider_job.bridge_legacy_preannotation(db, **kwargs)

    assert first is not None and second is not None
    assert first.id == second.id
    assert db.query(ProviderJob).count() == 1
    assert db.query(PredictionSet).count() == 1


def test_legacy_preannotation_bridge_supports_historical_task_without_project(db):
    task, sample = _task(db, project_id=None)

    result = provider_job.bridge_legacy_preannotation(
        db,
        task_id=task.id,
        sample_id=sample.id,
        payload={"items": []},
        provider_key="vision_llm",
        created_by=1,
    )

    assert result is not None
    assert db.query(ProviderJob).count() == 1
    assert db.query(PredictionSet).count() == 1


def test_legacy_preannotation_bridge_skips_disabled_provider(db):
    project = _project(db)
    task, sample = _task(db, project_id=project.id)
    ensure_seeded(db)
    with begin_transaction(db):
        provider = (
            db.query(ProviderRegistry)
            .filter(ProviderRegistry.provider_key == "vision_llm")
            .first()
        )
        assert provider is not None
        provider.status = "DISABLED"

    result = provider_job.bridge_legacy_preannotation(
        db,
        task_id=task.id,
        sample_id=sample.id,
        payload={"items": []},
        provider_key="vision_llm",
        created_by=1,
    )

    assert result is None
    assert db.query(ProviderJob).count() == 0
    assert db.query(PredictionSet).count() == 0


def test_legacy_bridge_carries_compatible_media_asset_and_model_lineage(db):
    project = _project(db)
    task, sample = _task(db, project_id=project.id)
    with begin_transaction(db):
        asset = MediaAsset(
            project_id=project.id, object_uri="file:///legacy.jpg", media_type="IMAGE",
            sha256="d" * 64, scan_status="PASSED", quality_status="PASSED",
            lifecycle_status="ACTIVE", created_by=1,
        )
        db.add(asset)
        db.flush()
        attachment = TaskAttachment(
            task_id=task.id, filename="legacy.jpg", path="legacy.jpg",
            media_asset_id=asset.id, created_by=1, updated_by=1,
        )
        db.add(attachment)
        db.flush()
        sample.file_id = attachment.id
    result = provider_job.bridge_legacy_preannotation(
        db, task_id=task.id, sample_id=sample.id,
        payload={"meta": {"model_version_id": 123}, "items": []},
        provider_key="vision_llm", created_by=1,
    )
    assert result is not None
    job = db.query(ProviderJob).one()
    assert json.loads(job.input_asset_ids_json) == [asset.id]
    assert job.model_version_id is None
    assert json.loads(job.config_json)["legacy_bridge"] is True
    lineage = json.loads(db.query(PredictionSet).one().metrics_json)["lineage"]
    assert lineage["provider_job_id"] == job.id
    assert lineage["input_asset_ids"] == [asset.id]
