import io
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from labelu.main import app
from labelu.internal.adapter.persistence import crud_annotation_taxonomy
from labelu.internal.application.service import (
    auto_sample_preannotate as auto_sample_service,
    experiment_dataset_import as import_service,
)
from labelu.internal.application.command.experiment_dataset_import import (
    CreateExperimentDatasetImportCommand,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.service_account import OPEN_SYSTEM_USERNAME
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.auto_label_job import AutoLabelJob
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.experiment_dataset_group import (
    ExperimentDatasetGroup,
)
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)


@pytest.fixture(autouse=True)
def _use_tmp_media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(import_service.settings, "MEDIA_ROOT", tmp_path)
    # Existing focused import cases use one or two samples; threshold behavior
    # is covered explicitly below with the production default overridden.
    monkeypatch.setattr(
        import_service.settings, "EXPERIMENT_DATASET_MIN_SAMPLE_COUNT", 1
    )


@pytest.fixture(autouse=True)
def _seed_import_dataset_category(db: Session, run_around_tests):
    with begin_transaction(db):
        if not db.query(DatasetCategory).filter(
            DatasetCategory.category_key == "illegalParking"
        ).first():
            db.add(
                DatasetCategory(
                    category_key="illegalParking",
                    name="非机动车违停",
                    status="active",
                )
            )


@pytest.fixture(autouse=True)
def _default_published_model_ready(monkeypatch):
    """Keep legacy fixture payloads focused on import mechanics.

    Dedicated model-resolution tests override this fixture explicitly.  The
    production path still requires a real published-model resolution.
    """
    monkeypatch.setattr(
        import_service.yolo_preannotate,
        "resolve_published_model",
        lambda db, published_model_id: {
            "ready": True,
            "model_id": str(published_model_id or ""),
            "labels": [],
        },
    )


def _payload(package_id: str = "experiment-dataset-1783148696144971776") -> dict:
    return {
        "schema_version": "v1",
        "package_id": package_id,
        "dataset_type": "reviewed_false_positive_daily",
        "dataset_date": "2026-07-03",
        "window_start": "2026-07-03 00:00:00",
        "window_end": "2026-07-04 00:00:00",
        "dataset": {
            "s3_uri": "s3://acity-artifacts/strategy-datasets/reviewed-false-positive/dt=2026-07-03/06ea712b2434fa18/",
            "bucket": "acity-artifacts",
            "prefix": "strategy-datasets/reviewed-false-positive/dt=2026-07-03/06ea712b2434fa18/",
            "image_prefix": "strategy-datasets/reviewed-false-positive/dt=2026-07-03/06ea712b2434fa18/images/",
            "image_s3_prefix": "s3://acity-artifacts/strategy-datasets/reviewed-false-positive/dt=2026-07-03/06ea712b2434fa18/images/",
            "metadata_key": "strategy-datasets/reviewed-false-positive/dt=2026-07-03/06ea712b2434fa18/metadata.json",
            "metadata_s3_uri": "s3://acity-artifacts/strategy-datasets/reviewed-false-positive/dt=2026-07-03/06ea712b2434fa18/metadata.json",
            "metadata_file": "metadata.json",
            "image_directory": "images/",
            "sample_alignment": "samples[].image.file_name matches the object name under dataset.image_prefix",
        },
        "labeling": {
            "required": True,
            "media_type": "IMAGE",
            "annotation_type": "bbox",
            "labels": ["electrombileWithoutHelmet"],
        },
        "target": {
            "device_type": "",
            "event_type": "electrombileWithoutHelmet",
            "model_id": "",
            "scene_type": "",
            "strategy_id": "",
        },
        "sample_count": 11,
        "target_boxes_required": True,
        "dataset_category_key": "illegalParking",
    }


def test_status_snapshot_remaining_is_based_on_required_threshold_not_declared_total():
    """累计请求的声明数不能把已经达到门槛的组显示成仍缺很多图片。"""
    row = SimpleNamespace(
        status=import_service.IMPORT_STATUS_WAITING_FOR_MODEL,
        formal_task_ids_json="[]",
        declared_sample_count=177,
        formal_sample_count=0,
        draft_sample_count=59,
        abnormal_sample_count=0,
        valid_sample_count=59,
        draft_task_id=6,
        metadata_json="{}",
        last_error=None,
    )
    group = SimpleNamespace(
        formal_task_id=None,
        declared_sample_count=177,
        valid_sample_count=59,
        min_sample_count=50,
        model_resolution_json=json.dumps(
            {
                "ready": False,
                "manual_confirmation_required": True,
                "reason": "published_model_not_found",
            }
        ),
    )

    snapshot = import_service._status_snapshot(row, group=group)

    assert snapshot["progress"]["declared_samples"] == 177
    assert snapshot["progress"]["valid_samples"] == 59
    assert snapshot["progress"]["required_samples"] == 50
    assert snapshot["progress"]["remaining_samples"] == 0


def test_status_snapshot_remaining_reports_shortfall_before_threshold():
    row = SimpleNamespace(
        status=import_service.IMPORT_STATUS_WAITING_FOR_SAMPLES,
        formal_task_ids_json="[]",
        declared_sample_count=22,
        formal_sample_count=0,
        draft_sample_count=0,
        abnormal_sample_count=0,
        valid_sample_count=22,
        draft_task_id=None,
        metadata_json="{}",
        last_error=None,
    )
    group = SimpleNamespace(
        formal_task_id=None,
        declared_sample_count=22,
        valid_sample_count=22,
        min_sample_count=50,
        model_resolution_json=None,
    )

    snapshot = import_service._status_snapshot(row, group=group)

    assert snapshot["progress"]["remaining_samples"] == 28


def _allow_published_model_for_import_test(monkeypatch) -> None:
    """Give legacy import tests an explicit, resolved model lineage."""
    # These tests verify import persistence, not a real model inference.  Keep
    # the durable preannotation row pending and avoid starting a background
    # inference thread with a synthetic model resolver result.
    monkeypatch.setattr(
        auto_sample_service, "_submit_auto_preannotate_job", lambda job_id: True
    )
    monkeypatch.setattr(
        import_service.yolo_preannotate,
        "resolve_published_model",
        lambda db, published_model_id: {
            "ready": True,
            "model_id": str(published_model_id),
            # Leave labels empty so the test focuses on its import behavior;
            # label-contract compatibility is covered by the dedicated model
            # resolution tests.
            "labels": [],
        },
    )


def test_aggregation_model_id_conflict_is_detected_before_task_creation():
    first = CreateExperimentDatasetImportCommand.model_validate(
        {**_payload("pkg-model-a"), "target": {**_payload("pkg-model-a")["target"], "model_id": "534"}}
    )
    second = CreateExperimentDatasetImportCommand.model_validate(
        {**_payload("pkg-model-b"), "target": {**_payload("pkg-model-b")["target"], "model_id": "535"}}
    )
    rows = [
        (SimpleNamespace(id=1), first, {}, 1, 0),
        (SimpleNamespace(id=2), second, {}, 1, 0),
    ]

    conflict = import_service._group_model_id_conflict(rows)

    assert conflict == {
        "reason": "aggregation_model_id_conflict",
        "model_ids": ["534", "535"],
    }


def test_external_task_id_is_trace_only_and_does_not_split_aggregation():
    first_payload = _payload("pkg-external-task-a")
    first_payload["source_external_task_id"] = "third-party-task-a"
    second_payload = _payload("pkg-external-task-b")
    second_payload["source_external_task_id"] = "third-party-task-b"
    first = CreateExperimentDatasetImportCommand.model_validate(first_payload)
    second = CreateExperimentDatasetImportCommand.model_validate(second_payload)

    first_key, first_identity = import_service._aggregation_key(first)
    second_key, second_identity = import_service._aggregation_key(second)

    assert first_key == second_key
    assert "source_external_task_id" not in first_identity
    assert "source_external_task_id" not in second_identity


def test_metadata_source_keys_are_stable_across_retries():
    cmd = CreateExperimentDatasetImportCommand.model_validate(_payload("pkg-source"))
    metadata = {
        "samples": [
            {"image": {"file_name": "one.jpg"}},
            {"image": {"file_name": "one.jpg"}},
        ]
    }

    keys = import_service._metadata_source_keys(cmd, metadata)

    assert keys == {
        f"{cmd.dataset.bucket}|{cmd.dataset.image_prefix}one.jpg"
    }


