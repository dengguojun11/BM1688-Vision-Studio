from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json


class AutoTrainingConflict(ValueError):
    pass


class PlanStatus(str, Enum):
    DRAFT_INCOMPLETE = "DRAFT_INCOMPLETE"
    DRAFT_COMPLETE = "DRAFT_COMPLETE"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    WAITING_FOR_DATA = "WAITING_FOR_DATA"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    WAITING_FOR_COVERAGE = "WAITING_FOR_COVERAGE"
    POLICY_REVIEW_REQUIRED = "POLICY_REVIEW_REQUIRED"
    ROUND_ACTIVE = "ROUND_ACTIVE"
    AUTO_QUALITY_REACHED = "AUTO_QUALITY_REACHED"
    PAUSED_MANUAL = "PAUSED_MANUAL"
    PAUSED_SAFETY = "PAUSED_SAFETY"
    DISABLED = "DISABLED"
    ARCHIVED = "ARCHIVED"


class RoundStatus(str, Enum):
    PREPARING = "PREPARING"
    WAITING_FOR_DATA = "WAITING_FOR_DATA"
    WAITING_FOR_REVIEW = "WAITING_FOR_REVIEW"
    WAITING_FOR_COVERAGE = "WAITING_FOR_COVERAGE"
    SNAPSHOT_READY = "SNAPSHOT_READY"
    QUEUED = "QUEUED"
    TRAINING = "TRAINING"
    TRAIN_FAILED = "TRAIN_FAILED"
    TRAIN_CANCELLED = "TRAIN_CANCELLED"
    PT_EVALUATING = "PT_EVALUATING"
    QUALITY_EVIDENCE_INSUFFICIENT = "QUALITY_EVIDENCE_INSUFFICIENT"
    QUALITY_FAILED = "QUALITY_FAILED"
    PT_CANDIDATE = "PT_CANDIDATE"
    AUTO_QUALITY_REACHED = "AUTO_QUALITY_REACHED"
    BM1688_QUEUED = "BM1688_QUEUED"
    BM1688_COMPILING = "BM1688_COMPILING"
    BM1688_FAILED = "BM1688_FAILED"
    BMODEL_EVALUATING = "BMODEL_EVALUATING"
    BMODEL_QUALITY_FAILED = "BMODEL_QUALITY_FAILED"
    EDGE_VALIDATION_QUEUED = "EDGE_VALIDATION_QUEUED"
    EDGE_VALIDATING = "EDGE_VALIDATING"
    EDGE_VALIDATION_FAILED = "EDGE_VALIDATION_FAILED"
    PUBLISH_READY = "PUBLISH_READY"
    PUBLISHING = "PUBLISHING"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    PUBLISHED = "PUBLISHED"
    MANUAL_BM1688_QUEUED = "MANUAL_BM1688_QUEUED"
    MANUAL_BM1688_COMPILING = "MANUAL_BM1688_COMPILING"
    MANUAL_BMODEL_EVALUATING = "MANUAL_BMODEL_EVALUATING"
    MANUAL_EDGE_VALIDATING = "MANUAL_EDGE_VALIDATING"
    DIAGNOSTIC_COMPLETED = "DIAGNOSTIC_COMPLETED"
    DIAGNOSTIC_FAILED = "DIAGNOSTIC_FAILED"


_PLAN_TRANSITIONS = {
    PlanStatus.DRAFT_INCOMPLETE: {PlanStatus.DRAFT_COMPLETE},
    PlanStatus.DRAFT_COMPLETE: {PlanStatus.DRAFT_INCOMPLETE, PlanStatus.PENDING_APPROVAL},
    PlanStatus.PENDING_APPROVAL: {PlanStatus.DRAFT_COMPLETE, PlanStatus.ACTIVE},
    PlanStatus.ACTIVE: {
        PlanStatus.ROUND_ACTIVE,
        PlanStatus.WAITING_FOR_DATA,
        PlanStatus.WAITING_FOR_REVIEW,
        PlanStatus.WAITING_FOR_COVERAGE,
        PlanStatus.PAUSED_MANUAL,
        PlanStatus.PAUSED_SAFETY,
        PlanStatus.DISABLED,
    },
    PlanStatus.ROUND_ACTIVE: {
        PlanStatus.ACTIVE,
        PlanStatus.WAITING_FOR_DATA,
        PlanStatus.WAITING_FOR_REVIEW,
        PlanStatus.WAITING_FOR_COVERAGE,
        PlanStatus.AUTO_QUALITY_REACHED,
        PlanStatus.PAUSED_SAFETY,
    },
    PlanStatus.WAITING_FOR_DATA: {PlanStatus.ACTIVE, PlanStatus.PAUSED_MANUAL},
    PlanStatus.WAITING_FOR_REVIEW: {PlanStatus.ACTIVE, PlanStatus.PAUSED_MANUAL},
    PlanStatus.WAITING_FOR_COVERAGE: {PlanStatus.ACTIVE, PlanStatus.PAUSED_MANUAL},
    PlanStatus.POLICY_REVIEW_REQUIRED: {PlanStatus.ACTIVE, PlanStatus.PAUSED_MANUAL},
    PlanStatus.AUTO_QUALITY_REACHED: {PlanStatus.ACTIVE},
    PlanStatus.PAUSED_MANUAL: {PlanStatus.ACTIVE, PlanStatus.ARCHIVED},
    PlanStatus.PAUSED_SAFETY: {PlanStatus.ACTIVE, PlanStatus.ARCHIVED},
    PlanStatus.DISABLED: {PlanStatus.ACTIVE, PlanStatus.ARCHIVED},
    PlanStatus.ARCHIVED: set(),
}


