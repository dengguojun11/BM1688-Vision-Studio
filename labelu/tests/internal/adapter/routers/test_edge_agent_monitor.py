import asyncio

from labelu.internal.adapter.routers import edge_agent as edge_agent_router
from labelu.internal.application.response.edge_agent import EdgeAgentResponse


def test_monitor_meta_data_size_matches_returned_rows(db, monkeypatch):
    row = EdgeAgentResponse(
        agent_id="camera-gate-01",
        name="东门摄像头",
        project_id=1,
        status="REGISTERED",
    )

    def fake_list_agents_page(db_arg, project_id, **kwargs):
        assert db_arg is db
        assert project_id is None
        assert kwargs == {
            "page": 2,
            "size": 20,
            "search": "东门",
            "user": None,
        }
        return [row], 41

    monkeypatch.setattr(
        edge_agent_router.service, "list_agents_page", fake_list_agents_page
    )

    response = asyncio.run(
        edge_agent_router.monitor(
            project_id=None,
            search="东门",
            page=2,
            size=20,
            authorization=None,
            db=db,
            current_user=None,
        )
    )

    assert response.meta_data.total == 41
    assert response.meta_data.page == 2
    assert response.meta_data.size == 1

