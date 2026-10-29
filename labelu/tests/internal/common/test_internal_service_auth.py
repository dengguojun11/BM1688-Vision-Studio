from __future__ import annotations

import hashlib
import hmac
import time

from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.main import app


def _signed_headers(*, path: str, secret: str, nonce: str) -> dict[str, str]:
    body_hash = hashlib.sha256(b"").hexdigest()
    timestamp = str(int(time.time()))
    canonical = "\n".join(("GET", path, timestamp, nonce, body_hash)).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    return {
        "X-LabelU-Timestamp": timestamp,
        "X-LabelU-Nonce": nonce,
        "X-LabelU-Signature": f"sha256={signature}",
    }


def test_internal_reads_reject_missing_service_credential(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True)
    monkeypatch.setattr(
        settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "service-test-token"
    )
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")

    response = client.get("/internal/v1/strategy-evolution/tasks")

    assert response.status_code == 401


def test_experiment_dataset_queries_reject_missing_service_credential(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True)
    monkeypatch.setattr(
        settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "service-test-token"
    )
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")

    for path in (
        "/internal/v1/experiment-datasets",
        "/internal/v1/experiment-datasets/created-tasks",
        "/internal/v1/experiment-datasets/missing-package",
    ):
        response = client.get(path)
        assert response.status_code == 401, (path, response.text)


def test_internal_reads_accept_dedicated_service_token(client: TestClient, monkeypatch):
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True)
    monkeypatch.setattr(
        settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "service-test-token"
    )
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")

    response = client.get(
        "/internal/v1/experiment-datasets/created-tasks",
        headers={"X-7GAI-Service-Token": "service-test-token"},
    )

    assert response.status_code == 200, response.text


def test_internal_reads_accept_hmac_and_reject_replay(client: TestClient, monkeypatch):
    secret = "internal-hmac-test-secret"
    path = "/internal/v1/strategy-evolution/tasks"
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "")
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", secret)

    headers = _signed_headers(path=path, secret=secret, nonce="internal-read-nonce")
    first = client.get(path, headers=headers)
    second = client.get(path, headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text


def test_internal_retry_route_requires_service_credential(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True)
    monkeypatch.setattr(
        settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "service-test-token"
    )
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")

    response = client.post(
        "/internal/v1/strategy-evolution/tasks/missing-task/callback-retry",
        json={},
    )

    assert response.status_code == 401


def test_strategy_evolution_create_routes_require_service_credential(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True)
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "service-test-token")
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")
    payload = {"external_task_id": "auth-create-check", "task_type": "threshold_experiment"}

    for path in (
        "/internal/v1/strategy-evolution/tasks",
        "/internal/v1/strategy-evolution/threshold-experiments",
        "/internal/v1/strategy-evolution/model-trainings",
        "/api/v1/strategy-tasks",
    ):
        response = client.post(path, json=payload)
        assert response.status_code == 401, (path, response.text)

    headers = {"X-7GAI-Service-Token": "service-test-token"}
    response = client.post(
        "/internal/v1/strategy-evolution/threshold-experiments",
        json=payload,
        headers=headers,
    )
    assert response.status_code != 401, response.text


def test_internal_routes_publish_service_auth_in_openapi():
    schema = app.openapi()
    protected = (
        ("/internal/v1/strategy-evolution/tasks", "post"),
        ("/api/v1/strategy-tasks", "post"),
        ("/api/v1/strategy-tasks", "get"),
        ("/api/v1/strategy-tasks/{external_task_id}", "get"),
        ("/internal/v1/strategy-evolution/threshold-experiments", "post"),
        ("/internal/v1/strategy-evolution/model-trainings", "post"),
        ("/internal/v1/strategy-evolution/tasks", "get"),
        ("/internal/v1/strategy-evolution/tasks/{external_task_id}", "get"),
        (
            "/internal/v1/strategy-evolution/tasks/{external_task_id}/callback-retry",
            "post",
        ),
        ("/internal/v1/experiment-datasets", "get"),
        ("/internal/v1/experiment-datasets/created-tasks", "get"),
        ("/internal/v1/experiment-datasets/{package_id}", "get"),
    )
    for path, method in protected:
        assert schema["paths"][path][method]["security"] == [{"APIKeyHeader": []}]
