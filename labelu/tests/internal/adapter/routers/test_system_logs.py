from labelu.internal.application.service import system_operation_log as service
from labelu.internal.common.config import settings
from labelu.internal.domain.models.user import User


def test_operation_logs_list_detail_and_permissions(
    client, db, testuser_token_headers, monkeypatch
):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "test@example.com")
    testuser = db.query(User).filter(User.username == "test@example.com").one()
    service.persist_request_operation_log(
        category=service.CATEGORY_USER,
        action="保存样本标注",
        user_id=testuser.id,
        request_id="test-request",
        method="PATCH",
        path="/api/v1/tasks/1/samples/1",
        status_code=200,
        duration_ms=12,
        query={"sample_id": 1},
        body_summary='{"data":{"result":"{}"}}',
    )

    response = client.get(
        "/api/v1/system-logs",
        params={"category": "user", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["meta_data"]["total"] == 1
    item = payload["data"][0]
    assert item["action"] == "保存样本标注"
    assert item["username"] == "test@example.com"
    assert item["status_code"] == 200

    detail = client.get(
        f"/api/v1/system-logs/{item['id']}", headers=testuser_token_headers
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["detail"]["method"] == "PATCH"
    assert detail.json()["data"]["detail"]["body"]["data"]["result"] == "{}"

    system_response = client.get(
        "/api/v1/system-logs",
        params={"category": "system"},
        headers=testuser_token_headers,
    )
    assert system_response.status_code == 200
    assert system_response.json()["meta_data"]["total"] == 0


def test_operation_logs_require_authentication(client):
    assert client.get("/api/v1/system-logs").status_code == 401
