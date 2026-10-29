from fastapi.testclient import TestClient
import uuid


def test_edge_feedback_deduplicates_and_reviews_hard_examples(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    testadmin_token_headers: dict[str, str],
):
    payload = {
        "device_id": "edge-01",
        "event_key": "event-001",
        "event_type": "LOW_CONFIDENCE",
        "confidence": 21,
        "asset_uri": "s3://bucket/frame.jpg",
        "payload": {"scene": "street"},
        "fingerprint": "frame-fingerprint-001",
    }
    first = client.post(
        "/api/v1/edge-feedback/events", json=payload, headers=testuser_token_headers
    )
    repeated = client.post(
        "/api/v1/edge-feedback/events", json=payload, headers=testuser_token_headers
    )
    assert first.status_code == 201, first.text
    assert repeated.status_code == 201, repeated.text
    hard_id = first.json()["data"]["id"]
    assert repeated.json()["data"]["id"] == hard_id
    detail = client.get(
        f"/api/v1/edge-feedback/hard-examples/{hard_id}",
        headers=testuser_token_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["fingerprint"] == "frame-fingerprint-001"
    reviewed = client.post(
        f"/api/v1/edge-feedback/hard-examples/{hard_id}/review",
        json={"decision": "ACCEPTED", "reason": "确认漏检"},
        headers=testadmin_token_headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["data"]["training_status"] == "READY"
    clusters = client.get(
        "/api/v1/active-learning/clusters",
        headers=testuser_token_headers,
    )
    assert clusters.status_code == 200, clusters.text
    assert clusters.json()["data"][0]["sample_count"] == 1
    rejected = client.post(
        f"/api/v1/edge-feedback/hard-examples/{hard_id}/review",
        json={"decision": "REJECTED", "reason": "误报"},
        headers=testadmin_token_headers,
    )
    assert rejected.status_code == 200, rejected.text
    clusters_after_reject = client.get(
        "/api/v1/active-learning/clusters",
        headers=testuser_token_headers,
    )
    # 全局队列会在样本被排除后移除空簇；不再依赖项目模式下的零计数占位簇。
    assert clusters_after_reject.json()["data"] == []


def test_edge_feedback_rejects_event_key_reuse_and_member_review(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    first_payload = {
        "device_id": "edge-01",
        "event_key": "stable-event",
        "event_type": "LOW_CONFIDENCE",
        "confidence": 10,
        "fingerprint": "stable-fingerprint-1",
    }
    assert (
        client.post(
            "/api/v1/edge-feedback/events",
            json=first_payload,
            headers=testuser_token_headers,
        ).status_code
        == 201
    )
    conflict = {
        **first_payload,
        "device_id": "edge-02",
        "fingerprint": "stable-fingerprint-2",
    }
    response = client.post(
        "/api/v1/edge-feedback/events", json=conflict, headers=testuser_token_headers
    )
    assert response.status_code == 409, response.text

    username = f"feedback-member-{uuid.uuid4().hex[:8]}@example.com"
    signup = client.post(
        "/api/v1/users/signup", json={"username": username, "password": "User@123"}
    )
    assert signup.status_code == 201, signup.text
    member_login = client.post(
        "/api/v1/users/login", json={"username": username, "password": "User@123"}
    )
    member_headers = {"Authorization": member_login.json()["data"]["token"]}
    member_id = signup.json()["data"]["id"]
    hard_id = client.get(
        "/api/v1/edge-feedback/hard-examples",
        headers=testuser_token_headers,
    ).json()["data"][0]["id"]
    denied = client.post(
        f"/api/v1/edge-feedback/hard-examples/{hard_id}/review",
        json={"decision": "ACCEPTED"},
        headers=member_headers,
    )
    assert denied.status_code == 403, denied.text


def test_edge_feedback_supports_projectless_ingest_and_global_query(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    testadmin_token_headers: dict[str, str],
):
    payload = {
        "device_id": "edge-global-01",
        "event_key": "global-event-001",
        "event_type": "LOW_CONFIDENCE",
        "confidence": 7,
        "asset_uri": "s3://feedback/global.jpg",
        "fingerprint": "global-fingerprint-001",
    }
    created = client.post(
        "/api/v1/edge-feedback/events",
        json=payload,
        headers=testuser_token_headers,
    )
    assert created.status_code == 201, created.text
    row = created.json()["data"]
    assert row["project_id"] is None
    assert row["scope_key"] == "global"
    assert row["created_by"] is not None

    repeated = client.post(
        "/api/v1/edge-feedback/events",
        json=payload,
        headers=testuser_token_headers,
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["data"]["id"] == row["id"]

    listing = client.get(
        "/api/v1/edge-feedback/hard-examples", headers=testuser_token_headers
    )
    assert listing.status_code == 200, listing.text
    assert any(item["id"] == row["id"] for item in listing.json()["data"])

    admin_listing = client.get(
        "/api/v1/edge-feedback/hard-examples", headers=testadmin_token_headers
    )
    assert admin_listing.status_code == 200, admin_listing.text
    assert any(item["id"] == row["id"] for item in admin_listing.json()["data"])

    reviewed = client.post(
        f"/api/v1/edge-feedback/hard-examples/{row['id']}/review",
        json={"decision": "ACCEPTED", "reason": "管理人员确认"},
        headers=testadmin_token_headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["data"]["training_status"] == "READY"
