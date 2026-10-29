from __future__ import annotations

import asyncio

import pytest

from labelu.internal.application.service import (
    deployment,
    edge_agent,
    edge_device_job,
    organization,
    project,
    rule,
    task,
    workflow,
)
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.deployment import DeploymentRelease
from labelu.internal.domain.models.edge_agent import EdgeAgent, EdgeAgentRelease
from labelu.internal.domain.models.edge_device_job import EdgeDeviceJob
from labelu.internal.domain.models.organization import Organization
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.rule import RuleBinding, RuleTemplate, RuleVersion
from labelu.internal.domain.models.workflow import WorkflowInstance


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


def test_retired_service_entrypoints_fail_before_database_access():
    async_calls = [
        lambda: project.create(None, None, None),
        lambda: project.update(None, 1, None, None),
        lambda: project.add_member(None, 1, None, None),
        lambda: task.batch_assign_project(None, None, None),
        lambda: deployment.create(None, None, None),
        lambda: deployment.approve(None, 1, None, None),
        lambda: deployment.transition(None, 1, "start", None, None),
        lambda: deployment.report_target(None, 1, "device", None),
        lambda: deployment.observe(None, 1, "device", None),
        lambda: edge_device_job.create(None, cmd=None, current_user=None),
        lambda: edge_device_job.claim_next(None, cmd=None),
        lambda: edge_device_job.submit_result(None, job_id=1, cmd=None),
        lambda: rule.create_template(None, None, None),
        lambda: rule.create_version(None, 1, None, None),
        lambda: rule.publish_version(None, 1, None, None),
        lambda: rule.bind(None, 1, "retired-device", None, None),
        lambda: rule.unbind(None, 1, None, None),
        lambda: rule.rollback(None, 1, "retired-device", None, None),
        lambda: rule.report_device_signature(None, None, 1, None),
        lambda: workflow.create(None, None, None),
        lambda: workflow.transition(None, 1, None, None),
        lambda: workflow.replay(None, 1, None),
    ]
    for call in async_calls:
        _assert_retired(lambda call=call: asyncio.run(call()))

    sync_calls = [
        lambda: organization.create_organization(None, None, None),
        lambda: organization.create_scene(None, 1, None, None),
        lambda: edge_agent.register(None, None),
        lambda: edge_agent.heartbeat(None, None, None),
        lambda: edge_agent.capabilities(None, None, None),
        lambda: edge_agent.create_release(None, None),
        lambda: edge_agent.manifest(None, None, 1, "http://localhost"),
        lambda: edge_agent.chunk(None, None, 1, 0),
        lambda: edge_agent.verify(None, None, 1, None),
        lambda: edge_agent.activate(None, None, 1, None),
        lambda: edge_agent.rollback(None, None, 1, None),
        lambda: workflow.execute_step(None, 1, "prepare", None),
    ]
    for call in sync_calls:
        _assert_retired(call)


def test_retired_control_plane_is_absent_from_phase_one_openapi(client):
    paths = client.app.openapi()["paths"]
    retired_prefixes = (
        "/api/v1/projects",
        "/api/v1/organizations",
        "/api/v1/deployment-releases",
        "/api/v1/edge-agents",
        "/api/v1/edge-device-jobs",
        "/api/v1/rule-templates",
    )
    assert not [path for path in paths if path.startswith(retired_prefixes)]
    assert "/api/v1/model-versions/{model_version_id}/publish" not in paths
    assert "/api/v1/tasks/project-assignment" not in paths
    assert "/api/v1/tasks/{task_id}/project" not in paths
    assert "/api/v1/workflows" not in paths
    assert "/api/v1/workflows/{workflow_id}" not in paths
    assert "/api/v1/workflows/{workflow_id}/transition" not in paths
    assert "/api/v1/workflows/{workflow_id}/steps/{step_key}/execute" not in paths
    assert "/api/v1/workflows/{workflow_id}/replay" not in paths

    retained = {
        "/api/v1/model-versions",
        "/api/v1/edge-devices",
        "/api/v1/yolo-edge-validation/jobs",
        "/api/v1/yolo-bm1688-exports/{export_id}/board-validation",
        "/api/v1/yolo-aicm-publish/publish",
        "/api/v1/published-models",
        "/api/v1/workflows/outbox",
        "/api/v1/workflows/outbox/{outbox_id}/retry",
        "/api/v1/workflows/outbox/{outbox_id}/dispatch",
    }
    assert retained <= set(paths)
    assert {"get", "post"} <= set(paths["/api/v1/edge-devices"])
    assert {"get", "post"} <= set(paths["/api/v1/yolo-edge-validation/jobs"])


def test_retired_writes_return_one_error_and_do_not_create_rows(
    client, db, testadmin_token_headers
):
    before = {
        Project: db.query(Project).count(),
        Organization: db.query(Organization).count(),
        DeploymentRelease: db.query(DeploymentRelease).count(),
        EdgeAgent: db.query(EdgeAgent).count(),
        EdgeAgentRelease: db.query(EdgeAgentRelease).count(),
        EdgeDeviceJob: db.query(EdgeDeviceJob).count(),
        RuleTemplate: db.query(RuleTemplate).count(),
        RuleVersion: db.query(RuleVersion).count(),
        RuleBinding: db.query(RuleBinding).count(),
        WorkflowInstance: db.query(WorkflowInstance).count(),
    }
    admin = testadmin_token_headers
    calls = [
        ("post", "/api/v1/projects", {"name": "不得创建的项目"}, admin),
        ("post", "/api/v1/organizations", {"name": "不得创建的组织"}, admin),
        ("post", "/api/v1/projects/1/scenes", {"scene_key": "retired", "name": "不得创建的场景"}, admin),
        (
            "post", "/api/v1/deployment-releases",
            {"project_id": 1, "model_version_id": 1, "target_device_ids": ["retired-device"], "target_bundle_sha256": "0" * 64},
            admin,
        ),
        ("post", "/api/v1/deployment-releases/1/approve", {}, admin),
        (
            "post", "/api/v1/edge-agents/register",
            {"agent_id": "retired-agent", "name": "不得注册的代理", "enrollment_token": "retired-token"},
            {},
        ),
        (
            "post", "/api/v1/edge-agents/releases",
            {"version": "1.0.0", "artifact_path": "retired.bin", "artifact_sha256": "0" * 64, "agent_ids": []},
            admin,
        ),
        (
            "post",
            "/api/v1/edge-device-jobs",
            {"export_job_id": 1, "payload": {}},
            admin,
        ),
        (
            "post", "/api/v1/rule-templates",
            {"project_id": 1, "template_key": "retired-rule", "name": "不得创建的规则", "algorithm_key": "retired"},
            admin,
        ),
        (
            "post", "/api/v1/workflows",
            {"project_id": 1, "workflow_type": "expert_feedback", "idempotency_key": "retired-workflow", "steps": ["review"]},
            admin,
        ),
        ("post", "/api/v1/workflows/1/transition", {"status": "RUNNING"}, admin),
        ("post", "/api/v1/workflows/1/steps/review/execute", {}, admin),
        ("post", "/api/v1/workflows/1/replay", {}, admin),
    ]
    for method, path, payload, headers in calls:
        response = getattr(client, method)(path, json=payload, headers=headers)
        assert response.status_code == 403, (path, response.text)
        assert response.json() == RETIRED_ERROR

    db.expire_all()
    for model, expected in before.items():
        assert db.query(model).count() == expected
