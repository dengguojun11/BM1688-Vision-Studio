from datetime import datetime

from labelu.internal.application.service import personnel_statistics
from labelu.internal.application.service import user_activity
from labelu.internal.common.config import settings
from labelu.internal.domain.models.sample_edit_log import SampleEditLog


def test_noop_state_only_annotation_is_not_effective_work():
    state_only = SampleEditLog(
        previous_data='{"result":"{}"}',
        new_data='{"result":"{}"}',
        previous_annotated_count=0,
        new_annotated_count=0,
    )
    content_change = SampleEditLog(
        previous_data='{"result":"{}"}',
        new_data='{"result":"{\\"rectTool\\":{}}"}',
        previous_annotated_count=0,
        new_annotated_count=1,
    )
    assert personnel_statistics._is_effective_annotation(state_only) is False
    assert personnel_statistics._is_effective_annotation(content_change) is True


def test_effective_work_clusters_only_business_events():
    start = datetime(2026, 11, 2, 16, 45, 0)
    assert personnel_statistics._cluster_seconds([start]) == 0
    assert personnel_statistics._cluster_seconds(
        [start, datetime(2026, 11, 2, 16, 50, 0)]
    ) == 300
    assert personnel_statistics._cluster_seconds(
        [start, datetime(2026, 11, 2, 18, 30, 0)]
    ) == 0


def test_heartbeat_creates_online_session(client, db, testuser_token_headers, monkeypatch):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "test@example.com")
    response = client.post(
        "/api/v1/user-activity/heartbeat",
        json={"session_id": "sim-test-session", "path": "/workspace/workbench"},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["session_id"] == "sim-test-session"
    online = user_activity.online_users(db)
    assert any(item["session_id"] == "sim-test-session" for item in online)

    dashboard = client.get(
        "/api/v1/personnel-statistics/dashboard",
        headers=testuser_token_headers,
    )
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["summary"]["online_now"] == 1
