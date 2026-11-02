from labelu.internal.common.config import settings


def test_data_analysis_requires_manager(client, testuser_token_headers, monkeypatch):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "test@example.com")
    response = client.get(
        "/api/v1/data-analysis/overview",
        params={"target_per_label": 20},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["summary"]["target_per_label"] == 20
    assert isinstance(data["contracts"], list)
    assert isinstance(data["gaps"], list)
    assert isinstance(data["global_tools"], list)


def test_data_analysis_rejects_annotation_users(client, testuser_token_headers, monkeypatch):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "")
    response = client.get(
        "/api/v1/data-analysis/overview",
        headers=testuser_token_headers,
    )
    assert response.status_code == 403