_ROUND_TRANSITIONS = {
    RoundStatus.PREPARING: {
        RoundStatus.WAITING_FOR_DATA,
        RoundStatus.WAITING_FOR_REVIEW,
        RoundStatus.WAITING_FOR_COVERAGE,
        RoundStatus.SNAPSHOT_READY,
    },
    RoundStatus.SNAPSHOT_READY: {RoundStatus.QUEUED},
    RoundStatus.QUEUED: {RoundStatus.TRAINING, RoundStatus.TRAIN_CANCELLED},
    RoundStatus.TRAINING: {
        RoundStatus.TRAIN_FAILED,
        RoundStatus.TRAIN_CANCELLED,
        RoundStatus.PT_EVALUATING,
    },
    RoundStatus.PT_EVALUATING: {
        RoundStatus.QUALITY_EVIDENCE_INSUFFICIENT,
        RoundStatus.QUALITY_FAILED,
        RoundStatus.PT_CANDIDATE,
        RoundStatus.AUTO_QUALITY_REACHED,
    },
    RoundStatus.PT_CANDIDATE: {RoundStatus.BM1688_QUEUED},
    RoundStatus.AUTO_QUALITY_REACHED: {RoundStatus.BM1688_QUEUED},
    RoundStatus.BM1688_QUEUED: {RoundStatus.BM1688_COMPILING},
    RoundStatus.BM1688_COMPILING: {
        RoundStatus.BM1688_FAILED,
        RoundStatus.BMODEL_EVALUATING,
    },
    RoundStatus.BMODEL_EVALUATING: {
        RoundStatus.BMODEL_QUALITY_FAILED,
        RoundStatus.EDGE_VALIDATION_QUEUED,
    },
    RoundStatus.EDGE_VALIDATION_QUEUED: {RoundStatus.EDGE_VALIDATING},
    RoundStatus.EDGE_VALIDATING: {
        RoundStatus.EDGE_VALIDATION_FAILED,
        RoundStatus.PUBLISH_READY,
    },
    RoundStatus.PUBLISH_READY: {RoundStatus.PUBLISHING},
    RoundStatus.PUBLISHING: {RoundStatus.PUBLISHED, RoundStatus.PUBLISH_FAILED},
    RoundStatus.PUBLISH_FAILED: {RoundStatus.PUBLISHING},
    RoundStatus.QUALITY_FAILED: {RoundStatus.MANUAL_BM1688_QUEUED},
    RoundStatus.MANUAL_BM1688_QUEUED: {RoundStatus.MANUAL_BM1688_COMPILING},
    RoundStatus.MANUAL_BM1688_COMPILING: {
        RoundStatus.MANUAL_BMODEL_EVALUATING,
        RoundStatus.DIAGNOSTIC_FAILED,
    },
    RoundStatus.MANUAL_BMODEL_EVALUATING: {
        RoundStatus.MANUAL_EDGE_VALIDATING,
        RoundStatus.DIAGNOSTIC_FAILED,
    },
    RoundStatus.MANUAL_EDGE_VALIDATING: {
        RoundStatus.DIAGNOSTIC_COMPLETED,
        RoundStatus.DIAGNOSTIC_FAILED,
    },
}


def assert_plan_transition(
    before: PlanStatus | str,
    after: PlanStatus | str,
    *,
    actor_is_admin: bool = True,
    reason: str | None = "state transition",
) -> None:
    before = PlanStatus(before)
    after = PlanStatus(after)
    if after not in _PLAN_TRANSITIONS.get(before, set()):
        raise AutoTrainingConflict(f"invalid plan transition: {before} -> {after}")
    if before == PlanStatus.AUTO_QUALITY_REACHED:
        if not actor_is_admin:
            raise AutoTrainingConflict("quality reached plans require admin resume")
        if not reason or not reason.strip():
            raise AutoTrainingConflict("quality reached resume requires a reason")


def assert_round_transition(before: RoundStatus | str, after: RoundStatus | str) -> None:
    before = RoundStatus(before)
    after = RoundStatus(after)
    if after not in _ROUND_TRANSITIONS.get(before, set()):
        raise AutoTrainingConflict(f"invalid round transition: {before} -> {after}")


@dataclass(frozen=True)
class QualityDecision:
    round_status: RoundStatus
    auto_compile: bool
    warnings: list[str]


def apply_quality_result(
    *,
    overall_recall: float,
    evidence_complete: bool,
    per_label_metrics: dict[str, dict[str, float]],
    auto_stop_threshold: float = 0.95,
) -> QualityDecision:
    warnings = [
        f"LABEL_RECALL_BELOW_AUTO_STOP_THRESHOLD:{label}"
        for label, metrics in sorted(per_label_metrics.items())
        if float(metrics.get("recall", 0.0)) < auto_stop_threshold
    ]
    if not evidence_complete:
        return QualityDecision(
            RoundStatus.QUALITY_EVIDENCE_INSUFFICIENT, False, warnings
        )
    if overall_recall >= auto_stop_threshold:
        return QualityDecision(RoundStatus.AUTO_QUALITY_REACHED, True, warnings)
    return QualityDecision(RoundStatus.QUALITY_FAILED, False, warnings)


def build_execution_signature(**parts: object) -> str:
    payload = json.dumps(parts, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
