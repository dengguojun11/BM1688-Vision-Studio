from __future__ import annotations

import pytest

from labelu.internal.application.command.edge_agent import RegisterEdgeAgentCommand
from labelu.internal.application.service import edge_agent
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.edge_agent import EdgeAgent

RETIRED_ERROR = {
    "msg": "该能力已下线，请使用任务、训练、BM1688 编译、真机验证和模型发布主流程",
    "err_code": 30004,
}


def _assert_retired(call) -> None:
    with pytest.raises(LabelUException) as captured:
        call()
    assert captured.value.status_code == 403
    assert captured.value.code == RETIRED_ERROR["err_code"]
    assert captured.value.msg == RETIRED_ERROR["msg"]


def test_edge_agent_service_writes_fail_before_database_access():
    _assert_retired(
        lambda: edge_agent.register(
            None,
            RegisterEdgeAgentCommand(
                agent_id="retired-agent",
                name="已退役代理",
                enrollment_token="irrelevant",
            ),
        )
    )


def test_edge_agent_http_surface_is_retired_without_persisting(client, db):
    before = db.query(EdgeAgent).count()
    response = client.post(
        "/api/v1/edge-agents/register",
        json={
            "agent_id": "retired-agent",
            "name": "已退役代理",
            "enrollment_token": "irrelevant",
        },
    )
    assert response.status_code == 403
    assert response.json() == RETIRED_ERROR
    db.expire_all()
    assert db.query(EdgeAgent).count() == before


def test_edge_agent_routes_are_absent_from_phase_one_openapi(client):
    assert not [
        path
        for path in client.app.openapi()["paths"]
        if path.startswith("/api/v1/edge-agents")
    ]
