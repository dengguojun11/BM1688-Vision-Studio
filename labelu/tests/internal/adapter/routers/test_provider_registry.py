from fastapi.testclient import TestClient

from labelu.internal.application.service.provider_adapter import (
    ProviderResult,
    register_provider_adapter,
    unregister_provider_adapter,
)


def test_provider_runtime_check_and_local_hil_routes_are_admin_only(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    testadmin_token_headers: dict[str, str],
):
    forbidden = client.post(
        "/api/v1/providers/yolo/runtime-check", headers=testuser_token_headers
    )
    assert forbidden.status_code == 403
    hil_forbidden = client.post(
        "/api/v1/providers/yolo/local-hil",
        json={},
        headers=testuser_token_headers,
    )
    assert hil_forbidden.status_code == 403


def test_provider_runtime_routes_persist_local_probe_for_admin(
    client: TestClient, testadmin_token_headers: dict[str, str]
):
    register_provider_adapter(_YoloRuntime())
    try:
        checked = client.post(
            "/api/v1/providers/yolo/runtime-check",
            params={"provider_version": "11.0.1"},
            headers=testadmin_token_headers,
        )
        assert checked.status_code == 200, checked.text
        assert checked.json()["data"]["runtime_ready"] is True

        probed = client.post(
            "/api/v1/providers/yolo/local-hil",
            json={"provider_version": "11.0.1"},
            headers=testadmin_token_headers,
        )
        assert probed.status_code == 200, probed.text
        data = probed.json()["data"]
        assert data["runtime_evidence"]["local_hil_status"] == "PASSED"
        assert data["hil_verified"] is False
        assert data["production_ready"] is False
    finally:
        unregister_provider_adapter("yolo", "11.0.1")


class _YoloRuntime:
    key = "yolo"
    version = "11.0.1"
    runtime_adapter = "yolo_preannotate"

    def predict(self, request):
        return ProviderResult(payload_uri="file:///unused.json", payload_sha256="a" * 64)


def test_provider_registry_requires_authentication(client: TestClient):
    assert client.get("/api/v1/providers").status_code == 403


def test_provider_registry_lists_legacy_preannotation_adapters(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    response = client.get("/api/v1/providers", headers=testuser_token_headers)
    assert response.status_code == 200
    rows = response.json()["data"]
    assert {row["provider_key"] for row in rows} >= {
        "yolo",
        "tagtool_cls",
        "locateanything",
        "vlx_seek",
        "rex_omni",
        "vision_llm",
    }
    yolo = next(row for row in rows if row["provider_key"] == "yolo")
    assert yolo["provider_version"] == "11.0.1"
    assert yolo["output_contract_version"] == "prediction.v1"
    assert yolo["config_schema"] == {"type": "object"}
    # Registry rows are not production-available until MLOps evidence exists.
    assert yolo["runtime_registered"] is False
    assert yolo["runtime_ready"] is False
    assert yolo["hil_verified"] is False
    assert yolo["production_ready"] is False
    assert yolo["available"] is False


def test_provider_registry_reports_runtime_ready_but_not_production_available(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    register_provider_adapter(_YoloRuntime())
    try:
        response = client.get("/api/v1/providers/yolo", params={"provider_version": "11.0.1"}, headers=testuser_token_headers)
        assert response.status_code == 200
        row = response.json()["data"]
        assert row["runtime_registered"] is True
        assert row["runtime_healthy"] is True
        assert row["runtime_ready"] is True
        assert row["hil_verified"] is False
        assert row["production_ready"] is False
        assert row["available"] is False
    finally:
        unregister_provider_adapter("yolo", "11.0.1")


def test_provider_registry_capability_filter_and_detail(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    filtered = client.get(
        "/api/v1/providers",
        params={"capability": "classification"},
        headers=testuser_token_headers,
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"]
    assert all("CLASSIFICATION" in row["capabilities"] for row in filtered.json()["data"])

    detail = client.get(
        "/api/v1/providers/yolo",
        params={"provider_version": "11.0.1"},
        headers=testuser_token_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["runtime_adapter"] == "yolo_preannotate"
    assert (
        client.get(
            "/api/v1/providers/not-registered", headers=testuser_token_headers
        ).status_code
        == 404
    )


def test_provider_registry_ignores_retired_project_query(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    testadmin_token_headers: dict[str, str],
):
    response = client.get(
        "/api/v1/providers",
        params={"project_id": 999999},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200
    assert all("project_id" not in row for row in response.json()["data"])


def test_provider_registry_rejects_unknown_status(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    response = client.get(
        "/api/v1/providers",
        params={"status": "RUNNING"},
        headers=testuser_token_headers,
    )
    assert response.status_code == 400


def test_provider_registry_exposes_server_pagination_metadata(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    response = client.get(
        "/api/v1/providers",
        params={"page": 1, "size": 2},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 2
    assert body["meta_data"] == {"total": 6, "page": 1, "size": 2}


def test_provider_evidence_update_requires_admin_and_persists_gate_fields(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    testadmin_token_headers: dict[str, str],
):
    payload = {
        "provider_version": "11.0.1",
        "image_digest": "a" * 64,
        "toolchain_version": "sophgo-3.2",
        "hil_verified": True,
        "hil_evidence": {"status": "PASSED", "evidence_id": "hil-1"},
        "runtime_evidence": {"status": "READY", "agent": "sim-1"},
        "availability_reason": "本地 HIL 已通过",
    }
    forbidden = client.patch("/api/v1/providers/yolo/evidence", json=payload, headers=testuser_token_headers)
    assert forbidden.status_code == 403
    updated = client.patch("/api/v1/providers/yolo/evidence", json=payload, headers=testadmin_token_headers)
    assert updated.status_code == 200, updated.text
    row = updated.json()["data"]
    assert row["image_digest"] == "sha256:" + "a" * 64
    assert row["toolchain_version"] == "sophgo-3.2"
    assert row["hil_verified"] is True
    assert row["hil_evidence"]["evidence_id"] == "hil-1"
    assert row["runtime_evidence"]["status"] == "READY"
    persisted = client.get("/api/v1/providers/yolo", params={"provider_version": "11.0.1"}, headers=testadmin_token_headers)
    assert persisted.status_code == 200
    assert persisted.json()["data"]["image_digest"] == "sha256:" + "a" * 64
    assert persisted.json()["data"]["hil_verified"] is True


def test_provider_evidence_rejects_invalid_digest(
    client: TestClient, testadmin_token_headers: dict[str, str]
):
    response = client.patch(
        "/api/v1/providers/yolo/evidence",
        json={"image_digest": "not-a-sha"},
        headers=testadmin_token_headers,
    )
    assert response.status_code == 422
