"""Training creator/workflow marker used to tell flexible-workflow jobs apart."""

from __future__ import annotations

from types import SimpleNamespace

from labelu.internal.adapter.routers.training import _workflow_source


def test_strategy_evolution_job_is_marked_as_flexible_workflow():
    job = SimpleNamespace(source_type="third_party", source_system="strategy_evolution")

    assert _workflow_source(job) == ("flexible_workflow", "柔性工作流")


def test_manual_job_is_marked_as_manual():
    job = SimpleNamespace(source_type="internal", source_system=None)

    assert _workflow_source(job) == ("manual", "手动创建")


def test_auto_training_task_mode_is_marked_as_auto_iteration():
    job = SimpleNamespace(source_type="internal", source_system=None)

    assert _workflow_source(job, training_task_mode="AUTO") == (
        "auto_training",
        "自动迭代",
    )


def test_other_third_party_source_keeps_third_party_label():
    job = SimpleNamespace(source_type="third_party", source_system="other_system")

    assert _workflow_source(job) == ("third_party", "第三方接口")
