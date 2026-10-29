import json

from labelu.internal.application.service.provider_adapter import (
    DeterministicLocalAdapter,
    ProviderResult,
    register_provider_adapter,
    unregister_provider_adapter,
)
from labelu.internal.application.service import provider_worker
from labelu.internal.domain.models.ingestion import MediaAsset
from labelu.internal.domain.models.model_version import ModelVersion
from labelu.internal.domain.models.provider_job import ProviderJob, PredictionSet
from labelu.internal.domain.models.provider_registry import ProviderRegistry


class _Adapter:
    key = "unit_provider"
    version = "1.0"
    runtime_adapter = "unit_runtime"

    def predict(self, request):
        return ProviderResult(
            payload_uri="file:///pred.json", payload_sha256="a" * 64, item_count=2
        )


def test_worker_missing_adapter_marks_job_failed(db):
    row = ProviderJob(
        project_id=1,
        provider_key="missing",
        provider_version="1",
        input_asset_ids_json="[]",
        contract_version="prediction.v1",
        request_signature="unit-missing",
        status="QUEUED",
        created_by=1,
    )
    db.add(row)
    db.commit()
    assert provider_worker.run_next_provider_job(db, worker_id="unit") is True
    db.refresh(row)
    assert row.status == "FAILED"
    assert row.error_code == "PROVIDER_ADAPTER_NOT_READY"


def test_worker_executes_registered_adapter_and_writes_prediction(db):
    register_provider_adapter(_Adapter())
    db.add(
        ProviderRegistry(
            provider_key="unit_provider",
            provider_version="1.0",
            display_name="Unit",
            provider_kind="PREANNOTATION",
            runtime_adapter="unit_runtime",
            status="ACTIVE",
            capabilities_json="[]",
            supported_media_types_json="[]",
            supported_chips_json="[]",
            config_schema_json='{"type":"object"}',
        )
    )
    db.commit()
    row = ProviderJob(
        project_id=1,
        provider_key="unit_provider",
        provider_version="1.0",
        input_asset_ids_json="[]",
        contract_version="prediction.v1",
        request_signature="unit-success",
        status="QUEUED",
        total_count=1,
        created_by=1,
    )
    db.add(row)
    db.commit()
    assert provider_worker.run_next_provider_job(db, worker_id="unit") is True
    db.refresh(row)
    assert row.status == "SUCCEEDED", row.error_message
    prediction = (
        db.query(PredictionSet).filter(PredictionSet.provider_job_id == row.id).one()
    )
    assert prediction.source == "AI"
    assert prediction.payload_sha256 == "a" * 64


def test_worker_replays_identical_prediction_without_duplicate(db):
    register_provider_adapter(_Adapter())
    db.add(
        ProviderRegistry(
            provider_key="unit_provider",
            provider_version="1.0",
            display_name="Unit",
            provider_kind="PREANNOTATION",
            runtime_adapter="unit_runtime",
            status="ACTIVE",
            capabilities_json="[]",
            supported_media_types_json="[]",
            supported_chips_json="[]",
            config_schema_json='{"type":"object"}',
        )
    )
    row = ProviderJob(
        project_id=1,
        provider_key="unit_provider",
        provider_version="1.0",
        input_asset_ids_json="[]",
        contract_version="prediction.v1",
        request_signature="unit-replay",
        status="QUEUED",
        total_count=1,
        created_by=1,
    )
    db.add(row)
    db.flush()
    db.add(
        PredictionSet(
            project_id=1,
            provider_job_id=row.id,
            version=1,
            annotation_contract_version="annotation_contract.v1",
            prediction_schema_version="prediction.v1",
            payload_uri="file:///pred.json",
            payload_sha256="a" * 64,
            status="COMPLETED",
            review_status="PENDING",
            source="AI",
            item_count=2,
            metrics_json=json.dumps(
                {
                    "lineage": {
                        "provider_job_id": row.id,
                        "project_id": 1,
                        "provider_key": "unit_provider",
                        "provider_version": "1.0",
                        "model_version_id": None,
                        "input_assets": [],
                    }
                },
                separators=(",", ":"),
            ),
            created_by=1,
        )
    )
    db.commit()
    assert provider_worker.run_next_provider_job(db, worker_id="unit") is True
    db.refresh(row)
    assert row.status == "SUCCEEDED"
    assert (
        db.query(PredictionSet).filter(PredictionSet.provider_job_id == row.id).count()
        == 1
    )


