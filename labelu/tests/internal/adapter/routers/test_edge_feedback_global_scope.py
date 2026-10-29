from labelu.internal.application.command.edge_feedback import CreateInferenceEventCommand


def test_edge_feedback_command_allows_project_free_reports():
    command = CreateInferenceEventCommand(
        device_id="bm1688-01",
        event_key="global-event-1",
        fingerprint="global-fingerprint-1",
    )
    assert command.project_id is None


def test_edge_feedback_project_free_http_chain(client, testuser_token_headers):
    payload = {
        "device_id": "bm1688-01",
        "event_key": "global-event-http-1",
        "event_type": "LOW_CONFIDENCE",
        "confidence": 32,
        "asset_uri": "s3://problem-images/1.jpg",
        "fingerprint": "global-http-fingerprint-1",
        "payload": {"model_set_id": 534, "scene": "yard"},
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

    listed = client.get(
        "/api/v1/edge-feedback/hard-examples",
        headers=testuser_token_headers,
    )
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == row["id"] for item in listed.json()["data"])

    duplicate = client.post(
        "/api/v1/edge-feedback/events",
        json=payload,
        headers=testuser_token_headers,
    )
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["data"]["id"] == row["id"]
