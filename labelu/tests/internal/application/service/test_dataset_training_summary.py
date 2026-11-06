"""Dataset training lineage labels for third-party flexible workflows."""

from __future__ import annotations

from types import SimpleNamespace

from labelu.internal.application.service.dataset_training_summary import _lineage_kind


def test_strategy_evolution_job_is_flexible_workflow():
    job = SimpleNamespace(source_system="strategy_evolution")

    assert _lineage_kind(job) == "FLEXIBLE"


def test_auto_iteration_job_is_auto():
    job = SimpleNamespace(
        source_system="internal",
        auto_iteration_root_id=12,
        auto_iteration_round_no=2,
    )

    assert _lineage_kind(job) == "AUTO"


def test_regular_job_is_manual():
    job = SimpleNamespace(
        source_system=None,
        auto_iteration_root_id=None,
        auto_iteration_round_no=None,
    )

    assert _lineage_kind(job) == "MANUAL"