def test_aggregation_model_id_empty_rows_remain_category_compatible():
    first = CreateExperimentDatasetImportCommand.model_validate(_payload("pkg-empty-a"))
    second = CreateExperimentDatasetImportCommand.model_validate(_payload("pkg-empty-b"))
    rows = [
        (SimpleNamespace(id=1), first, {}, 1, 0),
        (SimpleNamespace(id=2), second, {}, 1, 0),
    ]

    assert import_service._group_model_id_conflict(rows) is None


def test_s3_source_identity_prevents_same_filename_path_reuse(db: Session):
    existing = TaskAttachment(
        task_id=901,
        path="upload/901/frame.jpg",
        url="/api/v1/tasks/attachment/upload/901/frame.jpg",
        filename="frame.jpg",
        source_bucket="bucket-a",
        source_object_key="images/frame.jpg",
        source_etag="etag-old",
        source_sha256="a" * 64,
    )
    db.add(existing)
    db.commit()

    different_object_path, same_source = import_service._source_aware_attachment_path(
        db,
        task_id=901,
        filename="frame.jpg",
        identity={
            "bucket": "bucket-a",
            "object_key": "images/frame.jpg",
            "version_id": "",
            "etag": "etag-new",
            "size_bytes": 1024,
        },
    )
    assert same_source is None
    assert different_object_path != existing.path
    assert "--" in different_object_path


def test_s3_same_source_identity_reuses_attachment(db: Session):
    existing = TaskAttachment(
        task_id=902,
        path="upload/902/frame.jpg",
        url="/api/v1/tasks/attachment/upload/902/frame.jpg",
        filename="frame.jpg",
        source_bucket="bucket-a",
        source_object_key="images/frame.jpg",
        source_etag="etag-same",
        source_sha256="b" * 64,
    )
    db.add(existing)
    db.commit()

    path, same_source = import_service._source_aware_attachment_path(
        db,
        task_id=902,
        filename="frame.jpg",
        identity={
            "bucket": "bucket-a",
            "object_key": "images/frame.jpg",
            "version_id": "",
            "etag": "etag-same",
            "size_bytes": 1024,
        },
    )
    assert path == existing.path
    assert same_source is not None
    assert same_source.id == existing.id
    reused = import_service._get_or_create_attachment(
        db,
        task_id=902,
        datasource_id=7,
        object_key="images/frame.jpg",
        local_relative_path=path,
        filename="frame.jpg",
        current_user=SimpleNamespace(id=1),
        source_bucket="bucket-a",
        source_object_key="images/frame.jpg",
        source_etag="etag-same",
        source_sha256="b" * 64,
        source_size_bytes=1024,
    )
    assert reused.id == existing.id


def test_resolve_formal_preset_uses_event_semantics_before_label_uniqueness():
    riding = SimpleNamespace(
        slug="ridingNoHelmet",
        items=[SimpleNamespace(label_value="person_electricbike")],
    )
    fall = SimpleNamespace(
        slug="fallDetection",
        items=[SimpleNamespace(label_value="person_electricbike")],
    )
    preset_index = [(riding, {"person_electricbike"}), (fall, {"person_electricbike"})]

    assert (
        import_service._resolve_formal_preset(
            preset_index,
            ["person_electricbike"],
            event_type="electrombileWithoutHelmet",
        )
        is riding
    )
    assert (
        import_service._resolve_formal_preset(
            preset_index,
            ["person_electricbike"],
            event_type="unknownEvent",
        )
        is None
    )


