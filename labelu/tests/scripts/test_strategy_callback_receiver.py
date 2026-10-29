from __future__ import annotations

import json

from fastapi.testclient import TestClient

from scripts import strategy_callback_receiver as receiver


def test_callback_receiver_persists_and_exposes_payload(tmp_path, monkeypatch):
    store = receiver.CallbackStore(tmp_path / "callbacks.jsonl")
    monkeypatch.setattr(receiver, "store", store)
    client = TestClient(receiver.app)

    payload = {
        "schema_version": "v1",
        "callback_event_id": "strategy-auto-1",
        "external_task_id": "strategy-task-1",
        "task_type": "model_capability",
        "status": "succeeded",
        "result": {
            "target": {"modelId": "550"},
            "candidate_model": {"precision": "int8"},
        },
    }

    response = client.post(
        receiver.DEFAULT_CALLBACK_PATH,
        json=payload,
        headers={"X-LabelU-Event-Id": "strategy-auto-1"},
    )

    assert response.status_code == 200
    assert response.json()["received"] is True
    assert response.json()["callback_event_id"] == "strategy-auto-1"
    recent = client.get(
        "/api/v1/strategy-evolution/platform-callbacks/recent?limit=1"
    )
    assert recent.status_code == 200
    assert recent.json()["data"][0]["payload"] == payload
    assert recent.json()["data"][0]["headers"]["X-LabelU-Event-Id"] == (
        "strategy-auto-1"
    )
    stored = json.loads((tmp_path / "callbacks.jsonl").read_text(encoding="utf-8"))
    assert stored["payload"] == payload


def test_callback_receiver_rejects_non_object_json():
    client = TestClient(receiver.app)
    response = client.post(
        receiver.DEFAULT_CALLBACK_PATH,
        content="[]",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 400
