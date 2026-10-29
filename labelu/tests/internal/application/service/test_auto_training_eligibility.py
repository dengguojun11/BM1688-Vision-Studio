from __future__ import annotations

from dataclasses import replace

import pytest

from labelu.internal.application.service.auto_training_eligibility import (
    EligibilityCandidate,
    EligibilityDecision,
    evaluate_task_candidates,
)


def _candidate(
    sample_id: int,
    *,
    state: str = "DONE",
    deleted: bool = False,
    readable: bool = True,
    annotation_valid: bool = True,
    contract_matches: bool = True,
    authorized: bool = True,
    withdrawn: bool = False,
    duplicate: bool = False,
    source_type: str = "internal",
    annotation_sha256: str = "a" * 64,
    reviewed_sha256: str | None = None,
) -> EligibilityCandidate:
    return EligibilityCandidate(
        sample_id=sample_id,
        task_id=10,
        state=state,
        deleted=deleted,
        readable=readable,
        annotation_valid=annotation_valid,
        contract_matches=contract_matches,
        authorized=authorized,
        withdrawn=withdrawn,
        duplicate=duplicate,
        source_type=source_type,
        annotation_sha256=annotation_sha256,
        reviewed_sha256=reviewed_sha256,
    )


@pytest.mark.parametrize(
    ("count", "qualified", "included"),
    [(49, False, 0), (50, True, 50), (51, True, 51)],
)
def test_single_task_threshold_is_atomic(count, qualified, included):
    result = evaluate_task_candidates(
        [_candidate(i) for i in range(1, count + 1)], min_task_samples=50
    )

    assert result.qualified is qualified
    assert len(result.included_sample_ids) == included


def test_multiple_under_threshold_tasks_cannot_be_combined():
    task_a = [_candidate(i) for i in range(1, 31)]
    task_b = [replace(_candidate(i), task_id=11) for i in range(31, 61)]

    first = evaluate_task_candidates(task_a, min_task_samples=50)
    second = evaluate_task_candidates(task_b, min_task_samples=50)

    assert first.qualified is False
    assert second.qualified is False
    assert first.included_sample_ids == []
    assert second.included_sample_ids == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"state": "NEW"}, "STATE_NOT_DONE"),
        ({"deleted": True}, "DELETED"),
        ({"readable": False}, "FILE_UNREADABLE"),
        ({"annotation_valid": False}, "ANNOTATION_INVALID"),
        ({"contract_matches": False}, "CONTRACT_MISMATCH"),
        ({"authorized": False}, "NOT_AUTHORIZED"),
        ({"withdrawn": True}, "WITHDRAWN"),
        ({"duplicate": True}, "DUPLICATE"),
        ({"reserved_for_evaluation": True}, "EVALUATION_RESERVED"),
    ],
)
def test_ineligible_reasons_are_explicit(changes, reason):
    candidate = replace(_candidate(1), **changes)

    result = evaluate_task_candidates([candidate], min_task_samples=1)

    assert result.decisions[0] == EligibilityDecision(
        sample_id=1, eligible=False, reason_code=reason
    )


def test_third_party_annotation_requires_current_hash_review():
    pending = _candidate(1, source_type="third_party_auto")
    stale = _candidate(
        2,
        source_type="third_party_auto",
        annotation_sha256="b" * 64,
        reviewed_sha256="a" * 64,
    )
    accepted = _candidate(
        3,
        source_type="third_party_auto",
        annotation_sha256="c" * 64,
        reviewed_sha256="c" * 64,
    )

    result = evaluate_task_candidates([pending, stale, accepted], min_task_samples=1)

    assert [item.reason_code for item in result.decisions] == [
        "HUMAN_REVIEW_REQUIRED",
        "HUMAN_REVIEW_STALE",
        "ELIGIBLE",
    ]
    assert result.included_sample_ids == [3]


def test_newly_completed_samples_are_not_added_to_frozen_previous_round():
    first = evaluate_task_candidates(
        [_candidate(i) for i in range(1, 51)], min_task_samples=50
    )
    frozen = tuple(first.included_sample_ids)

    second = evaluate_task_candidates(
        [_candidate(i) for i in range(1, 56)], min_task_samples=50
    )

    assert frozen == tuple(range(1, 51))
    assert second.included_sample_ids == list(range(1, 56))
    assert frozen == tuple(range(1, 51))