class TestExperimentDatasetImportRouter:
    def test_row_processing_stops_before_late_side_effect_after_lease_loss(
        self, db: Session, monkeypatch
    ) -> None:
        owner = db.query(User).order_by(User.id.asc()).first()
        assert owner is not None
        payload = _payload("pkg-lease-loss-side-effect")
        payload["dataset"]["image_prefix"] = "images/"
        row = SimpleNamespace(
            id=901,
            source_payload_json=json.dumps(payload),
            draft_task_id=None,
            formal_task_ids_json="[]",
            source_integration_id=None,
        )
        metadata = {
            "samples": [
                {"image": {"file_name": "first.jpg"}, "target_boxes": []},
                {"image": {"file_name": "second.jpg"}, "target_boxes": []},
            ]
        }
        monkeypatch.setattr(import_service, "_import_owner", lambda db, cmd: owner)
        monkeypatch.setattr(
            import_service,
            "_get_or_create_datasource",
            lambda *args, **kwargs: SimpleNamespace(id=1, bucket="bucket"),
        )
        monkeypatch.setattr(import_service, "_read_metadata_json", lambda *args: metadata)
        monkeypatch.setattr(
            import_service,
            "_list_image_object_keys",
            lambda *args: {"images/first.jpg", "images/second.jpg"},
        )
        monkeypatch.setattr(import_service, "_load_preset_index", lambda db: [])
        monkeypatch.setattr(
            import_service,
            "_get_or_create_draft_task",
            lambda *args, **kwargs: 77,
        )
        monkeypatch.setattr(import_service.crud_task, "get", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            import_service,
            "_download_s3_image_with_resume",
            lambda **kwargs: None,
        )
        attachment_names: list[str] = []

        def fake_attachment(*args, **kwargs):
            attachment_names.append(str(kwargs["filename"]))
            return SimpleNamespace(id=len(attachment_names))

        monkeypatch.setattr(import_service, "_get_or_create_attachment", fake_attachment)
        monkeypatch.setattr(
            import_service,
            "_attach_sample_if_missing",
            lambda *args, **kwargs: (True, int(kwargs["attachment"].id)),
        )

        checks = {"count": 0}

        def lease_guard() -> bool:
            checks["count"] += 1
            return checks["count"] < 11

        with pytest.raises(import_service.ImportWorkerLeaseLost):
            import_service._process_import_row(
                db,
                row=row,
                effective_dataset_category_id=1,
                lease_guard=lease_guard,
            )

        assert attachment_names == ["first.jpg"]
        assert checks["count"] >= 11

    def test_worker_completion_updates_legacy_projection_and_task_lineage(
        self, db: Session
    ) -> None:
        """旧策略表只接收完成态投影，不能替代导入真源。"""

        owner = db.query(User).order_by(User.id.asc()).first()
        assert owner is not None
        with begin_transaction(db):
            integration = StrategyTaskIntegration(
                external_task_id="legacy-projection-task",
                task_name="legacy-projection-task",
                task_type="threshold_experiment",
                dataset_s3_uri="s3://bucket/prefix/",
                dataset_image_prefix="prefix/images/",
                metadata_key="prefix/metadata.json",
                bucket="bucket",
                prefix="prefix/",
                created_by=owner.id,
                updated_by=owner.id,
                import_status="PENDING",
            )
            db.add(integration)
            db.flush()
            task = Task(
                name="formal-task",
                description="formal-task",
                media_type="IMAGE",
                status="IMPORTED",
                created_by=owner.id,
                updated_by=owner.id,
                source_type="third_party",
                source_external_task_id="legacy-projection-task",
            )
            db.add(task)
            db.flush()
            row = ExperimentDatasetImport(
                package_id="legacy-projection-package",
                source_type="third_party",
                source_system="strategy",
                source_external_task_id="legacy-projection-task",
                source_integration_id=integration.id,
                dataset_type="problem",
                dataset_date="2026-09-19",
                event_type="helmet",
                model_id="534",
                target_signature="signature",
                source_payload_json="{}",
                status="PROCESSING",
                formal_task_ids_json="[]",
                updated_by=owner.id,
            )
            db.add(row)
            db.flush()

        import_service._sync_legacy_strategy_projection(
            db,
            row=row,
            status=import_service.IMPORT_STATUS_COMPLETED,
            formal_task_ids=[int(task.id)],
            draft_task_id=None,
            formal_sample_count=3,
            draft_sample_count=0,
            metadata={"samples": [{"file_name": "problem.jpg"}]},
        )

        db.expire_all()
        saved_integration = db.query(StrategyTaskIntegration).one()
        saved_task = db.query(Task).filter(Task.id == task.id).one()
        assert saved_integration.import_status == import_service.IMPORT_STATUS_COMPLETED
        assert saved_integration.labelu_task_id == task.id
        assert saved_integration.imported_sample_count == 3
        assert json.loads(saved_integration.metadata_json)["samples"][0]["file_name"] == (
            "problem.jpg"
        )
        assert saved_task.source_integration_id == integration.id

    def test_accepts_internal_experiment_dataset_notification(self) -> None:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/internal/v1/experiment-datasets", json=_payload())

        assert response.status_code == 202
        body = response.json()["data"]
        assert body["message"] == "专家回流数据集导入任务已受理"
        assert body["accepted"] is True
        assert body["completed"] is False
        assert body["success"] is None
        assert body["status_label"] == "已受理"
        assert body["stage"] == "QUEUED"
        assert body["progress"]["declared_samples"] == 11
        assert body["progress"]["imported_samples"] == 0
        assert body["status_url"] == "/internal/v1/experiment-datasets/experiment-dataset-1783148696144971776"
        assert body["last_error"] is None
        assert body["package_id"] == "experiment-dataset-1783148696144971776"
        assert body["status"] == "PENDING"
        assert isinstance(body["import_job_id"], int)
        assert body["dataset_type"] == "reviewed_false_positive_daily"
        assert body["dataset_date"] == "2026-07-03"
        assert body["window_start"] == "2026-07-03 00:00:00"
        assert body["window_end"] == "2026-07-04 00:00:00"
        assert body["target"]["event_type"] == "electrombileWithoutHelmet"
        assert body["declared_sample_count"] == 11

    def test_default_threshold_waits_without_creating_task(
        self, client, db: Session, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            import_service.settings, "EXPERIMENT_DATASET_MIN_SAMPLE_COUNT", 20
        )
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {"file_name": "only.jpg", "width": 64, "height": 48},
                        "target_boxes": [],
                    }
                ]
            },
            image_keys=["only.jpg"],
        )

        response = client.post(
            "/internal/v1/experiment-datasets",
            json=_payload("pkg-threshold-20"),
        )
        assert response.status_code == 202
        import_service.run_import_job_now(response.json()["data"]["import_job_id"])

        db.expire_all()
        row = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-threshold-20")
            .one()
        )
        group = db.query(ExperimentDatasetGroup).one()
        assert row.status == import_service.IMPORT_STATUS_WAITING_FOR_SAMPLES
        assert group.status == import_service.IMPORT_STATUS_WAITING_FOR_SAMPLES
        assert group.valid_sample_count == 1
        assert group.min_sample_count == 20
        persisted_metadata = json.loads(row.metadata_json)
        assert persisted_metadata[import_service._INTERNAL_VALID_SOURCE_KEYS]
        assert persisted_metadata[import_service._INTERNAL_VALID_SOURCE_HASHES]
        assert db.query(Task).count() == 0

    def test_packages_are_aggregated_into_one_task_after_threshold(
        self, client, db: Session, monkeypatch
    ) -> None:
        _allow_published_model_for_import_test(monkeypatch)
        monkeypatch.setattr(
            import_service.settings, "EXPERIMENT_DATASET_MIN_SAMPLE_COUNT", 20
        )
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        owner = db.query(User).filter(User.username == "admin@123.com").one()
        with begin_transaction(db):
            marker = Task(
                name="aggregate-marker",
                description="aggregate-marker",
                media_type="IMAGE",
                status="IMPORTED",
                created_by=owner.id,
                updated_by=owner.id,
            )
            db.add(marker)
            db.flush()
            marker_id = int(marker.id)

        valid_by_package = {"pkg-aggregate-1": 10, "pkg-aggregate-2": 10}
        process_calls: list[str] = []

        def fake_inspect(db, *, row):
            cmd = CreateExperimentDatasetImportCommand.model_validate(
                json.loads(row.source_payload_json)
            )
            count = valid_by_package[row.package_id]
            return cmd, {"samples": []}, count, 0

        def fake_process(db, *, row, **kwargs):
            process_calls.append(row.package_id)
            return (
                import_service.IMPORT_STATUS_COMPLETED,
                valid_by_package[row.package_id],
                0,
                0,
                [marker_id],
                None,
                None,
                {"samples": []},
            )

        monkeypatch.setattr(import_service, "_inspect_import_row", fake_inspect)
        monkeypatch.setattr(import_service, "_process_import_row", fake_process)

        first_payload = _payload("pkg-aggregate-1")
        first_payload["target"]["model_id"] = "test-model-aggregate"
        first_payload["aggregation_key"] = "same-experiment-window"
        first_payload["sample_count"] = 10
        second_payload = _payload("pkg-aggregate-2")
        second_payload["target"]["model_id"] = "test-model-aggregate"
        second_payload["aggregation_key"] = "same-experiment-window"
        second_payload["sample_count"] = 10

        first = client.post(
            "/internal/v1/experiment-datasets", json=first_payload
        )
        assert first.status_code == 202
        import_service.run_import_job_now(first.json()["data"]["import_job_id"])
        assert process_calls == []

        second = client.post(
            "/internal/v1/experiment-datasets", json=second_payload
        )
        assert second.status_code == 202
        import_service.run_import_job_now(second.json()["data"]["import_job_id"])

        db.expire_all()
        group = (
            db.query(ExperimentDatasetGroup)
            .filter(
                ExperimentDatasetGroup.aggregation_key
                == "same-experiment-window"
            )
            .one()
        )
        rows = (
            db.query(ExperimentDatasetImport)
            .filter(
                ExperimentDatasetImport.aggregation_key
                == "same-experiment-window"
            )
            .all()
        )
        assert group.status == import_service.GROUP_STATUS_CREATED
        assert group.valid_sample_count == 20
        assert group.formal_task_id == marker_id
        assert sorted(process_calls) == ["pkg-aggregate-1", "pkg-aggregate-2"]
        assert {row.status for row in rows} == {
            import_service.IMPORT_STATUS_COMPLETED
        }
        assert db.query(Task).count() == 1

    def test_aggregate_threshold_deduplicates_content_hashes_across_packages(
        self, client, db: Session, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            import_service.settings, "EXPERIMENT_DATASET_MIN_SAMPLE_COUNT", 4
        )
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        hashes_by_package = {
            "pkg-hash-1": ["a" * 64, "b" * 64],
            "pkg-hash-2": ["b" * 64, "c" * 64],
        }

        def fake_inspect(db, *, row):
            cmd = CreateExperimentDatasetImportCommand.model_validate(
                json.loads(row.source_payload_json)
            )
            hashes = hashes_by_package[row.package_id]
            metadata = {
                import_service._INTERNAL_VALID_SOURCE_KEYS: [
                    f"bucket|{row.package_id}-1.jpg",
                    f"bucket|{row.package_id}-2.jpg",
                ],
                import_service._INTERNAL_VALID_SOURCE_HASHES: hashes,
            }
            return cmd, metadata, len(hashes), 0

        monkeypatch.setattr(import_service, "_inspect_import_row", fake_inspect)
        first_payload = _payload("pkg-hash-1")
        first_payload["aggregation_key"] = "same-hash-group"
        first = client.post("/internal/v1/experiment-datasets", json=first_payload)
        assert first.status_code == 202
        import_service.run_import_job_now(first.json()["data"]["import_job_id"])

        second_payload = _payload("pkg-hash-2")
        second_payload["aggregation_key"] = "same-hash-group"
        second = client.post("/internal/v1/experiment-datasets", json=second_payload)
        assert second.status_code == 202
        import_service.run_import_job_now(second.json()["data"]["import_job_id"])

        db.expire_all()
        group = (
            db.query(ExperimentDatasetGroup)
            .filter(ExperimentDatasetGroup.aggregation_key == "same-hash-group")
            .one()
        )
        assert group.status == import_service.GROUP_STATUS_WAITING_FOR_SAMPLES
        assert group.valid_sample_count == 3
        first_row = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-hash-1")
            .one()
        )
        assert import_service._INTERNAL_VALID_SOURCE_HASHES in json.loads(
            first_row.metadata_json
        )

    def test_failed_package_is_not_reprocessed_by_later_aggregate_package(
        self, client, db: Session, monkeypatch
    ) -> None:
        """A terminal failed package stays immutable when its group receives a new package."""
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        process_calls: list[str] = []

        def fake_inspect(db, *, row):
            cmd = CreateExperimentDatasetImportCommand.model_validate(
                json.loads(row.source_payload_json)
            )
            return (
                cmd,
                {
                    import_service._INTERNAL_VALID_SOURCE_KEYS: [
                        f"bucket|{row.package_id}.jpg"
                    ],
                    import_service._INTERNAL_VALID_SOURCE_HASHES: [
                        ("a" if row.package_id.endswith("1") else "b") * 64
                    ],
                },
                1,
                0,
            )

        def fake_process(db, *, row, **kwargs):
            process_calls.append(row.package_id)
            return (
                import_service.IMPORT_STATUS_COMPLETED,
                1,
                0,
                0,
                [],
                None,
                None,
                {"samples": []},
            )

        monkeypatch.setattr(import_service, "_inspect_import_row", fake_inspect)
        monkeypatch.setattr(import_service, "_process_import_row", fake_process)

        first_payload = _payload("pkg-failed-aggregate-1")
        first_payload["aggregation_key"] = "failed-package-group"
        first_payload["target"]["model_id"] = "test-model-failed-aggregate"
        first = client.post("/internal/v1/experiment-datasets", json=first_payload)
        assert first.status_code == 202

        db.expire_all()
        first_row = (
            db.query(ExperimentDatasetImport)
            .filter(
                ExperimentDatasetImport.package_id == "pkg-failed-aggregate-1"
            )
            .one()
        )
        group = (
            db.query(ExperimentDatasetGroup)
            .filter(ExperimentDatasetGroup.aggregation_key == "failed-package-group")
            .one()
        )
        with begin_transaction(db):
            first_row.status = import_service.IMPORT_STATUS_FAILED
            first_row.valid_sample_count = 1
            first_row.metadata_json = json.dumps(
                {
                    import_service._INTERNAL_VALID_SOURCE_KEYS: [
                        "bucket|pkg-failed-aggregate-1.jpg"
                    ]
                },
                ensure_ascii=False,
            )
            first_row.finished_at = datetime.now()
            group.status = import_service.GROUP_STATUS_FAILED

        second_payload = _payload("pkg-failed-aggregate-2")
        second_payload["aggregation_key"] = "failed-package-group"
        second_payload["target"]["model_id"] = "test-model-failed-aggregate"
        second = client.post("/internal/v1/experiment-datasets", json=second_payload)
        assert second.status_code == 202
        import_service.run_import_job_now(second.json()["data"]["import_job_id"])

        db.expire_all()
        assert process_calls == ["pkg-failed-aggregate-2"]
        first_row = (
            db.query(ExperimentDatasetImport)
            .filter(
                ExperimentDatasetImport.package_id == "pkg-failed-aggregate-1"
            )
            .one()
        )
        second_row = (
            db.query(ExperimentDatasetImport)
            .filter(
                ExperimentDatasetImport.package_id == "pkg-failed-aggregate-2"
            )
            .one()
        )
        assert first_row.status == import_service.IMPORT_STATUS_FAILED
        assert second_row.status == import_service.IMPORT_STATUS_COMPLETED

    def test_missing_published_model_creates_draft_without_fallback(
        self, client, db: Session, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            import_service.settings, "EXPERIMENT_DATASET_MIN_SAMPLE_COUNT", 20
        )
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        monkeypatch.setattr(
            import_service,
            "_inspect_import_row",
            lambda db, row: (
                CreateExperimentDatasetImportCommand.model_validate(
                    json.loads(row.source_payload_json)
                ),
                {"samples": []},
                20,
                0,
            ),
        )
        monkeypatch.setattr(
            import_service.yolo_preannotate,
            "resolve_published_model",
            lambda db, published_model_id: {
                "ready": False,
                "model_id": published_model_id,
                "reason": "published_model_mapping_missing",
            },
        )
        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {"file_name": "unknown-model.jpg", "width": 64, "height": 48},
                        "target_boxes": [],
                    }
                ]
            },
            image_keys=["unknown-model.jpg"],
        )
        payload = _payload("pkg-model-wait")
        payload["target"]["model_id"] = "541"
        response = client.post(
            "/internal/v1/experiment-datasets", json=payload
        )
        assert response.status_code == 202
        import_service.run_import_job_now(response.json()["data"]["import_job_id"])

        db.expire_all()
        row = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-model-wait")
            .one()
        )
        group = (
            db.query(ExperimentDatasetGroup)
            .filter(ExperimentDatasetGroup.id == row.aggregation_group_id)
            .one()
        )
        assert row.status == import_service.IMPORT_STATUS_WAITING_FOR_MODEL
        assert group.status == import_service.GROUP_STATUS_WAITING_FOR_MODEL
        resolution = json.loads(group.model_resolution_json)
        assert resolution["reason"] == (
            "published_model_mapping_missing"
        )
        assert resolution["manual_confirmation_required"] is True
        assert resolution["preannotation_allowed"] is False
        assert row.draft_task_id is not None
        assert db.query(Task).count() == 1
        assert db.query(TaskSample).count() == 1
        assert db.query(TaskPreAnnotation).count() == 0
        detail = client.get("/internal/v1/experiment-datasets/pkg-model-wait")
        assert detail.status_code == 200
        detail_payload = detail.json()["data"]
        assert detail_payload["status_label"] == "待人工确认模型"
        assert "不会自动预标注" in detail_payload["status_detail"]

    def test_missing_model_id_creates_waiting_draft_without_auto_preannotation(
        self, client, db: Session, monkeypatch
    ) -> None:
        """没有模型编号时，达到门槛也必须等待人工确认模型。"""
        monkeypatch.setattr(
            import_service.settings, "EXPERIMENT_DATASET_MIN_SAMPLE_COUNT", 20
        )
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        monkeypatch.setattr(
            import_service,
            "_inspect_import_row",
            lambda db, row: (
                CreateExperimentDatasetImportCommand.model_validate(
                    json.loads(row.source_payload_json)
                ),
                {"samples": []},
                20,
                0,
            ),
        )
        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {"file_name": "missing-model.jpg", "width": 64, "height": 48},
                        "target_boxes": [],
                    }
                ]
            },
            image_keys=["missing-model.jpg"],
        )
        payload = _payload("pkg-model-id-missing")
        payload["sample_count"] = 20
        payload["target"]["model_id"] = ""
        payload["source_type"] = "third_party"
        payload["source_system"] = "strategy_evolution"
        response = client.post(
            "/internal/v1/experiment-datasets", json=payload
        )
        assert response.status_code == 202
        import_service.run_import_job_now(response.json()["data"]["import_job_id"])

        db.expire_all()
        row = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-model-id-missing")
            .one()
        )
        group = (
            db.query(ExperimentDatasetGroup)
            .filter(ExperimentDatasetGroup.id == row.aggregation_group_id)
            .one()
        )
        assert row.status == import_service.IMPORT_STATUS_WAITING_FOR_MODEL
        assert group.status == import_service.GROUP_STATUS_WAITING_FOR_MODEL
        resolution = json.loads(group.model_resolution_json)
        assert resolution["reason"] == "model_id_missing"
        assert resolution["manual_confirmation_required"] is True
        assert resolution["preannotation_allowed"] is False
        assert row.draft_task_id is not None
        assert db.query(TaskSample).count() == 1
        assert db.query(TaskPreAnnotation).count() == 0
        assert db.query(AutoLabelJob).count() == 0

    def test_third_party_import_is_owned_by_open_system_when_accepted(
        self,
        client,
        db: Session,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )

        response = client.post(
            "/internal/v1/experiment-datasets",
            json=_payload("pkg-third-party-owner"),
        )

        assert response.status_code == 202
        row = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-third-party-owner")
            .one()
        )
        owner = db.query(User).filter(User.id == row.created_by).one()
        assert row.source_type == "third_party"
        assert owner.username == OPEN_SYSTEM_USERNAME
        assert row.updated_by == owner.id

    def test_run_import_job_creates_formal_and_draft_tasks(
        self,
        client,
        db: Session,
        monkeypatch,
    ) -> None:
        _allow_published_model_for_import_test(monkeypatch)
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        monkeypatch.setattr(
            auto_sample_service,
            "_submit_auto_preannotate_job",
            lambda job_id: True,
        )

        with begin_transaction(db):
            crud_annotation_taxonomy.label_preset_create(
                db=db,
                slug="ridingNoHelmet",
                name="骑行未戴头盔",
                description=None,
                sort_order=1,
                items=[
                    {
                        "label_key": "person_electricbike",
                        "label_value": "person_electricbike",
                        "color": "#ff0000",
                        "sort_order": 1,
                    }
                ],
            )
            crud_annotation_taxonomy.label_preset_create(
                db=db,
                slug="fallDetection",
                name="跌倒检测",
                description=None,
                sort_order=2,
                items=[
                    {
                        "label_key": "person_electricbike",
                        "label_value": "person_electricbike",
                        "color": "#00ff00",
                        "sort_order": 1,
                    }
                ],
            )

        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {
                            "file_name": "matched.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 10,
                                "y1": 20,
                                "x2": 110,
                                "y2": 220,
                                "class_name": "person_electricbike",
                            }
                        ],
                    },
                    {
                        "image": {
                            "file_name": "draft.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 30,
                                "y1": 40,
                                "x2": 130,
                                "y2": 260,
                                "class_name": "not_in_labelu",
                            }
                        ],
                    },
                ]
            },
            image_keys=["matched.jpg", "draft.jpg"],
        )

        payload = _payload("pkg-route-1")
        payload["target"]["model_id"] = "test-model-route"
        response = client.post(
            "/internal/v1/experiment-datasets", json=payload
        )

        assert response.status_code == 202
        assert response.json()["data"]["dataset_category_key"] == "illegalParking"
        import_job_id = response.json()["data"]["import_job_id"]

        import_service.run_import_job_now(import_job_id)

        db.expire_all()
        job = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-route-1")
            .first()
        )
        assert job is not None
        assert job.status == "COMPLETED"
        assert job.formal_sample_count == 1
        assert job.draft_sample_count == 1
        assert job.abnormal_sample_count == 0
        assert job.draft_task_id is not None
        formal_task_ids = json.loads(job.formal_task_ids_json)
        assert len(formal_task_ids) == 1

        category = (
            db.query(DatasetCategory)
            .filter(DatasetCategory.category_key == "illegalParking")
            .one()
        )
        assert job.dataset_category_id == category.id
        group = (
            db.query(ExperimentDatasetGroup)
            .filter(ExperimentDatasetGroup.id == job.aggregation_group_id)
            .one()
        )
        assert group.dataset_category_id == category.id

        formal_task = db.query(Task).filter(Task.id == formal_task_ids[0]).first()
        draft_task = db.query(Task).filter(Task.id == job.draft_task_id).first()
        assert formal_task is not None
        assert draft_task is not None
        assert formal_task.dataset_category_id == category.id
        assert draft_task.dataset_category_id == category.id
        assert (
            formal_task.name
            == "专家回流-electrombileWithoutHelmet-骑行未戴头盔-dt=2026-07-03"
        )
        assert (
            draft_task.name
            == "专家回流草稿-electrombileWithoutHelmet-dt=2026-07-03-pkg-route-1"
        )
        assert "defaultRectPresetId" not in (formal_task.config or "")
        assert formal_task.current_annotation_contract_id is not None

        formal_attachments = (
            db.query(TaskAttachment)
            .filter(
                TaskAttachment.task_id == formal_task.id,
                TaskAttachment.deleted_at.is_(None),
            )
            .all()
        )
        draft_attachments = (
            db.query(TaskAttachment)
            .filter(
                TaskAttachment.task_id == draft_task.id,
                TaskAttachment.deleted_at.is_(None),
            )
            .all()
        )
        assert [row.filename for row in formal_attachments] == ["matched.jpg"]
        assert [row.filename for row in draft_attachments] == ["draft.jpg"]
        assert formal_attachments[0].data_source_id is None
        assert draft_attachments[0].data_source_id is None
        assert formal_attachments[0].path == f"upload/{formal_task.id}/matched.jpg"
        assert draft_attachments[0].path == f"upload/{draft_task.id}/draft.jpg"
        assert (
            import_service.settings.MEDIA_ROOT / formal_attachments[0].path
        ).is_file()
        assert (
            import_service.settings.MEDIA_ROOT / draft_attachments[0].path
        ).is_file()

        formal_samples = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id == formal_task.id, TaskSample.deleted_at.is_(None)
            )
            .all()
        )
        draft_samples = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id == draft_task.id, TaskSample.deleted_at.is_(None)
            )
            .all()
        )
        assert len(formal_samples) == 1
        assert len(draft_samples) == 1

        formal_pre_annotations = (
            db.query(TaskPreAnnotation)
            .filter(
                TaskPreAnnotation.task_id == formal_task.id,
                TaskPreAnnotation.deleted_at.is_(None),
            )
            .all()
        )
        draft_pre_annotations = (
            db.query(TaskPreAnnotation)
            .filter(
                TaskPreAnnotation.task_id == draft_task.id,
                TaskPreAnnotation.deleted_at.is_(None),
            )
            .all()
        )
        assert len(formal_pre_annotations) == 1
        assert len(draft_pre_annotations) == 0

        auto_jobs = (
            db.query(AutoLabelJob)
            .filter(
                AutoLabelJob.task_id.in_([formal_task.id, draft_task.id]),
                AutoLabelJob.job_kind == "AUTO_SAMPLE_PREANNOTATE",
            )
            .order_by(AutoLabelJob.task_id.asc())
            .all()
        )
        assert len(auto_jobs) == 2
        expected_sample_ids = {
            formal_task.id: [formal_samples[0].id],
            draft_task.id: [draft_samples[0].id],
        }
        for auto_job in auto_jobs:
            payload = json.loads(auto_job.payload_json)
            assert payload["sample_ids"] == expected_sample_ids[auto_job.task_id]
            assert auto_job.status == "PENDING"
            assert auto_job.source_key == (
                f"experiment_dataset_import:{import_job_id}:task:{auto_job.task_id}"
            )

        detail_response = client.get("/internal/v1/experiment-datasets/pkg-route-1")
        assert detail_response.status_code == 200
        auto_progress = detail_response.json()["data"]["progress"]["auto_preannotation"]
        assert auto_progress["status"] == "PENDING"
        assert auto_progress["sample_count"] == 2
        assert auto_progress["processed_count"] == 0
        assert auto_progress["job_ids"] == [job.id for job in auto_jobs]

        pre_annotation_payload = json.loads(formal_pre_annotations[0].data)
        rects = pre_annotation_payload["annotations"]["rectTool"]["result"]
        assert len(rects) == 1
        assert rects[0]["label"] == "person_electricbike"
        assert rects[0]["x"] == pytest.approx(8.708333333333334)
        assert rects[0]["y"] == pytest.approx(17.425925925925927)
        assert rects[0]["width"] == pytest.approx(87.08333333333333)
        assert rects[0]["height"] == pytest.approx(174.25925925925927)
        assert pre_annotation_payload["meta"]["width"] == 1672
        assert pre_annotation_payload["meta"]["height"] == 941

    def test_missing_category_waits_without_creating_tasks(
        self, client, db: Session, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {
                            "file_name": "uncategorized.jpg",
                            "width": 64,
                            "height": 48,
                        },
                        "target_boxes": [],
                    }
                ]
            },
            image_keys=["uncategorized.jpg"],
        )
        payload = _payload("pkg-category-wait")
        payload.pop("dataset_category_key")
        payload["target"]["model_id"] = ""

        response = client.post(
            "/internal/v1/experiment-datasets", json=payload
        )
        assert response.status_code == 202
        assert response.json()["data"]["dataset_category_id"] is None
        import_job_id = response.json()["data"]["import_job_id"]

        import_service.run_import_job_now(import_job_id)

        db.expire_all()
        row = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-category-wait")
            .one()
        )
        group = (
            db.query(ExperimentDatasetGroup)
            .filter(ExperimentDatasetGroup.id == row.aggregation_group_id)
            .one()
        )
        assert row.status == import_service.IMPORT_STATUS_WAITING_FOR_CATEGORY
        assert group.status == import_service.GROUP_STATUS_WAITING_FOR_CATEGORY
        assert row.formal_task_ids_json in (None, "[]")
        assert row.draft_task_id is None

    def test_model_lineage_category_is_used_when_payload_key_is_blank(
        self, db: Session, monkeypatch
    ) -> None:
        category = (
            db.query(DatasetCategory)
            .filter(DatasetCategory.category_key == "illegalParking")
            .one()
        )
        monkeypatch.setattr(
            import_service,
            "_published_model_dataset_category",
            lambda db, published_model_id: (
                category,
                {
                    "category_resolution": "model_lineage",
                    "published_model_id": published_model_id,
                },
            ),
        )
        command = CreateExperimentDatasetImportCommand.model_validate(
            {
                **_payload("pkg-model-category"),
                "dataset_category_key": "",
                "target": {**_payload("pkg-model-category")["target"], "model_id": "563"},
            }
        )

        resolved, source, details = import_service._resolve_effective_dataset_category(
            db, command
        )
        assert resolved.id == category.id
        assert source == "model_lineage"
        assert details["category_resolution"] == "model_lineage"

    def test_explicit_category_conflict_with_model_lineage_is_rejected(
        self, client, db: Session, monkeypatch
    ) -> None:
        with begin_transaction(db):
            other = DatasetCategory(
                category_key="otherCategory",
                name="other",
                status="active",
            )
            db.add(other)
            db.flush()

        monkeypatch.setattr(
            import_service,
            "_published_model_dataset_category",
            lambda db, published_model_id: (
                other,
                {"category_resolution": "model_lineage"},
            ),
        )
        payload = _payload("pkg-category-conflict")
        payload["target"]["model_id"] = "563"
        response = client.post(
            "/internal/v1/experiment-datasets", json=payload
        )

        assert response.status_code == 409
        assert (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-category-conflict")
            .count()
            == 0
        )

    def test_explicit_category_is_part_of_generated_aggregation_identity(self):
        first = CreateExperimentDatasetImportCommand.model_validate(_payload("pkg-a"))
        second_payload = _payload("pkg-b")
        second_payload["dataset_category_key"] = "otherCategory"
        second = CreateExperimentDatasetImportCommand.model_validate(second_payload)

        first_key, first_identity = import_service._aggregation_key(first)
        second_key, second_identity = import_service._aggregation_key(second)

        assert first_key != second_key
        assert first_identity["dataset_category_key"] == "illegalparking"
        assert second_identity["dataset_category_key"] == "othercategory"

    def test_run_import_job_truncates_long_draft_task_name(
        self,
        client,
        db: Session,
        monkeypatch,
    ) -> None:
        _allow_published_model_for_import_test(monkeypatch)
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )

        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {
                            "file_name": "draft-only.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 30,
                                "y1": 40,
                                "x2": 130,
                                "y2": 260,
                                "class_name": "not_in_labelu",
                            }
                        ],
                    },
                ]
            },
            image_keys=["draft-only.jpg"],
        )

        payload = _payload()
        payload["target"]["model_id"] = "test-model-long-name"
        response = client.post("/internal/v1/experiment-datasets", json=payload)

        assert response.status_code == 202
        import_job_id = response.json()["data"]["import_job_id"]

        import_service.run_import_job_now(import_job_id)

        db.expire_all()
        job = (
            db.query(ExperimentDatasetImport)
            .filter(
                ExperimentDatasetImport.package_id
                == "experiment-dataset-1783148696144971776"
            )
            .first()
        )
        assert job is not None
        assert job.status == "COMPLETED"
        assert job.draft_task_id is not None

        draft_task = db.query(Task).filter(Task.id == job.draft_task_id).first()
        assert draft_task is not None
        assert len(draft_task.name) <= 64
        assert draft_task.name.endswith(_payload()["package_id"][-8:])

    def test_repeated_notify_reuses_same_job_and_only_adds_new_samples(
        self,
        client,
        db: Session,
        monkeypatch,
    ) -> None:
        _allow_published_model_for_import_test(monkeypatch)
        scheduled_job_ids: list[int] = []
        monkeypatch.setattr(
            import_service,
            "schedule_import_job",
            lambda import_job_id: scheduled_job_ids.append(int(import_job_id)),
        )

        with begin_transaction(db):
            crud_annotation_taxonomy.label_preset_create(
                db=db,
                slug="ridingNoHelmet",
                name="骑行未戴头盔",
                description=None,
                sort_order=1,
                items=[
                    {
                        "label_key": "person_electricbike",
                        "label_value": "person_electricbike",
                        "color": "#ff0000",
                        "sort_order": 1,
                    }
                ],
            )

        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {
                            "file_name": "first.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 10,
                                "y1": 20,
                                "x2": 110,
                                "y2": 220,
                                "class_name": "person_electricbike",
                            }
                        ],
                    }
                ]
            },
            image_keys=["first.jpg"],
        )
        first_payload = _payload("pkg-idem-1")
        first_payload["target"]["model_id"] = "test-model-idempotent"
        first = client.post(
            "/internal/v1/experiment-datasets", json=first_payload
        )
        assert first.status_code == 202
        first_job_id = first.json()["data"]["import_job_id"]
        import_service.run_import_job_now(first_job_id)

        db.expire_all()
        first_job = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-idem-1")
            .first()
        )
        assert first_job is not None
        assert first_job.status == "COMPLETED"
        first_formal_task_ids = json.loads(first_job.formal_task_ids_json)
        assert len(first_formal_task_ids) == 1
        first_formal_task_ids = [int(task_id) for task_id in first_formal_task_ids]
        first_task_ids = [
            row.id for row in db.query(Task).order_by(Task.id.asc()).all()
        ]

        first_attachments = (
            db.query(TaskAttachment)
            .filter(
                TaskAttachment.task_id == first_formal_task_ids[0],
                TaskAttachment.deleted_at.is_(None),
            )
            .order_by(TaskAttachment.id.asc())
            .all()
        )
        first_samples = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id == first_formal_task_ids[0],
                TaskSample.deleted_at.is_(None),
            )
            .order_by(TaskSample.id.asc())
            .all()
        )
        first_pre_annotations = (
            db.query(TaskPreAnnotation)
            .filter(
                TaskPreAnnotation.task_id == first_formal_task_ids[0],
                TaskPreAnnotation.deleted_at.is_(None),
            )
            .order_by(TaskPreAnnotation.id.asc())
            .all()
        )
        assert [row.filename for row in first_attachments] == ["first.jpg"]
        assert len(first_samples) == 1
        assert len(first_pre_annotations) == 1
        first_attachment_ids = [row.id for row in first_attachments]
        first_sample_ids = [row.id for row in first_samples]
        first_pre_annotation_ids = [row.id for row in first_pre_annotations]

        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {
                            "file_name": "first.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 10,
                                "y1": 20,
                                "x2": 110,
                                "y2": 220,
                                "class_name": "person_electricbike",
                            }
                        ],
                    },
                    {
                        "image": {
                            "file_name": "second.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 30,
                                "y1": 40,
                                "x2": 130,
                                "y2": 260,
                                "class_name": "person_electricbike",
                            }
                        ],
                    },
                ]
            },
            image_keys=["first.jpg", "second.jpg"],
        )
        second_payload = _payload("pkg-idem-1")
        second_payload["target"]["model_id"] = "test-model-idempotent"
        second = client.post(
            "/internal/v1/experiment-datasets", json=second_payload
        )
        assert second.status_code == 202
        assert second.json()["data"]["import_job_id"] == first_job_id
        assert scheduled_job_ids == [first_job_id]
        import_service.run_import_job_now(first_job_id)

        db.expire_all()
        job = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-idem-1")
            .first()
        )
        assert job is not None
        assert job.status == "COMPLETED"
        assert job.formal_sample_count == 1
        formal_task_ids = json.loads(job.formal_task_ids_json)
        assert [int(task_id) for task_id in formal_task_ids] == first_formal_task_ids
        assert [
            row.id for row in db.query(Task).order_by(Task.id.asc()).all()
        ] == first_task_ids

        attachments = (
            db.query(TaskAttachment)
            .filter(
                TaskAttachment.task_id == formal_task_ids[0],
                TaskAttachment.deleted_at.is_(None),
            )
            .order_by(TaskAttachment.id.asc())
            .all()
        )
        assert [row.id for row in attachments] == first_attachment_ids
        assert [row.filename for row in attachments] == ["first.jpg"]

        samples = (
            db.query(TaskSample)
            .filter(
                TaskSample.task_id == formal_task_ids[0],
                TaskSample.deleted_at.is_(None),
            )
            .order_by(TaskSample.id.asc())
            .all()
        )
        assert [row.id for row in samples] == first_sample_ids
        assert len(samples) == 1

        pre_annotations = (
            db.query(TaskPreAnnotation)
            .filter(
                TaskPreAnnotation.task_id == formal_task_ids[0],
                TaskPreAnnotation.deleted_at.is_(None),
            )
            .order_by(TaskPreAnnotation.id.asc())
            .all()
        )
        assert [row.id for row in pre_annotations] == first_pre_annotation_ids
        assert len(pre_annotations) == 1

    def test_same_package_id_with_changed_labels_returns_conflict(
        self,
        client,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )
        payload = _payload("pkg-idempotency-conflict")
        first = client.post("/internal/v1/experiment-datasets", json=payload)
        assert first.status_code == 202, first.text

        changed = json.loads(json.dumps(payload))
        changed["labeling"]["labels"] = ["changed_label"]
        second = client.post("/internal/v1/experiment-datasets", json=changed)
        assert second.status_code == 409, second.text
        assert "请求内容" in second.text

    def test_run_import_job_requires_all_images_downloaded_before_creating_samples(
        self,
        client,
        db: Session,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )

        with begin_transaction(db):
            crud_annotation_taxonomy.label_preset_create(
                db=db,
                slug="ridingNoHelmet",
                name="骑行未戴头盔",
                description=None,
                sort_order=1,
                items=[
                    {
                        "label_key": "person_electricbike",
                        "label_value": "person_electricbike",
                        "color": "#ff0000",
                        "sort_order": 1,
                    }
                ],
            )

        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {
                            "file_name": "first.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 10,
                                "y1": 20,
                                "x2": 110,
                                "y2": 220,
                                "class_name": "person_electricbike",
                            }
                        ],
                    },
                    {
                        "image": {
                            "file_name": "second.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 30,
                                "y1": 40,
                                "x2": 130,
                                "y2": 260,
                                "class_name": "person_electricbike",
                            }
                        ],
                    },
                ]
            },
            image_keys=["first.jpg", "second.jpg"],
            missing_image_names=["second.jpg"],
        )

        response = client.post(
            "/internal/v1/experiment-datasets", json=_payload("pkg-download-fail")
        )

        assert response.status_code == 202
        import_job_id = response.json()["data"]["import_job_id"]
        import_service.run_import_job_now(import_job_id)

        db.expire_all()
        job = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-download-fail")
            .first()
        )
        assert job is not None
        assert job.status == "FAILED"
        assert db.query(TaskAttachment).count() == 0
        assert db.query(TaskSample).count() == 0

        detail = client.get("/internal/v1/experiment-datasets/pkg-download-fail")
        detail_body = detail.json()["data"]
        assert detail_body["completed"] is True
        assert detail_body["success"] is False
        assert detail_body["status_label"] == "失败"
        assert detail_body["stage"] == "IMPORT_FAILED"
        assert detail_body["progress"]["draft_task_id"] is None
        assert detail_body["message"] == "导入失败"
        assert detail_body["last_error"]

    def test_run_import_job_uses_range_downloads_from_byte_zero(
        self,
        client,
        db: Session,
        monkeypatch,
    ) -> None:
        _allow_published_model_for_import_test(monkeypatch)
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )

        with begin_transaction(db):
            crud_annotation_taxonomy.label_preset_create(
                db=db,
                slug="ridingNoHelmet",
                name="骑行未戴头盔",
                description=None,
                sort_order=1,
                items=[
                    {
                        "label_key": "person_electricbike",
                        "label_value": "person_electricbike",
                        "color": "#ff0000",
                        "sort_order": 1,
                    }
                ],
            )

        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {
                            "file_name": "range-only.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "x1": 10,
                                "y1": 20,
                                "x2": 110,
                                "y2": 220,
                                "class_name": "person_electricbike",
                            }
                        ],
                    }
                ]
            },
            image_keys=["range-only.jpg"],
            require_range_image_names=["range-only.jpg"],
        )

        payload = _payload("pkg-range-only")
        payload["target"]["model_id"] = "test-model-range"
        response = client.post(
            "/internal/v1/experiment-datasets", json=payload
        )

        assert response.status_code == 202
        import_job_id = response.json()["data"]["import_job_id"]
        import_service.run_import_job_now(import_job_id)

        db.expire_all()
        job = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-range-only")
            .first()
        )
        assert job is not None
        assert job.status == "COMPLETED"
        assert job.formal_sample_count == 1

    def test_run_import_job_treats_small_reviewed_false_positive_boxes_as_percent_coordinates(
        self,
        client,
        db: Session,
        monkeypatch,
    ) -> None:
        _allow_published_model_for_import_test(monkeypatch)
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )

        with begin_transaction(db):
            crud_annotation_taxonomy.label_preset_create(
                db=db,
                slug="ridingNoHelmet",
                name="preset-percent",
                description=None,
                sort_order=1,
                items=[
                    {
                        "label_key": "person_electricbike",
                        "label_value": "person_electricbike",
                        "color": "#ff0000",
                        "sort_order": 1,
                    }
                ],
            )

        _install_fake_s3(
            monkeypatch,
            metadata={
                "samples": [
                    {
                        "image": {
                            "file_name": "percent-like.jpg",
                            "width": 1920,
                            "height": 1080,
                        },
                        "target_boxes": [
                            {
                                "coordinate_type": "pixel",
                                "x1": 29.17,
                                "y1": 22.78,
                                "x2": 45.68,
                                "y2": 58.52,
                                "class_name": "person_electricbike",
                            }
                        ],
                    }
                ]
            },
            image_keys=["percent-like.jpg"],
        )

        payload = _payload("pkg-percent-1")
        payload["target"]["model_id"] = "test-model-percent"
        response = client.post(
            "/internal/v1/experiment-datasets", json=payload
        )

        assert response.status_code == 202
        import_job_id = response.json()["data"]["import_job_id"]
        import_service.run_import_job_now(import_job_id)

        db.expire_all()
        job = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == "pkg-percent-1")
            .first()
        )
        assert job is not None
        assert job.status == "COMPLETED"

        formal_task_ids = json.loads(job.formal_task_ids_json)
        assert len(formal_task_ids) == 1
        pre_annotations = (
            db.query(TaskPreAnnotation)
            .filter(
                TaskPreAnnotation.task_id == formal_task_ids[0],
                TaskPreAnnotation.deleted_at.is_(None),
            )
            .all()
        )
        assert len(pre_annotations) == 1

        payload = json.loads(pre_annotations[0].data)
        rect = payload["annotations"]["rectTool"]["result"][0]
        assert rect["x"] == pytest.approx(487.7224)
        assert rect["y"] == pytest.approx(214.3598)
        assert rect["width"] == pytest.approx(276.0472)
        assert rect["height"] == pytest.approx(336.3134)
        assert payload["meta"]["width"] == 1672
        assert payload["meta"]["height"] == 941

    def test_list_and_detail_return_package_level_summary(
        self,
        client,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            import_service, "schedule_import_job", lambda import_job_id: None
        )

        create_response = client.post(
            "/internal/v1/experiment-datasets", json=_payload("pkg-query-1")
        )

        assert create_response.status_code == 202
        import_job_id = create_response.json()["data"]["import_job_id"]

        list_response = client.get(
            "/internal/v1/experiment-datasets?package_id=pkg-query-1"
        )
        detail_response = client.get("/internal/v1/experiment-datasets/pkg-query-1")

        assert list_response.status_code == 200
        assert detail_response.status_code == 200
        assert list_response.json()["data"][0]["package_id"] == "pkg-query-1"
        assert list_response.json()["data"][0]["import_job_id"] == import_job_id
        assert list_response.json()["data"][0]["completed"] is False
        assert list_response.json()["data"][0]["success"] is None
        assert detail_response.json()["data"]["completed"] is False
        assert detail_response.json()["data"]["success"] is None
        assert detail_response.json()["data"]["status_label"] == "已受理"
        assert detail_response.json()["data"]["stage"] == "QUEUED"
        assert detail_response.json()["data"]["message"] == "已受理，等待后台导入"
        assert (
            list_response.json()["data"][0]["dataset_type"]
            == "reviewed_false_positive_daily"
        )
        assert (
            list_response.json()["data"][0]["target"]["event_type"]
            == "electrombileWithoutHelmet"
        )
        assert detail_response.json()["data"]["package_id"] == "pkg-query-1"
        assert detail_response.json()["data"]["import_job_id"] == import_job_id
        assert detail_response.json()["data"]["s3_uri"].startswith(
            "s3://acity-artifacts/"
        )
        assert detail_response.json()["data"]["metadata_key"].endswith("metadata.json")
        assert detail_response.json()["data"]["labeling"]["labels"] == [
            "electrombileWithoutHelmet"
        ]
        list_item = list_response.json()["data"][0]
        assert list_item["s3_uri"].startswith("s3://acity-artifacts/")
        assert list_item["metadata_key"].endswith("metadata.json")
        assert list_item["dataset"]["bucket"] == "acity-artifacts"
        assert list_item["labeling"]["labels"] == [
            "electrombileWithoutHelmet"
        ]

    def test_created_tasks_matches_active_formal_tasks_on_task_page(
        self,
        client,
        db: Session,
    ) -> None:
        owner = db.query(User).filter(User.username == "admin@123.com").first()
        assert owner is not None

        with begin_transaction(db):
            active_failed_import_task = Task(
                name="专家回流-骑行未戴头盔-失败导入仍已创建",
                description="source=experiment_dataset_formal\npackage_id=pkg-active-failed",
                media_type="IMAGE",
                status="FINISHED",
                created_by=owner.id,
                updated_by=owner.id,
            )
            active_completed_import_task = Task(
                name="专家回流-骑行未戴头盔-已完成导入",
                description="source=experiment_dataset_formal\npackage_id=pkg-active-completed",
                media_type="IMAGE",
                status="CONFIGURED",
                created_by=owner.id,
                updated_by=owner.id,
            )
            deleted_formal_task = Task(
                name="专家回流-已删除任务",
                description="source=experiment_dataset_formal\npackage_id=pkg-deleted",
                media_type="IMAGE",
                status="FINISHED",
                created_by=owner.id,
                updated_by=owner.id,
                deleted_at=datetime.now(),
            )
            draft_task = Task(
                name="专家回流草稿-骑行未戴头盔",
                description="source=experiment_dataset_draft\npackage_id=pkg-draft",
                media_type="IMAGE",
                status="IMPORTED",
                created_by=owner.id,
                updated_by=owner.id,
            )
            db.add_all(
                [
                    active_failed_import_task,
                    active_completed_import_task,
                    deleted_formal_task,
                    draft_task,
                ]
            )
            db.flush()
            for package_id, status, task_id, draft_task_id in [
                ("pkg-active-failed", "FAILED", active_failed_import_task.id, None),
                (
                    "pkg-active-completed",
                    "COMPLETED",
                    active_completed_import_task.id,
                    None,
                ),
                ("pkg-deleted", "COMPLETED", deleted_formal_task.id, None),
                ("pkg-draft", "COMPLETED", None, draft_task.id),
            ]:
                db.add(
                    ExperimentDatasetImport(
                        package_id=package_id,
                        dataset_type="reviewed_false_positive_daily",
                        dataset_date="2026-07-24",
                        event_type="electrombileWithoutHelmet",
                        model_id="",
                        target_signature="test-signature",
                        source_payload_json=json.dumps(
                            {
                                "target": {
                                    "event_type": "electrombileWithoutHelmet"
                                }
                            }
                        ),
                        status=status,
                        formal_task_ids_json=json.dumps(
                            [task_id] if task_id is not None else []
                        ),
                        draft_task_id=draft_task_id,
                        declared_sample_count=0,
                        formal_sample_count=0,
                        draft_sample_count=0,
                        abnormal_sample_count=0,
                        created_by=owner.id,
                        updated_by=owner.id,
                    )
                )

        response = client.get("/internal/v1/experiment-datasets/created-tasks")

        assert response.status_code == 200
        body = response.json()
        assert body["meta_data"]["total"] == 2
        rows = body["data"]
        assert [row["task_id"] for row in rows] == [
            active_completed_import_task.id,
            active_failed_import_task.id,
        ]
        assert [row["import_status"] for row in rows] == ["COMPLETED", "FAILED"]
        assert all(row["task_url"].startswith("/tasks/") for row in rows)
        assert client.get(
            "/internal/v1/experiment-datasets/created-tasks?status=FINISHED"
        ).json()["meta_data"]["total"] == 1


