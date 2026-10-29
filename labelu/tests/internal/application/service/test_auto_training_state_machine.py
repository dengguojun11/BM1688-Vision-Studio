from __future__ import annotations

import pytest

from labelu.internal.application.service.auto_training_state_machine import (
    AutoTrainingConflict,
    PlanStatus,
    RoundStatus,
    apply_quality_result,
    assert_plan_transition,
    assert_round_transition,
    build_execution_signature,
)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (PlanStatus.DRAFT_COMPLETE, PlanStatus.PENDING_APPROVAL),
        (PlanStatus.PENDING_APPROVAL, PlanStatus.ACTIVE),
        (PlanStatus.ACTIVE, PlanStatus.ROUND_ACTIVE),
        (PlanStatus.ROUND_ACTIVE, PlanStatus.WAITING_FOR_DATA),
        (PlanStatus.AUTO_QUALITY_REACHED, PlanStatus.ACTIVE),
    ],
)
def test_legal_plan_transitions(before, after):
    assert_plan_transition(before, after)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (PlanStatus.DRAFT_INCOMPLETE, PlanStatus.ACTIVE),
        (PlanStatus.ARCHIVED, PlanStatus.ACTIVE),
        (PlanStatus.AUTO_QUALITY_REACHED, PlanStatus.ROUND_ACTIVE),
    ],
)
def test_illegal_plan_transitions_are_rejected(before, after):
    with pytest.raises(AutoTrainingConflict):
        assert_plan_transition(before, after)


def test_round_state_machine_rejects_publish_from_diagnostic_branch():
    assert_round_transition(
        RoundStatus.QUALITY_FAILED, RoundStatus.MANUAL_BM1688_QUEUED
    )
    assert_round_transition(
        RoundStatus.MANUAL_EDGE_VALIDATING, RoundStatus.DIAGNOSTIC_COMPLETED
    )
    with pytest.raises(AutoTrainingConflict):
        assert_round_transition(
            RoundStatus.DIAGNOSTIC_COMPLETED, RoundStatus.PUBLISH_READY
        )


@pytest.mark.parametrize(
    ("recall", "expected_status", "auto_compile"),
    [
        (0.9499, RoundStatus.QUALITY_FAILED, False),
        (0.9500, RoundStatus.AUTO_QUALITY_REACHED, True),
        (1.0, RoundStatus.AUTO_QUALITY_REACHED, True),
    ],
)
def test_overall_recall_is_the_only_automatic_stop_gate(
    recall, expected_status, auto_compile
):
    decision = apply_quality_result(
        overall_recall=recall,
        evidence_complete=True,
        per_label_metrics={"rare_label": {"recall": 0.25}},
    )

    assert decision.round_status == expected_status
    assert decision.auto_compile is auto_compile
    assert decision.warnings == ["LABEL_RECALL_BELOW_AUTO_STOP_THRESHOLD:rare_label"]


def test_missing_approved_evidence_never_claims_quality_reached():
    decision = apply_quality_result(
        overall_recall=0.99,
        evidence_complete=False,
        per_label_metrics={},
    )

    assert decision.round_status == RoundStatus.QUALITY_EVIDENCE_INSUFFICIENT
    assert decision.auto_compile is False


def test_execution_signature_blocks_same_data_auto_tuning():
    values = {
        "plan_id": 1,
        "dataset_signature": "dataset-v1",
        "baseline_signature": "champion-v3",
        "recipe_signature": "recipe-v2",
        "hyperparams_signature": "hyper-v1",
        "quality_policy_signature": "quality-v5",
        "runtime_signature": "runtime-v1",
    }

    first = build_execution_signature(**values)
    second = build_execution_signature(**values)
    changed_data = build_execution_signature(
        **{**values, "dataset_signature": "dataset-v2"}
    )

    assert first == second
    assert first != changed_data


def test_quality_reached_requires_explicit_admin_resume():
    with pytest.raises(AutoTrainingConflict):
        assert_plan_transition(
            PlanStatus.AUTO_QUALITY_REACHED,
            PlanStatus.ACTIVE,
            actor_is_admin=False,
            reason="ordinary owner retry",
        )
    assert_plan_transition(
        PlanStatus.AUTO_QUALITY_REACHED,
        PlanStatus.ACTIVE,
        actor_is_admin=True,
        reason="approved new quality cycle",
    )
