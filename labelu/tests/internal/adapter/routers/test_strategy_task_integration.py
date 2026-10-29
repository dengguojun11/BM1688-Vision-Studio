import io
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.data_source import DataSource
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.task import Task


class _FakeBody:
    def __init__(self, data: bytes):
        self._buffer = io.BytesIO(data)

    def read(self) -> bytes:
        return self._buffer.read()


class _FakeS3Client:
    def __init__(self, listings: dict[str, list[str]], metadata_map: dict[str, dict]):
        self.listings = listings
        self.metadata_map = metadata_map
        self.list_failures = 0

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000, ContinuationToken=None):
        if self.list_failures > 0:
            self.list_failures -= 1
            raise RuntimeError("temporary S3 failure")
        keys = self.listings.get(Prefix, [])
        return {
            "Contents": [{"Key": key} for key in keys],
            "IsTruncated": False,
        }

    def get_object(self, Bucket, Key):
        payload = self.metadata_map[Key]
        return {
            "Body": _FakeBody(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        }


def _payload(external_task_id: str, *, task_name: str | None = None) -> dict:
    return {
        "schema_version": "v1",
        "external_task_id": external_task_id,
        "task_name": task_name or f"task-{external_task_id}",
        "task_type": "threshold_experiment",
        "dataset": {
            "s3_uri": f"s3://acity-artifacts/strategy-datasets/{external_task_id}/",
            "image_prefix": "images/",
            "metadata_key": "metadata.json",
        },
        "labeling": {
            "required": True,
            "media_type": "IMAGE",
            "annotation_type": "bbox",
            "labels": ["person"],
        },
        "target": {
            "device_type": "camera",
            "event_type": "person",
            "model_id": "model-v1",
            "scene_type": "default",
            "strategy_id": "strategy-v1",
        },
        "experiment": {
            "current_thresholds": {"edge_low_threshold": 0.35},
            "search_space": {"edge_low_threshold": [0.4, 0.45]},
            "goal": "reduce_false_positive",
        },
    }


def _install_fake_s3(monkeypatch, *, external_task_ids: list[str]):
    listings: dict[str, list[str]] = {}
    metadata_map: dict[str, dict] = {}
    for external_task_id in external_task_ids:
        image_prefix = f"strategy-datasets/{external_task_id}/images/"
        listings[image_prefix] = [
            f"{image_prefix}sample-a.jpg",
            f"{image_prefix}sample-b.png",
            f"{image_prefix}ignore.txt",
        ]
        metadata_map[f"strategy-datasets/{external_task_id}/metadata.json"] = {
            "samples": [
                {
                    "image_name": "sample-a.jpg",
                    "width": 1920,
                    "height": 1080,
                    "target_boxes": [
                        {"label": "person", "x": 10, "y": 20, "width": 30, "height": 40}
                    ],
                },
                {
                    "image_name": "sample-b.png",
                    "width": 1280,
                    "height": 720,
                    "target_boxes": [
                        {"label": "person", "x": 50, "y": 60, "width": 70, "height": 80}
                    ],
                },
            ]
        }
    fake_client = _FakeS3Client(listings=listings, metadata_map=metadata_map)
    monkeypatch.setattr(
        "labelu.internal.application.service.strategy_task_integration._build_s3_client",
        lambda ds: fake_client,
    )
    return fake_client


class TestStrategyTaskIntegrationRouter:
    def test_create_strategy_task_does_not_require_login(
        self,
        client: TestClient,
        monkeypatch,
    ) -> None:
        external_task_id = "strategy-task-no-auth"
        _install_fake_s3(monkeypatch, external_task_ids=[external_task_id])

        response = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            json=_payload(external_task_id),
        )

        assert response.status_code == 201
        assert response.json()["data"]["import_status"] == "PENDING"
        assert response.json()["data"]["package_id"]
        assert response.json()["data"]["status_url"].startswith(
            "/internal/v1/experiment-datasets/"
        )

    def test_create_strategy_task_successful(
        self,
        client: TestClient,
        testuser_token_headers: dict,
        db: Session,
        monkeypatch,
    ) -> None:
        external_task_id = "strategy-task-1001"
        _install_fake_s3(monkeypatch, external_task_ids=[external_task_id])

        response = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            headers=testuser_token_headers,
            json=_payload(external_task_id, task_name="night-person-fp"),
        )

        assert response.status_code == 201
        body = response.json()["data"]
        assert body["created"] is True
        assert body["import_status"] == "PENDING"
        assert body["sample_count"] == 0
        assert body["pre_annotation_count"] == 0
        assert body["metadata_consumed"] is False
        assert body["labelu_task_id"] is None
        assert body["datasource_id"] is None

        db.expire_all()
        integration = (
            db.query(StrategyTaskIntegration)
            .filter(StrategyTaskIntegration.external_task_id == external_task_id)
            .first()
        )
        assert integration is not None
        assert integration.import_status == "PENDING"
        assert integration.datasource_id is None
        assert integration.labelu_task_id is None
        import_row = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == body["package_id"])
            .one()
        )
        assert import_row.source_integration_id == integration.id
        assert db.query(Task).filter(Task.source_external_task_id == external_task_id).count() == 0

    def test_legacy_route_only_enqueues_unified_import_and_never_reads_s3(
        self,
        client: TestClient,
        db: Session,
        monkeypatch,
    ) -> None:
        external_task_id = "strategy-task-worker-only"

        def fail_if_legacy_s3_is_used(*args, **kwargs):
            raise AssertionError("legacy request path must not read S3")

        monkeypatch.setattr(
            "labelu.internal.application.service.strategy_task_integration._build_s3_client",
            fail_if_legacy_s3_is_used,
        )
        response = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            json=_payload(external_task_id),
        )

        assert response.status_code == 201, response.text
        body = response.json()["data"]
        assert body["import_status"] == "PENDING"
        assert body["status_url"].startswith("/internal/v1/experiment-datasets/")
        assert db.query(Task).filter(Task.source_external_task_id == external_task_id).count() == 0

    def test_create_strategy_task_is_idempotent(
        self,
        client: TestClient,
        testuser_token_headers: dict,
        db: Session,
        monkeypatch,
    ) -> None:
        external_task_id = "strategy-task-1002"
        _install_fake_s3(monkeypatch, external_task_ids=[external_task_id])
        payload = _payload(external_task_id)

        first = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            headers=testuser_token_headers,
            json=payload,
        )
        second = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            headers=testuser_token_headers,
            json=payload,
        )

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["data"]["created"] is True
        assert second.json()["data"]["created"] is False
        assert first.json()["data"]["package_id"] == second.json()["data"]["package_id"]

        db.expire_all()
        integration_rows = (
            db.query(StrategyTaskIntegration)
            .filter(StrategyTaskIntegration.external_task_id == external_task_id)
            .all()
        )
        assert len(integration_rows) == 1
        assert integration_rows[0].labelu_task_id is None
        import_rows = db.query(ExperimentDatasetImport).all()
        assert len(import_rows) == 1

        integration = integration_rows[0]
        assert len(integration.request_sha256 or "") == 64
        assert import_rows[0].source_integration_id == integration.id

    def test_same_external_task_id_with_changed_labels_returns_conflict(
        self,
        client: TestClient,
        testuser_token_headers: dict,
        monkeypatch,
    ) -> None:
        external_task_id = "strategy-task-idempotency-conflict"
        _install_fake_s3(monkeypatch, external_task_ids=[external_task_id])
        payload = _payload(external_task_id)

        first = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            headers=testuser_token_headers,
            json=payload,
        )
        assert first.status_code == 201, first.text

        changed = json.loads(json.dumps(payload))
        changed["labeling"]["labels"] = ["vehicle"]
        second = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            headers=testuser_token_headers,
            json=changed,
        )
        assert second.status_code == 409, second.text
        assert "请求内容" in second.text

    def test_reuses_datasource_across_strategy_tasks(
        self,
        client: TestClient,
        testuser_token_headers: dict,
        db: Session,
        monkeypatch,
    ) -> None:
        task_a = "strategy-task-2001"
        task_b = "strategy-task-2002"
        _install_fake_s3(monkeypatch, external_task_ids=[task_a, task_b])

        resp_a = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            headers=testuser_token_headers,
            json=_payload(task_a, task_name="task-a"),
        )
        resp_b = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            headers=testuser_token_headers,
            json=_payload(task_b, task_name="task-b"),
        )

        assert resp_a.status_code == 201
        assert resp_b.status_code == 201
        data_a = resp_a.json()["data"]
        data_b = resp_b.json()["data"]
        assert data_a["datasource_id"] is None
        assert data_b["datasource_id"] is None

        db.expire_all()
        datasource_count = (
            db.query(DataSource)
            .filter(
                DataSource.deleted_at.is_(None), DataSource.bucket == "acity-artifacts"
            )
            .count()
        )
        assert datasource_count == 0

    def test_dataset_category_key_is_validated_and_preserved_for_training(
        self,
        client: TestClient,
        db: Session,
        monkeypatch,
    ) -> None:
        external_task_id = "strategy-task-category"
        _install_fake_s3(monkeypatch, external_task_ids=[external_task_id])
        with begin_transaction(db):
            category = DatasetCategory(
                category_key="road_scene",
                name="道路场景",
                status="active",
                version=1,
            )
            db.add(category)
            db.flush()
            category_id = int(category.id)
        payload = _payload(external_task_id)
        payload["dataset_category_key"] = "road_scene"

        response = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            json=payload,
        )

        assert response.status_code == 201, response.text
        body = response.json()["data"]
        assert body["dataset_category_id"] == category_id
        assert body["dataset_category_key"] == "road_scene"
        row = (
            db.query(StrategyTaskIntegration)
            .filter(StrategyTaskIntegration.external_task_id == external_task_id)
            .one()
        )
        assert row.dataset_category_id == category_id

        with begin_transaction(db):
            category.status = "disabled"
            db.add(category)

        repeated = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            json=payload,
        )
        assert repeated.status_code == 400, repeated.text
        assert "road_scene" in repeated.text

    def test_unknown_dataset_category_key_is_rejected(
        self,
        client: TestClient,
    ) -> None:
        payload = _payload("strategy-task-category-missing")
        payload["dataset_category_key"] = "missing_category"

        response = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            json=payload,
        )

        assert response.status_code == 400, response.text
        assert "missing_category" in response.text

    def test_s3_retry_reuses_task_instead_of_creating_orphan(
        self,
        client: TestClient,
        db: Session,
        monkeypatch,
    ) -> None:
        external_task_id = "strategy-task-s3-retry"
        fake_s3 = _install_fake_s3(
            monkeypatch,
            external_task_ids=[external_task_id],
        )
        fake_s3.list_failures = 1
        payload = _payload(external_task_id)

        first = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            json=payload,
        )
        second = client.post(
            f"{settings.API_V1_STR}/integrations/strategy-tasks",
            json=payload,
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert first.json()["data"]["import_status"] == "PENDING"
        assert second.json()["data"]["import_status"] == "PENDING"
        integration = (
            db.query(StrategyTaskIntegration)
            .filter(StrategyTaskIntegration.external_task_id == external_task_id)
            .one()
        )
        tasks = db.query(Task).filter(Task.source_external_task_id == external_task_id).all()
        assert tasks == []
        assert integration.labelu_task_id is None
        assert db.query(ExperimentDatasetImport).count() == 1
        assert fake_s3.list_failures == 1
