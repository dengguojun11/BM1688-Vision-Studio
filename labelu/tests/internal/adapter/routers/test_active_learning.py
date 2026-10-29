"""Global active-learning API contracts after project scope retirement."""

import json

from labelu.internal.domain.models.auto_training import AutoTrainingAuditEvent
from labelu.internal.domain.models.edge_feedback import EdgeHardExample, InferenceEvent


def _add_example(
    db,
    *,
    device_id: str,
    event_key: str,
    confidence: int,
    event_type: str = "LOW_CONFIDENCE",
    fingerprint: str | None = None,
    review_status: str = "ACCEPTED",
    training_status: str = "READY",
    payload: dict | None = None,
) -> EdgeHardExample:
    event = InferenceEvent(
        project_id=None,
        scope_key="global",
        created_by=1,
        device_id=device_id,
        event_key=event_key,
        event_type=event_type,
        confidence=confidence,
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
    )
    db.add(event)
    db.flush()
    hard = EdgeHardExample(
        project_id=None,
        scope_key="global",
        created_by=1,
        inference_event_id=event.id,
        device_id=device_id,
        fingerprint=fingerprint or f"{event_key}-fingerprint",
        confidence=confidence,
        hard_example_type=event_type,
        review_status=review_status,
        training_status=training_status,
    )
    db.add(hard)
    db.flush()
    return hard


