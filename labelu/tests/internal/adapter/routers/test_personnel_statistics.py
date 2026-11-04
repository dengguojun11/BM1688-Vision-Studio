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
    assert (
        personnel_statistics._cluster_seconds([start, datetime(2026, 11, 2, 16, 50, 0)])
        == 300
    )
    assert (
        personnel_statistics._cluster_seconds([start, datetime(2026, 11, 2, 18, 30, 0)])
        == 0
    )


def _rect_payload(boxes):
    return {"result": {"rectTool": {"result": boxes}}}


def test_rect_change_count_only_counts_real_rect_changes():
    previous = _rect_payload(
        [{"id": "a", "x": 1, "y": 2, "width": 3, "height": 4, "label": "person"}]
    )
    modified_and_added = _rect_payload(
        [
            {"id": "a", "x": 2, "y": 2, "width": 3, "height": 4, "label": "person"},
            {"id": "b", "x": 10, "y": 20, "width": 30, "height": 40, "label": "car"},
        ]
    )
    tag_only = _rect_payload(
        [{"id": "a", "x": 1, "y": 2, "width": 3, "height": 4, "label": "person"}]
    )
    assert personnel_statistics._rect_change_count(previous, modified_and_added) == 2
    assert personnel_statistics._rect_change_count(previous, tag_only) == 0
    removed = _rect_payload([])
    assert personnel_statistics._rect_change_count(previous, removed) == 1


def test_online_intervals_merge_and_cap():
    start = datetime(2026, 11, 4, 9, 0, 0)
    assert (
        personnel_statistics._merge_intervals(
            [
                (start, datetime(2026, 11, 4, 10, 0, 0)),
                (datetime(2026, 11, 4, 9, 30, 0), datetime(2026, 11, 4, 11, 0, 0)),
                (datetime(2026, 11, 4, 11, 30, 0), datetime(2026, 11, 4, 12, 0, 0)),
            ]
        )
        == 2 * 3600 + 30 * 60
    )


def test_heartbeat_creates_online_session(
    client, db, testuser_token_headers, monkeypatch
):
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", "test@example.com")
    response = client.post(
        "/api/v1/user-activity/heartbeat",
        json={
            "session_id": "sim-test-session",
            "path": "/workspace/workbench",
            "active_seconds": 60,
        },
        headers=testuser_token_headers,
    )
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["session_id"] == "sim-test-session"
    db.expire_all()
    session = (
        db.query(user_activity.UserActivitySession)
        .filter_by(session_id="sim-test-session")
        .one()
    )
    assert session.active_seconds == 60
    assert session.active_date is not None
    online = user_activity.online_users(db)
    assert any(item["session_id"] == "sim-test-session" for item in online)

    dashboard = client.get(
        "/api/v1/personnel-statistics/dashboard",
        headers=testuser_token_headers,
    )
    assert dashboard.status_code == 200
    assert dashboard.json()["data"]["summary"]["online_now"] == 1
    user_row = next(
        row
        for row in dashboard.json()["data"]["users"]
        if row["username"] == "test@example.com"
    )
    assert user_row["online_seconds"] == 60
