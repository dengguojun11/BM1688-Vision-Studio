from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EligibilityCandidate:
    sample_id: int
    task_id: int
    state: str
    deleted: bool
    readable: bool
    annotation_valid: bool
    contract_matches: bool
    authorized: bool
    withdrawn: bool
    duplicate: bool
    source_type: str
    annotation_sha256: str
    reviewed_sha256: str | None
    reserved_for_evaluation: bool = False


@dataclass(frozen=True)
class EligibilityDecision:
    sample_id: int
    eligible: bool
    reason_code: str


@dataclass(frozen=True)
class TaskEligibilityResult:
    task_id: int | None
    qualified: bool
    included_sample_ids: list[int]
    decisions: list[EligibilityDecision]


def _decision(candidate: EligibilityCandidate) -> EligibilityDecision:
    if candidate.state != "DONE":
        reason = "STATE_NOT_DONE"
    elif candidate.deleted:
        reason = "DELETED"
    elif not candidate.readable:
        reason = "FILE_UNREADABLE"
    elif not candidate.annotation_valid:
        reason = "ANNOTATION_INVALID"
    elif not candidate.contract_matches:
        reason = "CONTRACT_MISMATCH"
    elif not candidate.authorized:
        reason = "NOT_AUTHORIZED"
    elif candidate.withdrawn:
        reason = "WITHDRAWN"
    elif candidate.duplicate:
        reason = "DUPLICATE"
    elif candidate.reserved_for_evaluation:
        reason = "EVALUATION_RESERVED"
    elif candidate.source_type == "third_party_auto" and not candidate.reviewed_sha256:
        reason = "HUMAN_REVIEW_REQUIRED"
    elif (
        candidate.source_type == "third_party_auto"
        and candidate.reviewed_sha256 != candidate.annotation_sha256
    ):
        reason = "HUMAN_REVIEW_STALE"
    else:
        return EligibilityDecision(candidate.sample_id, True, "ELIGIBLE")
    return EligibilityDecision(candidate.sample_id, False, reason)


def evaluate_task_candidates(
    candidates: list[EligibilityCandidate], *, min_task_samples: int
) -> TaskEligibilityResult:
    if min_task_samples < 1:
        raise ValueError("min_task_samples must be positive")
    task_ids = {item.task_id for item in candidates}
    if len(task_ids) > 1:
        raise ValueError("candidates must belong to one task")
    decisions = [_decision(item) for item in candidates]
    eligible_ids = [item.sample_id for item in decisions if item.eligible]
    qualified = len(eligible_ids) >= min_task_samples
    return TaskEligibilityResult(
        task_id=next(iter(task_ids), None),
        qualified=qualified,
        included_sample_ids=eligible_ids if qualified else [],
        decisions=decisions,
    )
