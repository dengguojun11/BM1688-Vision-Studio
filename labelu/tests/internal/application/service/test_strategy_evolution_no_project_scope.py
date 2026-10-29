from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from labelu.internal.application.command.strategy_evolution import (
    CreateStrategyEvolutionTaskCommand,
)
from labelu.internal.application.command.strategy_task_integration import (
    CreateStrategyTaskIntegrationCommand,
)
from labelu.internal.application.service import strategy_evolution
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.main import app


def _payload() -> dict:
    return {
        "external_task_id": "strategy-no-project-1",
        "task_name": "第三方问题样本",
        "task_type": "threshold_experiment",
        "target": {
            "device_type": "BM1688",
            "event_type": "helmet",
            "modelId": "model-1",
            "scene_type": "yard",
            "strategy_id": "strategy-1",
        },
        "data_window": {
            "start_time": "2026-09-01 00:00:00",
            "end_time": "2026-09-02 00:00:00",
        },
    }


def test_strategy_evolution_command_has_no_project_field_and_rejects_new_scope():
    assert "project_id" not in CreateStrategyEvolutionTaskCommand.model_fields

    for key in ("project_id", "projectId"):
        with pytest.raises(ValidationError, match="不支持 project_id"):
            CreateStrategyEvolutionTaskCommand.model_validate(
                {**_payload(), key: 7}
            )


def test_strategy_task_integration_command_has_no_project_field():
    assert "project_id" not in CreateStrategyTaskIntegrationCommand.model_fields


def test_strategy_evolution_openapi_request_schema_has_no_project_field():
    schema = app.openapi()["components"]["schemas"]["CreateStrategyEvolutionTaskCommand"]
    assert "project_id" not in schema.get("properties", {})


def test_strategy_integration_model_has_no_project_write_attribute():
    assert not hasattr(StrategyTaskIntegration, "project_id")


def test_strategy_accept_and_callback_paths_do_not_create_project_workflows():
    source = inspect.getsource(strategy_evolution)
    assert "WorkflowInstance" not in source
    assert "crud_workflow" not in source
    assert "_ensure_strategy_workflow" not in source
    assert "_record_platform_callback_workflow" not in source
    assert "strategy_workflow" not in source