def test_worker_fails_when_registry_runtime_does_not_match(db):
    register_provider_adapter(_Adapter())
    db.add(
        ProviderRegistry(
            provider_key="unit_provider",
            provider_version="1.0",
            display_name="Unit",
            provider_kind="PREANNOTATION",
            runtime_adapter="wrong_runtime",
            status="ACTIVE",
            capabilities_json="[]",
            supported_media_types_json="[]",
            supported_chips_json="[]",
            config_schema_json='{"type":"object"}',
        )
    )
    row = ProviderJob(
        project_id=1,
        provider_key="unit_provider",
        provider_version="1.0",
        input_asset_ids_json="[]",
        contract_version="prediction.v1",
        request_signature="unit-mismatch",
        status="QUEUED",
        created_by=1,
    )
    db.add(row)
    db.commit()
    assert provider_worker.run_next_provider_job(db, worker_id="unit") is True
    db.refresh(row)
    assert row.status == "FAILED"
    assert row.error_code == "PROVIDER_ADAPTER_NOT_READY"


def test_worker_rejects_conflicting_prediction_replay(db):
    register_provider_adapter(_Adapter())
    db.add(
        ProviderRegistry(
            provider_key="unit_provider",
            provider_version="1.0",
            display_name="Unit",
            provider_kind="PREANNOTATION",
            runtime_adapter="unit_runtime",
            status="ACTIVE",
            capabilities_json="[]",
            supported_media_types_json="[]",
            supported_chips_json="[]",
            config_schema_json='{"type":"object"}',
        )
    )
    row = ProviderJob(
        project_id=1,
        provider_key="unit_provider",
        provider_version="1.0",
        input_asset_ids_json="[]",
        contract_version="prediction.v1",
        request_signature="unit-conflict",
        status="QUEUED",
        total_count=1,
        created_by=1,
    )
    db.add(row)
    db.flush()
    db.add(
        PredictionSet(
            project_id=1,
            provider_job_id=row.id,
            version=1,
            annotation_contract_version="annotation_contract.v1",
            prediction_schema_version="prediction.v1",
            payload_uri="file:///different.json",
            payload_sha256="b" * 64,
            status="COMPLETED",
            review_status="PENDING",
            source="AI",
            item_count=1,
            metrics_json="{}",
            created_by=1,
        )
    )
    db.commit()
    assert provider_worker.run_next_provider_job(db, worker_id="unit") is True
    db.refresh(row)
    assert row.status == "FAILED"
    assert row.error_code == "PROVIDER_RESULT_CONFLICT"
    assert (
        db.query(PredictionSet).filter(PredictionSet.provider_job_id == row.id).count()
        == 1
    )


def test_worker_persists_deterministic_media_lineage(db):
    adapter = DeterministicLocalAdapter()
    register_provider_adapter(adapter)
    db.add(
        ProviderRegistry(
            provider_key=adapter.key,
            provider_version=adapter.version,
            display_name="Local",
            provider_kind="PREANNOTATION",
            runtime_adapter=adapter.runtime_adapter,
            status="ACTIVE",
            capabilities_json="[]",
            supported_media_types_json="[]",
            supported_chips_json="[]",
            config_schema_json='{"type":"object"}',
        )
    )
    asset = MediaAsset(
        project_id=1,
        object_uri="file:///sample.jpg",
        media_type="IMAGE",
        sha256="c" * 64,
        size_bytes=42,
        scan_status="PASSED",
        quality_status="PASSED",
        lifecycle_status="ACTIVE",
        created_by=1,
    )
    db.add(asset)
    db.flush()
    model = ModelVersion(
        algorithm_id=1,
        dataset_version_id=1,
        model_key="local-model-v1",
        version_no=1,
        display_name="Local model",
        status="CANDIDATE",
        created_by=1,
    )
    db.add(model)
    db.flush()
    row = ProviderJob(
        project_id=1,
        provider_key=adapter.key,
        provider_version=adapter.version,
        model_version_id=model.id,
        input_asset_ids_json=json.dumps([asset.id]),
        contract_version="prediction.v1",
        request_signature="unit-lineage",
        status="QUEUED",
        total_count=1,
        created_by=1,
    )
    db.add(row)
    db.commit()
    try:
        assert provider_worker.run_next_provider_job(db, worker_id="unit") is True
        prediction = (
            db.query(PredictionSet)
            .filter(PredictionSet.provider_job_id == row.id)
            .one()
        )
        metrics = json.loads(prediction.metrics_json)
        assert metrics["lineage"]["model_version_id"] == model.id
        assert metrics["lineage"]["input_assets"][0]["id"] == asset.id
        assert metrics["lineage"]["input_assets"][0]["sha256"] == "c" * 64
        assert (
            metrics["lineage"]["input_assets"][0]["object_uri"] == "file:///sample.jpg"
        )
    finally:
        unregister_provider_adapter(adapter.key, adapter.version)