def _batch(client, headers, *, quota=1, batch_key=None):
    payload = {"quota": quota}
    if batch_key:
        payload["batch_key"] = batch_key
    response = client.post(
        "/api/v1/active-learning/batches", json=payload, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_active_learning_clusters_and_batch_quota(
    client, db, testadmin_token_headers
):
    for index, confidence in enumerate((10, 20, 30), start=1):
        _add_example(
            db,
            device_id="edge-1",
            event_key=f"e-{index}",
            confidence=confidence,
            fingerprint=f"fp-{index}",
        )
    db.commit()
    clusters = client.post(
        "/api/v1/active-learning/clusters/rebuild",
        json={"confidence_threshold": 50},
        headers=testadmin_token_headers,
    )
    assert clusters.status_code == 200, clusters.text
    assert clusters.json()["data"][0]["sample_count"] == 3
    batch = _batch(client, testadmin_token_headers, quota=2)
    assert batch["selected_count"] == 2
    assert len(batch["items"]) == 2


def test_active_learning_cluster_search_is_server_paginated(
    client, db, testadmin_token_headers
):
    _add_example(
        db,
        device_id="edge-search-1",
        event_key="cluster-search-1",
        confidence=10,
        payload={"source_package_id": "source-a"},
    )
    _add_example(
        db,
        device_id="edge-search-2",
        event_key="cluster-search-2",
        confidence=12,
        payload={"source_package_id": "source-b"},
    )
    db.commit()
    rebuilt = client.post(
        "/api/v1/active-learning/clusters/rebuild",
        json={"confidence_threshold": 50},
        headers=testadmin_token_headers,
    )
    assert rebuilt.status_code == 200, rebuilt.text

    page = client.get(
        "/api/v1/active-learning/clusters/search?page=0&size=1",
        headers=testadmin_token_headers,
    )
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["meta_data"]["total"] == 2
    assert len(body["data"]) == 1

    searched = client.get(
        "/api/v1/active-learning/clusters/search?q=edge-search-2",
        headers=testadmin_token_headers,
    )
    assert searched.status_code == 200, searched.text
    searched_body = searched.json()
    assert searched_body["meta_data"]["total"] == 1
    assert searched_body["data"][0]["device_id"] == "edge-search-2"


def test_active_learning_accept_reuses_workflow(
    client, db, testadmin_token_headers
):
    _add_example(
        db,
        device_id="edge-2",
        event_key="workflow-event",
        confidence=5,
        event_type="CONFLICT",
        fingerprint="fp-workflow",
    )
    db.commit()
    batch = _batch(client, testadmin_token_headers)
    accepted = client.post(
        f"/api/v1/active-learning/batches/{batch['id']}/accept",
        json={},
        headers=testadmin_token_headers,
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()["data"]
    assert body["status"] == "ACCEPTED"
    assert body["workflow_id"]
    assert body["dataset_version_id"]
    workflow = client.get(
        f"/api/v1/workflows/{body['workflow_id']}", headers=testadmin_token_headers
    )
    assert workflow.json()["data"]["workflow_type"] == "expert_feedback"
    version = client.get(
        f"/api/v1/dataset-versions/{body['dataset_version_id']}",
        headers=testadmin_token_headers,
    )
    assert version.status_code == 200, version.text
    assert version.json()["data"]["status"] == "READY_TO_REVIEW"


def test_active_learning_accept_is_idempotent_after_commit(
    client, db, testadmin_token_headers
):
    _add_example(
        db,
        device_id="edge-idempotent",
        event_key="idempotent-event",
        confidence=3,
        fingerprint="idempotent-fingerprint",
    )
    db.commit()
    batch = _batch(client, testadmin_token_headers, batch_key="accept-idempotent")
    repeated = _batch(client, testadmin_token_headers, batch_key="accept-idempotent")
    assert repeated["id"] == batch["id"]
    first = client.post(
        f"/api/v1/active-learning/batches/{batch['id']}/accept",
        json={"reason": "首轮接受"},
        headers=testadmin_token_headers,
    )
    second = client.post(
        f"/api/v1/active-learning/batches/{batch['id']}/accept",
        json={"reason": "网络重试"},
        headers=testadmin_token_headers,
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    assert second.json()["data"]["workflow_id"] == first.json()["data"]["workflow_id"]
    assert second.json()["data"]["dataset_version_id"] == first.json()["data"]["dataset_version_id"]


def test_active_learning_freeze_respects_phase_one_automatic_training_lock(
    client, db, testadmin_token_headers
):
    _add_example(
        db,
        device_id="edge-loop",
        event_key="loop-1",
        confidence=1,
        fingerprint="fp-loop",
    )
    db.commit()
    batch = _batch(client, testadmin_token_headers)
    accepted = client.post(
        f"/api/v1/active-learning/batches/{batch['id']}/accept",
        json={},
        headers=testadmin_token_headers,
    ).json()["data"]
    workflow_id = accepted["workflow_id"]
    reviewed = client.post(
        f"/api/v1/dataset-versions/{accepted['dataset_version_id']}/review",
        json={"decision": "APPROVE", "comment": "主动学习难例已复核"},
        headers=testadmin_token_headers,
    )
    assert reviewed.status_code == 200, reviewed.text
    frozen = client.post(
        f"/api/v1/dataset-versions/{accepted['dataset_version_id']}/freeze",
        headers=testadmin_token_headers,
    )
    assert frozen.status_code == 403, frozen.text
    assert frozen.json()["err_code"] == 57122
    version = client.get(
        f"/api/v1/dataset-versions/{accepted['dataset_version_id']}",
        headers=testadmin_token_headers,
    )
    assert version.json()["data"]["status"] == "READY_TO_REVIEW"
    workflow = client.get(
        f"/api/v1/workflows/{workflow_id}", headers=testadmin_token_headers
    )
    assert workflow.json()["data"]["status"] in {"PENDING", "WAITING"}
    assert db.query(AutoTrainingAuditEvent).count() == 0


def test_active_learning_batch_exposes_four_dimension_scoring_snapshot(
    client, db, testadmin_token_headers
):
    rows = [
        ("edge-a", 45, {"scene": "street", "class_id": 1}),
        ("edge-a", 55, {"scene": "street", "class_id": 1}),
        ("edge-b", 80, {"scene": "highway", "class_id": 2}),
    ]
    hard_ids = []
    for index, (device_id, confidence, payload) in enumerate(rows, start=1):
        hard = _add_example(
            db,
            device_id=device_id,
            event_key=f"score-event-{index}",
            confidence=confidence,
            fingerprint=f"score-fingerprint-{index}",
            payload=payload,
        )
        hard_ids.append(hard.id)
    db.commit()
    response = client.post(
        "/api/v1/active-learning/batches",
        json={"quota": 3},
        headers=testadmin_token_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()["data"]
    assert body["scoring_policy"]["version"] == "7gai.active_learning_scoring.v2"
    assert body["scoring_policy"]["dimensions"] == [
        "uncertainty",
        "representativeness",
        "class_scarcity",
        "device_scene_coverage",
    ]
    assert body["items"][0]["hard_example_id"] == hard_ids[2]
    for item in body["items"]:
        breakdown = item["score_breakdown"]
        assert set(breakdown) == set(body["scoring_policy"]["dimensions"])
        assert all(0 <= value <= 100 for value in breakdown.values())
        snapshot = body["scoring_policy"]["candidate_scores"][str(item["hard_example_id"])]
        assert item["score"] == snapshot["score"]


def test_active_learning_accept_rejects_stale_review_snapshot(
    client, db, testadmin_token_headers
):
    hard = _add_example(
        db,
        device_id="edge-stale",
        event_key="stale-event",
        confidence=10,
        event_type="CONFLICT",
        fingerprint="stale-fingerprint",
    )
    db.commit()
    batch = _batch(client, testadmin_token_headers)
    hard.review_status = "REJECTED"
    hard.training_status = "NOT_INCLUDED"
    db.commit()
    response = client.post(
        f"/api/v1/active-learning/batches/{batch['id']}/accept",
        json={"reason": "审核快照过期"},
        headers=testadmin_token_headers,
    )
    assert response.status_code == 409, response.text
    db.refresh(hard)
    current = client.get(
        f"/api/v1/active-learning/batches/{batch['id']}",
        headers=testadmin_token_headers,
    )
    assert current.json()["data"]["status"] == "READY"
    assert hard.training_status == "NOT_INCLUDED"


def test_active_learning_batches_do_not_reselect_reserved_examples(
    client, db, testadmin_token_headers
):
    _add_example(
        db,
        device_id="edge-reserved",
        event_key="reserved-event",
        confidence=5,
        fingerprint="reserved-fingerprint",
    )
    db.commit()
    first = _batch(client, testadmin_token_headers, batch_key="reserved-a")
    assert first["selected_count"] == 1
    second = _batch(client, testadmin_token_headers, batch_key="reserved-b")
    assert second["selected_count"] == 0
    assert second["items"] == []
    rejected = client.post(
        f"/api/v1/active-learning/batches/{second['id']}/accept",
        json={},
        headers=testadmin_token_headers,
    )
    assert rejected.status_code == 409, rejected.text


def test_active_learning_accept_marks_examples_consumed_for_future_batches(
    client, db, testadmin_token_headers
):
    hard = _add_example(
        db,
        device_id="edge-consumed",
        event_key="consumed-event",
        confidence=8,
        fingerprint="consumed-fingerprint",
    )
    db.commit()
    first = _batch(client, testadmin_token_headers, batch_key="consume-a")
    accepted = client.post(
        f"/api/v1/active-learning/batches/{first['id']}/accept",
        json={},
        headers=testadmin_token_headers,
    )
    assert accepted.status_code == 200, accepted.text
    db.refresh(hard)
    assert hard.training_status == "COMPLETED"
    clusters = client.get(
        "/api/v1/active-learning/clusters", headers=testadmin_token_headers
    )
    assert clusters.status_code == 200, clusters.text
    assert not clusters.json()["data"] or all(
        row["sample_count"] == 0 for row in clusters.json()["data"]
    )
    second = _batch(client, testadmin_token_headers, batch_key="consume-b")
    assert second["selected_count"] == 0
    assert second["items"] == []


def test_active_learning_clusters_exclude_rejected_and_consumed_examples(
    client, db, testadmin_token_headers
):
    examples = [
        ("PENDING", "NOT_INCLUDED"),
        ("ACCEPTED", "READY"),
        ("REJECTED", "NOT_INCLUDED"),
        ("ACCEPTED", "COMPLETED"),
    ]
    for index, (review_status, training_status) in enumerate(examples, start=1):
        _add_example(
            db,
            device_id="edge-cluster",
            event_key=f"cluster-filter-{index}",
            confidence=20,
            fingerprint=f"cluster-filter-fp-{index}",
            review_status=review_status,
            training_status=training_status,
        )
    db.commit()
    response = client.post(
        "/api/v1/active-learning/clusters/rebuild",
        json={"confidence_threshold": 50},
        headers=testadmin_token_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["sample_count"] == 2