class _FakeBody:
    def __init__(self, data: bytes):
        self._buffer = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)


class _FakeS3Client:
    def __init__(
        self,
        listings: dict[str, list[str]],
        metadata_by_key: dict[str, dict],
        object_bytes_by_key: dict[str, bytes],
        require_range_keys: set[str] | None = None,
    ):
        self._listings = listings
        self._metadata_by_key = metadata_by_key
        self._object_bytes_by_key = object_bytes_by_key
        self._require_range_keys = require_range_keys or set()

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, ContinuationToken=None):
        return {
            "Contents": [{"Key": key} for key in self._listings.get(Prefix, [])],
            "IsTruncated": False,
        }

    def head_object(self, Bucket, Key):
        data = self._object_bytes_by_key[Key]
        return {"ContentLength": len(data)}

    def get_object(self, Bucket, Key, Range=None):
        if Key in self._metadata_by_key:
            payload = self._metadata_by_key[Key]
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            data = self._object_bytes_by_key[Key]
        if Key in self._require_range_keys and not Range:
            raise TimeoutError("full object stream read timed out")
        if Range:
            range_text = str(Range).replace("bytes=", "")
            start_text, end_text = range_text.split("-", 1)
            start = int(start_text or "0")
            end = int(end_text) if end_text else len(data) - 1
            data = data[start : end + 1]
        return {"Body": _FakeBody(data)}