def test_worker_rechecks_asset_scan_and_lifecycle_state(db):
    adapter = DeterministicLocalAdapter()
    register_provider_adapter(adapter)
    db.add(
        ProviderRegistry(
            provider_key=adapter.key,
            provider_version=adapter.version,
            display_name="Local",
            provider_kind="PREANNOTATION",
            runtime_adapter=adapter.runtime_adapter,
            status="ACTIVE",
            capabilities_json="[]",
            supported_media_types_json="[]",
            supported_chips_json="[]",
            config_schema_json='{"type":"object"}',
        )
    )
    asset = MediaAsset(
        project_id=1,
        object_uri="file:///pending.jpg",
        media_type="IMAGE",
        sha256="e" * 64,
        size_bytes=42,
        scan_status="PENDING",
        quality_status="PASSED",
        lifecycle_status="ACTIVE",
        created_by=1,
    )
    db.add(asset)
    db.flush()
    row = ProviderJob(
        project_id=1,
        provider_key=adapter.key,
        provider_version=adapter.version,
        input_asset_ids_json=json.dumps([asset.id]),
        contract_version="prediction.v1",
        request_signature="unit-asset-state",
        status="QUEUED",
        total_count=1,
        created_by=1,
    )
    db.add(row)
    db.commit()
    try:
        assert provider_worker.run_next_provider_job(db, worker_id="asset-state") is True
        db.refresh(row)
        assert row.status == "FAILED"
        assert row.error_code == "PROVIDER_INPUT_ASSET_NOT_READY"
    finally:
        unregister_provider_adapter(adapter.key, adapter.version)


def test_worker_uses_model_version_when_job_has_no_project_scope(db):
    adapter = DeterministicLocalAdapter()
    register_provider_adapter(adapter)
    db.add(
        ProviderRegistry(
            provider_key=adapter.key,
            provider_version=adapter.version,
            display_name="Local",
            provider_kind="PREANNOTATION",
            runtime_adapter=adapter.runtime_adapter,
            status="ACTIVE",
            capabilities_json="[]",
            supported_media_types_json="[]",
            supported_chips_json="[]",
            config_schema_json='{"type":"object"}',
        )
    )
    asset = MediaAsset(
        object_uri="file:///sample-no-project.jpg",
        media_type="IMAGE",
        sha256="d" * 64,
        size_bytes=42,
        scan_status="PASSED",
        quality_status="PASSED",
        lifecycle_status="ACTIVE",
        created_by=1,
    )
    db.add(asset)
    db.flush()
    model = ModelVersion(
        algorithm_id=1,
        dataset_version_id=1,
        model_key="no-project-model-v1",
        version_no=1,
        display_name="No project model",
        status="CANDIDATE",
        created_by=1,
    )
    db.add(model)
    db.flush()
    row = ProviderJob(
        provider_key=adapter.key,
        provider_version=adapter.version,
        model_version_id=model.id,
        input_asset_ids_json=json.dumps([asset.id]),
        contract_version="prediction.v1",
        request_signature="unit-no-project-model",
        status="QUEUED",
        total_count=1,
        created_by=1,
    )
    db.add(row)
    db.commit()
    try:
        assert provider_worker.run_next_provider_job(db, worker_id="unit-no-project") is True
        db.refresh(row)
        assert row.status == "SUCCEEDED", row.error_message
        prediction = (
            db.query(PredictionSet).filter(PredictionSet.provider_job_id == row.id).one()
        )
        assert json.loads(prediction.metrics_json)["lineage"]["model_version_id"] == model.id
    finally:
        unregister_provider_adapter(adapter.key, adapter.version)


def test_worker_persists_inline_provider_payload(db):
    import hashlib

    class InlineAdapter:
        key = "inline_provider"
        version = "1.0"
        runtime_adapter = "inline_runtime"

        def predict(self, request):
            payload = {"annotations": {"rectTool": {"result": []}}}
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            return ProviderResult(
                payload=payload,
                payload_sha256=digest,
                item_count=0,
            )

    adapter = InlineAdapter()
    register_provider_adapter(adapter)
    db.add(
        ProviderRegistry(
            provider_key=adapter.key,
            provider_version=adapter.version,
            display_name="Inline",
            provider_kind="PREANNOTATION",
            runtime_adapter=adapter.runtime_adapter,
            status="ACTIVE",
            capabilities_json="[]",
            supported_media_types_json="[]",
            supported_chips_json="[]",
            config_schema_json='{"type":"object"}',
        )
    )
    row = ProviderJob(
        project_id=1,
        provider_key=adapter.key,
        provider_version=adapter.version,
        input_asset_ids_json="[]",
        contract_version="prediction.v1",
        request_signature="inline-payload",
        status="QUEUED",
        total_count=0,
        created_by=1,
    )
    db.add(row)
    db.commit()
    try:
        assert provider_worker.run_next_provider_job(db, worker_id="inline") is True
        prediction = (
            db.query(PredictionSet)
            .filter(PredictionSet.provider_job_id == row.id)
            .one()
        )
        assert json.loads(prediction.payload_json)["annotations"] == {
            "rectTool": {"result": []}
        }
        assert len(prediction.payload_sha256) == 64
    finally:
        unregister_provider_adapter(adapter.key, adapter.version)
