"""事件审核路由的鉴权与后海平台配置错误映射测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from labelu.internal.application.service import event_review as event_review_service


PROGRESS_URL = "/api/v1/event-review/progress"
LIST_URL = "/api/v1/event-review"
ACTION_URL = "/api/v1/event-review/1001/action"


def _clear_houhai_config(monkeypatch) -> None:
    monkeypatch.setattr(event_review_service, "OLD_BASE_URL", "")
    monkeypatch.setattr(event_review_service, "OLD_API_TOKEN", "")
    monkeypatch.setattr(event_review_service, "_runtime_token", None)


def test_progress_requires_authentication(client: TestClient):
    response = client.get(PROGRESS_URL)

    # HTTPBearer rejects a missing Authorization header before the JWT
    # dependency runs, so the application's existing HTTP error mapping is 403.
    assert response.status_code == 403


def test_progress_returns_503_when_houhai_is_not_configured(
    client: TestClient,
    testadmin_token_headers,
    monkeypatch,
):
    _clear_houhai_config(monkeypatch)

    response = client.get(PROGRESS_URL, headers=testadmin_token_headers)

    assert response.status_code == 503
    assert "HOUHAI_BASE_URL" in response.json()["msg"]


def test_list_returns_503_when_houhai_is_not_configured(
    client: TestClient,
    testadmin_token_headers,
    monkeypatch,
):
    _clear_houhai_config(monkeypatch)

    response = client.get(LIST_URL, headers=testadmin_token_headers)

    assert response.status_code == 503
    assert "HOUHAI_BASE_URL" in response.json()["msg"]


def test_list_accepts_q_alias_and_forwards_it_as_search_value(
    client: TestClient,
    testadmin_token_headers,
    monkeypatch,
):
    monkeypatch.setattr(event_review_service, "OLD_BASE_URL", "https://houhai.test")
    monkeypatch.setattr(event_review_service, "OLD_API_TOKEN", "test-token")
    monkeypatch.setattr(event_review_service, "_runtime_token", None)
    captured = {}

    def fake_call_old_api(endpoint, fields=None, method="POST"):
        captured.update(fields or {})
        return {
            "body": {
                "list": [{"id": "1001", "eventName": "安全帽告警"}],
                "paging": {"total": 1},
            },
            "status": 0,
        }

    monkeypatch.setattr(event_review_service, "_call_old_api", fake_call_old_api)

    response = client.get(
        LIST_URL,
        params={"q": "安全帽"},
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    assert captured["searchValue"] == "安全帽"
    assert response.json()["meta_data"]["total"] == 1


def test_list_keeps_keyword_compatibility_and_prefers_q_when_both_are_sent(
    client: TestClient,
    testadmin_token_headers,
    monkeypatch,
):
    monkeypatch.setattr(event_review_service, "OLD_BASE_URL", "https://houhai.test")
    monkeypatch.setattr(event_review_service, "OLD_API_TOKEN", "test-token")
    monkeypatch.setattr(event_review_service, "_runtime_token", None)
    captured = {}

    def fake_call_old_api(endpoint, fields=None, method="POST"):
        captured.update(fields or {})
        return {"body": {"list": [], "paging": {"total": 0}}, "status": 0}

    monkeypatch.setattr(event_review_service, "_call_old_api", fake_call_old_api)

    legacy_response = client.get(
        LIST_URL,
        params={"keyword": "旧字段"},
        headers=testadmin_token_headers,
    )
    assert legacy_response.status_code == 200, legacy_response.text
    assert captured["searchValue"] == "旧字段"

    both_response = client.get(
        LIST_URL,
        params={"q": "新字段", "keyword": "旧字段"},
        headers=testadmin_token_headers,
    )
    assert both_response.status_code == 200, both_response.text
    assert captured["searchValue"] == "新字段"


def test_progress_returns_503_when_houhai_token_is_not_configured(
    client: TestClient,
    testadmin_token_headers,
    monkeypatch,
):
    monkeypatch.setattr(event_review_service, "OLD_BASE_URL", "https://houhai.test")
    monkeypatch.setattr(event_review_service, "OLD_API_TOKEN", "")
    monkeypatch.setattr(event_review_service, "_runtime_token", None)

    response = client.get(PROGRESS_URL, headers=testadmin_token_headers)

    assert response.status_code == 503
    assert "HOUHAI_API_TOKEN" in response.json()["msg"]


def test_skip_action_remains_local_when_houhai_is_not_configured(
    client: TestClient,
    testadmin_token_headers,
    monkeypatch,
):
    _clear_houhai_config(monkeypatch)

    response = client.post(
        ACTION_URL,
        headers=testadmin_token_headers,
        json={"event_id": 1001, "action": "skip"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "skipped"


def test_progress_keeps_success_response_when_external_call_is_available(
    client: TestClient,
    testadmin_token_headers,
    monkeypatch,
):
    monkeypatch.setattr(event_review_service, "OLD_BASE_URL", "https://houhai.test")
    monkeypatch.setattr(event_review_service, "OLD_API_TOKEN", "test-token")
    monkeypatch.setattr(event_review_service, "_runtime_token", None)

    def fake_call_old_api(endpoint, fields=None, method="POST"):
        assert endpoint == "/iot-api/workform/alarm/commonAiAlarm/list"
        assert method == "POST"
        return {
            "body": {
                "list": [{"id": "1001"}],
                "paging": {"total": 4},
            },
            "status": 0,
        }

    monkeypatch.setattr(event_review_service, "_call_old_api", fake_call_old_api)

    response = client.get(PROGRESS_URL, headers=testadmin_token_headers)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "total_pending": 4,
        "reviewing_count": 0,
        "done_today": 0,
        "current_batch": 0,
        "batch_total": 4,
    }
