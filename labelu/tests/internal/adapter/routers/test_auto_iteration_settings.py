"""自动迭代策略可视化配置 API 测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_settings_returns_defaults_and_editability(
    client: TestClient, testadmin_token_headers
):
    response = client.get(
        "/api/v1/auto-iteration-settings", headers=testadmin_token_headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["values"]["pass_threshold"] == 0.6
    assert data["values"]["max_rounds"] == 4
    assert data["values"]["metric_basis"] == "val_recall"
    assert data["can_edit"] is True
    assert data["max_iterations_after_first"] == 3
    assert any(item["value"] == "val_recall" for item in data["metric_basis_options"])


def test_get_settings_is_read_only_for_normal_user(
    client: TestClient, testuser_token_headers
):
    response = client.get(
        "/api/v1/auto-iteration-settings", headers=testuser_token_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["can_edit"] is False


def test_patch_requires_admin(client: TestClient, testuser_token_headers):
    response = client.patch(
        "/api/v1/auto-iteration-settings",
        headers=testuser_token_headers,
        json={"pass_threshold": 0.7, "reason": "普通用户尝试修改"},
    )
    assert response.status_code == 403, response.text


def test_patch_persists_threshold_and_history(
    client: TestClient, testadmin_token_headers
):
    response = client.patch(
        "/api/v1/auto-iteration-settings",
        headers=testadmin_token_headers,
        json={
            "pass_threshold": 0.7,
            "max_rounds": 5,
            "reason": "业务确认达标线调整为 70%",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["values"]["pass_threshold"] == 0.7
    assert data["values"]["max_rounds"] == 5
    assert data["max_iterations_after_first"] == 4
    assert set(data["updated_keys"]) == {"pass_threshold", "max_rounds"}

    # 保存后读取到的是新值（判定侧读取同一来源）
    fetched = client.get(
        "/api/v1/auto-iteration-settings", headers=testadmin_token_headers
    ).json()["data"]
    assert fetched["values"]["pass_threshold"] == 0.7
    assert fetched["sources"]["pass_threshold"] == "db"

    history = client.get(
        "/api/v1/auto-iteration-settings/history?page=0&size=20",
        headers=testadmin_token_headers,
    )
    assert history.status_code == 200, history.text
    rows = history.json()["data"]
    reasons = {row["change_reason"] for row in rows}
    assert "业务确认达标线调整为 70%" in reasons
    updated = {row["item_key"]: row for row in rows}
    assert updated["pass_threshold"]["after"] == 0.7


def test_patch_rejects_invalid_threshold(client: TestClient, testadmin_token_headers):
    response = client.patch(
        "/api/v1/auto-iteration-settings",
        headers=testadmin_token_headers,
        json={"pass_threshold": 0.1, "reason": "越界阈值"},
    )
    assert response.status_code == 422, response.text


def test_patch_rejects_missing_reason(client: TestClient, testadmin_token_headers):
    response = client.patch(
        "/api/v1/auto-iteration-settings",
        headers=testadmin_token_headers,
        json={"pass_threshold": 0.7, "reason": ""},
    )
    assert response.status_code == 422, response.text


def test_patch_detects_concurrent_change(client: TestClient, testadmin_token_headers):
    first = client.patch(
        "/api/v1/auto-iteration-settings",
        headers=testadmin_token_headers,
        json={"pass_threshold": 0.7, "reason": "第一次修改"},
    )
    assert first.status_code == 200, first.text
    conflict = client.patch(
        "/api/v1/auto-iteration-settings",
        headers=testadmin_token_headers,
        json={
            "pass_threshold": 0.8,
            "expected_values": {"pass_threshold": 0.6},
            "reason": "基于旧值修改",
        },
    )
    assert conflict.status_code == 409, conflict.text