def _fake_image_bytes(name: str = "") -> bytes:
    out = io.BytesIO()
    # Keep the same object name byte-stable across retries while making two
    # different source objects different contents.  The import Worker now
    # de-duplicates by content SHA256 across packages, so a test that uploads
    # first.jpg and second.jpg must not accidentally give them one hash.
    seed = sum(str(name).encode("utf-8")) % 256
    color = (seed, (seed * 3) % 256, (seed * 7) % 256)
    Image.new("RGB", (32, 24), color=color).save(out, format="JPEG")
    return out.getvalue()


def _install_fake_s3(
    monkeypatch,
    *,
    metadata: dict,
    image_keys: list[str],
    missing_image_names: list[str] | None = None,
    require_range_image_names: list[str] | None = None,
) -> None:
    prefix = "strategy-datasets/reviewed-false-positive/dt=2026-07-03/06ea712b2434fa18/images/"
    listings = {
        prefix: [f"{prefix}{name}" for name in image_keys],
    }
    metadata_by_key = {
        "strategy-datasets/reviewed-false-positive/dt=2026-07-03/06ea712b2434fa18/metadata.json": metadata,
    }
    missing = set(missing_image_names or [])
    object_bytes_by_key = {
        f"{prefix}{name}": _fake_image_bytes(name)
        for name in image_keys
        if name not in missing
    }
    require_range_keys = {
        f"{prefix}{name}" for name in (require_range_image_names or [])
    }
    fake_client = _FakeS3Client(
        listings=listings,
        metadata_by_key=metadata_by_key,
        object_bytes_by_key=object_bytes_by_key,
        require_range_keys=require_range_keys,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.experiment_dataset_import._build_s3_client",
        lambda ds: fake_client,
    )
