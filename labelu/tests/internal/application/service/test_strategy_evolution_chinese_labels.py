"""Chinese labels/messages for the third-party strategy-task contract."""

from __future__ import annotations

import json
from types import SimpleNamespace

from labelu.internal.application.response.strategy_evolution import (
    StrategyEvolutionTaskAcceptedResponse,
)
from labelu.internal.application.service import strategy_evolution as service


def _row(**overrides):
    values = {
        "external_task_id": "strategy-task-cn-1",
        "task_name": "非机动车违停模型优化",
        "task_type": "model_capability",
        "source_package_id": None,
        "source_type": "third_party",
        "source_system": "strategy_evolution",
        "source_external_package_id": None,
        "owner": SimpleNamespace(username="open@system"),
        "target_json": "{}",
        "data_window_json": "{}",
        "experiment_json": "{}",
        "platform_result_json": json.dumps(
            {
                "message": "模型训练执行中",
                "model_training_status": "running",
                "model_training_jobs": [
                    {"job_id": 5, "progress_snapshot": "28,8696.24,0.91265"}
                ],
            }
        ),
        "platform_error_json": "{}",
        "platform_status": "model_training_created",
        "automation_stage": "training",
        "automation_status": "running",
        "outbound_callback_status": "failed",
        "outbound_callback_attempts": 4,
        "outbound_callback_next_retry_at": None,
        "outbound_callback_event_id": None,
        "platform_callback_event_id": None,
        "automation_attempts": 1,
        "automation_lease_expires_at": None,
        "platform_progress_json": None,
        "platform_finished_at": None,
        "created_at": None,
        "updated_at": None,
        "source_train_job_id": None,
        "source_export_id": None,
        "derived_train_job_id": 5,
        "derived_export_id": None,
        "derived_edge_validation_job_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_list_row_returns_chinese_labels():
    row = _row()

    response = service._to_list_row(row)

    assert response.task_type_label == "模型能力优化"
    assert response.platform_status_label == "已创建模型训练"
    assert response.automation_stage_label == "模型训练"
    assert response.automation_status_label == "执行中"
    assert response.outbound_callback_status_label == "发送失败"
    assert response.workflow_source_label == "柔性工作流"


def test_detail_returns_chinese_labels_and_result_label():
    row = _row()

    response = service._to_detail(row)

    assert response.task_type_label == "模型能力优化"
    assert response.platform_status_label == "已创建模型训练"
    assert response.outbound_callback_status_label == "发送失败"
    assert response.result is not None
    assert response.result["model_training_status_label"] == "训练中"


def test_last_message_prefers_chinese_result_message_over_raw_training_stdout():
    row = _row()

    message = service._row_last_message(
        row,
        result={
            "message": "模型训练执行中",
            "model_training_jobs": [
                {"job_id": 5, "progress_snapshot": "28,8696.24,0.91265"}
            ],
        },
        error=None,
        platform_status="model_training_created",
    )

    assert message == "模型训练执行中"


def test_accepted_response_has_chinese_status_label():
    response = StrategyEvolutionTaskAcceptedResponse(message="模型训练作业已存在，不会重复创建")

    assert response.status == "accepted"
    assert response.status_label == "已受理"
    assert response.message == "模型训练作业已存在，不会重复创建"
