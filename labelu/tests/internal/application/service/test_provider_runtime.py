from __future__ import annotations

import importlib.util
import pathlib
import threading

from labelu.internal.application.service.provider_adapter import (
    HttpProviderAdapter,
    ProviderResult,
    register_provider_adapter,
    unregister_provider_adapter,
)
from labelu.internal.application.service.provider_runtime import local_hil_check, runtime_check
from labelu.internal.application.command.provider_runtime import RunProviderLocalHilCommand
from labelu.internal.domain.models.user import User


def _load_simulator_module():
    path = pathlib.Path(__file__).parents[5] / "scripts" / "ops" / "provider_http_simulator.py"
    spec = importlib.util.spec_from_file_location("provider_http_simulator_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runtime_check_persists_local_health_snapshot(db):
    simulator = _load_simulator_module()
    server = simulator.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = HttpProviderAdapter(
        key="locateanything",
        version="1.0.0",
        runtime_adapter="locateanything_preannotate",
        endpoint=f"http://127.0.0.1:{server.server_port}",
        timeout_seconds=5,
    )
    register_provider_adapter(adapter)
    try:
        row = runtime_check(
            db,
            "locateanything",
            provider_version="1.0.0",
            current_user=User(id=1, username="admin@123.com"),
        )
        assert row.runtime_ready is True
        assert row.runtime_evidence["status"] == "READY"
        assert row.runtime_evidence["environment"] == "local"
        assert row.runtime_evidence["healthy"] is True
    finally:
        unregister_provider_adapter(adapter.key, adapter.version)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_hil_probe_records_report_without_production_hil_gate(db):
    simulator = _load_simulator_module()
    server = simulator.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = HttpProviderAdapter(
        key="locateanything",
        version="1.0.0",
        runtime_adapter="locateanything_preannotate",
        endpoint=f"http://127.0.0.1:{server.server_port}",
        timeout_seconds=5,
    )
    register_provider_adapter(adapter)
    try:
        row = local_hil_check(
            db,
            "locateanything",
            command=RunProviderLocalHilCommand(
                provider_version="1.0.0",
                sample_uri="file:///tmp/7gai-hil.jpg",
                labels=[{"name": "person", "tool": "rectTool"}],
            ),
            current_user=User(id=1, username="admin@123.com"),
        )
        assert row.runtime_evidence["local_hil_status"] == "PASSED"
        assert row.hil_verified is False
        assert row.production_ready is False
        assert row.runtime_evidence["environment"] == "local"
    finally:
        unregister_provider_adapter(adapter.key, adapter.version)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_local_hil_failure_does_not_leave_runtime_ready(db):
    class FailingAdapter:
        key = "locateanything"
        version = "1.0.0"
        runtime_adapter = "locateanything_preannotate"

        def health(self):
            return True

        def ready(self):
            return True

        def predict(self, _request):
            return ProviderResult(
                payload={"items": []},
                payload_sha256="not-the-payload-digest",
                item_count=0,
            )

    adapter = FailingAdapter()
    register_provider_adapter(adapter)
    try:
        row = local_hil_check(
            db,
            "locateanything",
            command=RunProviderLocalHilCommand(
                provider_version="1.0.0",
                sample_uri="file:///tmp/7gai-failing-hil.jpg",
                labels=[{"name": "person", "tool": "rectTool"}],
            ),
            current_user=User(id=1, username="admin@123.com"),
        )
        assert row.runtime_evidence["local_hil_status"] == "FAILED"
        assert row.runtime_evidence["status"] == "UNHEALTHY"
        assert row.production_ready is False
    finally:
        unregister_provider_adapter(adapter.key, adapter.version)
