import json

from labelu.internal.domain.models.edge_feedback import EdgeHardExample, InferenceEvent


def test_projectless_active_learning_cluster_and_batch_use_global_scope(
    client, db, testuser_token_headers, testadmin_token_headers
):
    event = InferenceEvent(
        project_id=None,
        scope_key="global",
        created_by=1,
        device_id="bm1688-global",
        event_key="global-active-learning-event",
        event_type="LOW_CONFIDENCE",
        confidence=8,
        payload_json=json.dumps(
            {"model_set_id": 534, "source_package_id": "s3-package-001", "scene": "yard"}
        ),
    )
    db.add(event)
    db.flush()
    db.add(
        EdgeHardExample(
            project_id=None,
            scope_key="global",
            created_by=1,
            inference_event_id=event.id,
            device_id="bm1688-global",
            model_version_id=None,
            fingerprint="global-active-learning-fingerprint",
            confidence=8,
            hard_example_type="LOW_CONFIDENCE",
            review_status="ACCEPTED",
            training_status="READY",
        )
    )
    db.commit()

    rebuilt = client.post(
        "/api/v1/active-learning/clusters/rebuild",
        json={"confidence_threshold": 50},
        headers=testadmin_token_headers,
    )
    assert rebuilt.status_code == 200, rebuilt.text
    cluster = rebuilt.json()["data"][0]
    assert cluster["project_id"] is None
    assert cluster["scope_key"] == "global"
    assert cluster["source_key"] == "s3-package-001"

    listed = client.get(
        "/api/v1/active-learning/clusters", headers=testadmin_token_headers
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"][0]["scope_key"] == "global"

    first = client.post(
        "/api/v1/active-learning/batches",
        json={"quota": 1, "source_key": "s3-package-001"},
        headers=testadmin_token_headers,
    )
    assert first.status_code == 201, first.text
    body = first.json()["data"]
    assert body["project_id"] is None
    assert body["scope_key"] == "global"
    assert body["selected_count"] == 1

    repeated = client.post(
        "/api/v1/active-learning/batches",
        json={"quota": 1, "source_key": "s3-package-001"},
        headers=testadmin_token_headers,
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["data"]["id"] == body["id"]


def test_projectless_batch_accept_creates_global_dataset_and_workflow(
    client, db, testadmin_token_headers
):
    event = InferenceEvent(
        project_id=None,
        scope_key="global",
        created_by=1,
        device_id="bm1688-global-accept",
        event_key="global-accept-event",
        event_type="LOW_CONFIDENCE",
        confidence=6,
        payload_json=json.dumps({"model_set_id": 534, "source_package_id": "s3-package-accept"}),
    )
    db.add(event)
    db.flush()
    db.add(
        EdgeHardExample(
            project_id=None,
            scope_key="global",
            created_by=1,
            inference_event_id=event.id,
            device_id="bm1688-global-accept",
            fingerprint="global-accept-fingerprint",
            confidence=6,
            hard_example_type="LOW_CONFIDENCE",
            review_status="ACCEPTED",
            training_status="READY",
        )
    )
    db.commit()
    batch = client.post(
        "/api/v1/active-learning/batches",
        json={"quota": 1, "source_key": "s3-package-accept", "batch_key": "global-accept"},
        headers=testadmin_token_headers,
    )
    assert batch.status_code == 201, batch.text
    batch_id = batch.json()["data"]["id"]
    accepted = client.post(
        f"/api/v1/active-learning/batches/{batch_id}/accept",
        json={"reason": "全局难例批次审核通过"},
        headers=testadmin_token_headers,
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()["data"]
    assert body["project_id"] is None
    assert body["scope_key"] == "global"
    assert body["dataset_version_id"]
    assert body["workflow_id"]


def test_projectless_orchestration_requires_management_account(
    client, testuser_token_headers
):
    rebuilt = client.post(
        "/api/v1/active-learning/clusters/rebuild",
        json={"confidence_threshold": 50},
        headers=testuser_token_headers,
    )
    assert rebuilt.status_code == 403, rebuilt.text

    created = client.post(
        "/api/v1/active-learning/batches",
        json={"quota": 1},
        headers=testuser_token_headers,
    )
    assert created.status_code == 403, created.text
