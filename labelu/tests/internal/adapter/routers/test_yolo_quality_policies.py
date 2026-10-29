from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.common.label_contract import label_set_signature


@pytest.fixture
def testuser_token_headers(testadmin_token_headers):
    """Global policy mutations must use the production-equivalent admin user."""
    return testadmin_token_headers


def _create_label_preset(client: TestClient, headers, slug: str, name: str):
    resp = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=headers,
        json={
            "slug": slug,
            "name": name,
            "items": [
                {
                    "label_key": name,
                    "label_value": slug,
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def test_quality_policies_html_page(client: TestClient):
    resp = client.get("/yolo-quality-policies")

    assert resp.status_code == 200
    assert "yolo-quality-policies" in resp.text
    assert "loadPolicies()" in resp.text
    assert "AI 推荐草稿" in resp.text


def test_quality_policies_api_list_and_upsert(client: TestClient, testuser_token_headers, tmp_path, monkeypatch):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))
    _create_label_preset(client, testuser_token_headers, "garbage_exposure", "垃圾暴露")

    resp = client.get("/api/v1/yolo-quality-policies", headers=testuser_token_headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["policies"][0]["policy_name"] == "default-policy-v1"

    save_resp = client.post(
        "/api/v1/yolo-quality-policies",
        headers=testuser_token_headers,
        json={
            "policy_name": "garbage-ai-v1",
            "scene_name": "垃圾暴露",
            "rect_preset_slug": "garbage_exposure",
            "status": "draft",
            "source": "manual",
            "source_summary": {"business_goal": "减少漏检"},
            "training_quality": {"map50_95_pass": 0.58},
            "generalization_quality": {"test_map50_95_warn": 0.5},
            "edge_usability": {"require_edge_validation": True},
            "release_readiness": {"block_publish_when_edge_unverified": True},
            "key_classes": [{"label": "垃圾暴露", "min_ap50_95": 0.5}],
            "reasons": ["人工录入"],
            "risks": [],
        },
    )
    assert save_resp.status_code == 200, save_resp.text
    rows = save_resp.json()["data"]["policies"]
    assert any(row["scene_name"] == "垃圾暴露" for row in rows)


def test_quality_policies_api_activate(client: TestClient, testuser_token_headers, tmp_path, monkeypatch):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))
    _create_label_preset(client, testuser_token_headers, "scene_x", "场景X")

    client.post(
        "/api/v1/yolo-quality-policies",
        headers=testuser_token_headers,
        json={
            "policy_name": "scene-x",
            "scene_name": "场景X",
            "rect_preset_slug": "scene_x",
            "status": "draft",
            "source": "manual",
        },
    )
    resp = client.post(
        "/api/v1/yolo-quality-policies/activate",
        headers=testuser_token_headers,
        json={"policy_name": "scene-x"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["active_policy_name"] == "scene-x"
    assert resp.json()["data"]["active_policy_names"]["scene_x"] == "scene-x"


def test_quality_policies_api_activate_keeps_other_scene_active(
    client: TestClient,
    testuser_token_headers,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))
    _create_label_preset(client, testuser_token_headers, "scene_a", "场景A")
    _create_label_preset(client, testuser_token_headers, "scene_b", "场景B")

    client.post(
        "/api/v1/yolo-quality-policies",
        headers=testuser_token_headers,
        json={
            "policy_name": "scene-a-v1",
            "scene_name": "场景A",
            "rect_preset_slug": "scene_a",
            "status": "draft",
            "source": "manual",
        },
    )
    client.post(
        "/api/v1/yolo-quality-policies",
        headers=testuser_token_headers,
        json={
            "policy_name": "scene-b-v1",
            "scene_name": "场景B",
            "rect_preset_slug": "scene_b",
            "status": "draft",
            "source": "manual",
        },
    )
    client.post(
        "/api/v1/yolo-quality-policies/activate",
        headers=testuser_token_headers,
        json={"policy_name": "scene-a-v1"},
    )
    resp = client.post(
        "/api/v1/yolo-quality-policies/activate",
        headers=testuser_token_headers,
        json={"policy_name": "scene-b-v1"},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["active_policy_names"]["scene_a"] == "scene-a-v1"
    assert data["active_policy_names"]["scene_b"] == "scene-b-v1"
    rows = {row["policy_name"]: row for row in data["policies"]}
    assert rows["scene-a-v1"]["is_active"] is True
    assert rows["scene-b-v1"]["is_active"] is True


def test_quality_policies_api_recommend(client: TestClient, testuser_token_headers, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_quality_policy_recommendation.recommend_quality_policy",
        lambda db, cmd, current_user: {
            "recommendation": {
                "policy_name": "scene-ai-v1",
                "scene_name": cmd.scene_name,
                "rect_preset_slug": cmd.rect_preset_slug,
                "status": "draft",
                "source": "ai_recommended",
                "version": 1,
                "is_active": False,
                "source_summary": {},
                "training_quality": {"map50_95_pass": 0.6},
                "generalization_quality": {},
                "edge_usability": {},
                "release_readiness": {},
                "key_classes": [],
                "reasons": ["AI建议"],
                "risks": [],
                "updated_at": None,
            },
            "default_recommendation": {
                "policy_name": "scene-default-v1",
                "scene_name": cmd.scene_name,
                "rect_preset_slug": cmd.rect_preset_slug,
                "status": "draft",
                "source": "ai_recommended",
                "version": 1,
                "is_active": False,
                "source_summary": {},
                "training_quality": {"map50_95_pass": 0.55},
                "generalization_quality": {},
                "edge_usability": {},
                "release_readiness": {},
                "key_classes": [],
                "reasons": ["默认值"],
                "risks": [],
                "updated_at": None,
            },
            "fallback_used": False,
            "provider_key": "demo-model",
            "fallback_reason": "",
        },
    )

    resp = client.post(
        "/api/v1/yolo-quality-policies/recommend",
        headers=testuser_token_headers,
        json={
            "scene_name": "垃圾暴露",
            "rect_preset_slug": "garbage_exposure",
            "business_goal": "减少漏检",
            "key_classes": ["垃圾暴露"],
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["recommendation"]["policy_name"] == "scene-ai-v1"
    assert resp.json()["data"]["default_recommendation"]["training_quality"]["map50_95_pass"] == 0.55


def test_quality_policies_api_upsert_rejects_unknown_preset(
    client: TestClient,
    testuser_token_headers,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))

    resp = client.post(
        "/api/v1/yolo-quality-policies",
        headers=testuser_token_headers,
        json={
            "policy_name": "unknown-preset-policy",
            "scene_name": "未知场景",
            "rect_preset_slug": "not_exists_slug",
            "status": "draft",
            "source": "manual",
        },
    )

    assert resp.status_code == 400, resp.text
    assert "拉框预设不存在" in resp.text


def test_quality_policy_new_chain_binds_direct_label_ids(
    client: TestClient,
    testuser_token_headers,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr("labelu.internal.common.config.settings.BASE_DATA_DIR", str(tmp_path))
    label_ids = []
    for index, value in enumerate(("quality_person", "quality_car"), start=1):
        response = client.post(
            f"{settings.API_V1_STR}/annotation-taxonomy/labels",
            headers=testuser_token_headers,
            json={
                "label_key": f"Quality label {index}",
                "label_value": value,
                "annotation_kind": "bbox",
                "color": f"#{index:06x}",
            },
        )
        assert response.status_code == 201, response.text
        label_ids.append(response.json()["data"]["id"])

    saved = client.post(
        f"{settings.API_V1_STR}/yolo-quality-policies",
        headers=testuser_token_headers,
        json={
            "policy_name": "quality-direct-labels",
            "scene_name": "Direct labels",
            "label_ids": list(reversed(label_ids)),
            "model_family": "yolov11",
            "annotation_kind": "bbox",
            "status": "draft",
            "source": "manual",
        },
    )
    assert saved.status_code == 200, saved.text
    row = next(
        item
        for item in saved.json()["data"]["policies"]
        if item["policy_name"] == "quality-direct-labels"
    )
    assert row["rect_preset_slug"] == ""
    assert row["label_ids"] == list(reversed(label_ids))
    assert row["label_set_signature"] == label_set_signature(label_ids)

    activated = client.post(
        f"{settings.API_V1_STR}/yolo-quality-policies/activate",
        headers=testuser_token_headers,
        json={"policy_name": "quality-direct-labels"},
    )
    assert activated.status_code == 200, activated.text
    semantic_key = f"semantic:yolov11:bbox:{label_set_signature(label_ids)}"
    assert activated.json()["data"]["active_policy_names"][semantic_key] == "quality-direct-labels"
