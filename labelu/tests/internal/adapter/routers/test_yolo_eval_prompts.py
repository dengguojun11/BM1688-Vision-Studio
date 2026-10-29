"""评估提示词页与 /api/v1/yolo-eval-prompts API 测试。"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_eval_prompts_html_page(client: TestClient):
    r = client.get("/yolo-eval-prompts")
    assert r.status_code == 200
    assert "评估提示词" in r.text
    assert "/api/v1/yolo-eval-prompts" in r.text or "yolo-eval-prompts" in r.text


def test_eval_prompts_api_get_patch(client: TestClient, testadmin_token_headers):
    testuser_token_headers = testadmin_token_headers
    r = client.get("/api/v1/yolo-eval-prompts", headers=testuser_token_headers)
    assert r.status_code == 200
    body = r.json()
    assert "full_analysis" in body["data"]
    assert "next_iteration" in body["data"]

    patch = client.patch(
        "/api/v1/yolo-eval-prompts",
        headers=testuser_token_headers,
        json={"full_analysis": "test-full", "next_iteration": "test-next"},
    )
    assert patch.status_code == 200
    assert patch.json()["data"]["full_analysis"] == "test-full"
    assert patch.json()["data"]["next_iteration"] == "test-next"
