import pytest

from labelu.internal.application.response.edge_queue_center import EdgeQueueJobListResponse
from labelu.internal.application.service import edge_queue_center as edge_queue_center_service


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/yolo-edge-validation/queue/summary"),
        ("get", "/api/v1/yolo-edge-validation/jobs/queue-summary"),
        ("get", "/api/v1/yolo-edge-validation/queue/jobs"),
        ("post", "/api/v1/yolo-edge-validation/jobs/1/cancel"),
        ("post", "/api/v1/yolo-edge-validation/jobs/1/retry"),
        ("get", "/api/v1/edge-devices/unknown-device/agent-bootstrap"),
    ],
)
def test_edge_control_plane_routes_require_system_admin(
    client, testuser_token_headers, method, path
):
    response = getattr(client, method)(path, headers=testuser_token_headers)

    assert response.status_code == 403, response.text


def test_edge_queue_search_accepts_q_and_legacy_keyword_with_q_precedence(
    client, testadmin_token_headers, monkeypatch
):
    captured = []

    def fake_list_queue_jobs(db, *, current_user, query):
        captured.append(query)
        return EdgeQueueJobListResponse(page=query.page, size=query.size)

    monkeypatch.setattr(
        edge_queue_center_service, "list_queue_jobs", fake_list_queue_jobs
    )

    response = client.get(
        "/api/v1/yolo-edge-validation/queue/jobs",
        params={"q": "canonical", "keyword": "legacy", "page": 1, "size": 10},
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    assert captured[-1].keyword == "canonical"
    assert captured[-1].page == 1
    assert captured[-1].size == 10

    response = client.get(
        "/api/v1/yolo-edge-validation/queue/jobs",
        params={"keyword": "legacy"},
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    assert captured[-1].keyword == "legacy"
