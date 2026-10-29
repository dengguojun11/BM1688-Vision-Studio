from __future__ import annotations

import importlib.util
import json
import pathlib
import threading

from labelu.internal.application.service.provider_adapter import (
    HttpProviderAdapter,
    ProviderInputAsset,
    ProviderRequest,
)
from labelu.internal.application.service import provider_worker
from labelu.internal.application.service.provider_adapter import (
    register_provider_adapter,
    unregister_provider_adapter,
)
from labelu.internal.domain.models.ingestion import MediaAsset
from labelu.internal.domain.models.provider_job import PredictionSet, ProviderJob
from labelu.internal.domain.models.provider_registry import ProviderRegistry


def _load_simulator_module():
    path = (
        pathlib.Path(__file__).parents[5]
        / "scripts"
        / "ops"
        / "provider_http_simulator.py"
    )
    spec = importlib.util.spec_from_file_location("provider_http_simulator", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_provider_adapter_round_trips_against_local_socket_server():
    simulator = _load_simulator_module()
    server = simulator.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        adapter = HttpProviderAdapter(
            key="local_http",
            version="1.0.0",
            runtime_adapter="local_http",
            endpoint=endpoint,
            timeout_seconds=5,
        )
        assert adapter.health() is True
        result = adapter.predict(
            ProviderRequest(
                provider_key="local_http",
                provider_version="1.0.0",
                asset_ids=[12],
                contract_version="prediction.v1",
                config={"labels": [{"name": "person", "tool": "rectTool"}]},
                assets=(
                    ProviderInputAsset(
                        id=12,
                        object_uri="file:///tmp/frame-12.jpg",
                        media_type="IMAGE",
                    ),
                ),
            )
        )
        assert result.payload is not None
        assert result.payload["format"] == "7gai.provider_prediction.v1"
        assert result.item_count == 1
        item = result.payload["items"][0]
        assert item["asset_id"] == 12
        assert item["response"]["results"][0]["label"] == "person"
        assert len(result.payload_sha256 or "") == 64
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_provider_worker_persists_http_prediction_set(db):
    simulator = _load_simulator_module()
    server = simulator.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = HttpProviderAdapter(
        key="local_http_worker",
        version="1.0.0",
        runtime_adapter="local_http_worker",
        endpoint=f"http://127.0.0.1:{server.server_port}",
        timeout_seconds=5,
    )
    register_provider_adapter(adapter)
    try:
        db.add(
            ProviderRegistry(
                provider_key=adapter.key,
                provider_version=adapter.version,
                display_name="Local HTTP",
                provider_kind="PREANNOTATION",
                runtime_adapter=adapter.runtime_adapter,
                status="ACTIVE",
                capabilities_json='["DETECTION"]',
                supported_media_types_json='["IMAGE"]',
                supported_chips_json='["CPU"]',
                config_schema_json='{"type":"object"}',
            )
        )
        asset = MediaAsset(
            project_id=1,
            object_uri="file:///tmp/worker-frame.jpg",
            media_type="IMAGE",
            sha256="d" * 64,
            size_bytes=12,
            scan_status="PASSED",
            quality_status="PASSED",
            lifecycle_status="ACTIVE",
            created_by=1,
        )
        db.add(asset)
        db.flush()
        job = ProviderJob(
            project_id=1,
            provider_key=adapter.key,
            provider_version=adapter.version,
            input_asset_ids_json=json.dumps([asset.id]),
            contract_version="prediction.v1",
            config_json=json.dumps(
                {"labels": [{"name": "person", "tool": "rectTool"}]}
            ),
            request_signature="local-http-worker",
            status="QUEUED",
            total_count=1,
            created_by=1,
        )
        db.add(job)
        db.commit()

        assert provider_worker.run_next_provider_job(db, worker_id="http-sim") is True
        db.refresh(job)
        assert job.status == "SUCCEEDED", job.error_message
        prediction = (
            db.query(PredictionSet)
            .filter(PredictionSet.provider_job_id == job.id)
            .one()
        )
        assert prediction.review_status == "PENDING"
        payload = json.loads(prediction.payload_json)
        assert payload["items"][0]["response"]["model"] == "7gai-local-provider"
        lineage = json.loads(prediction.metrics_json)["lineage"]
        assert lineage["input_assets"][0]["id"] == asset.id
    finally:
        unregister_provider_adapter(adapter.key, adapter.version)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
