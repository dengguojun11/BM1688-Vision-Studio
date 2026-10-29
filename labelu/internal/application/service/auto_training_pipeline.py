from __future__ import annotations

from enum import Enum
from typing import Callable

from .auto_training_state_machine import RoundStatus


class PipelineConflict(ValueError):
    pass


class PipelineOrigin(str, Enum):
    AUTO_QUALITY = "AUTO_QUALITY"
    MANUAL_DIAGNOSTIC = "MANUAL_DIAGNOSTIC"


def create_bm1688_request(
    *,
    round_id: int,
    round_status: RoundStatus,
    actor_is_admin: bool,
    reason: str | None,
    existing_execution_signatures: set[str],
    factory: Callable[..., object],
):
    execution_signature = f"round:{round_id}:bm1688"
    if execution_signature in existing_execution_signatures:
        return None
    if round_status == RoundStatus.AUTO_QUALITY_REACHED:
        origin = PipelineOrigin.AUTO_QUALITY
    elif round_status == RoundStatus.QUALITY_FAILED:
        if not actor_is_admin:
            raise PipelineConflict("diagnostic compilation is admin-only")
        if not reason or not reason.strip():
            raise PipelineConflict("diagnostic compilation requires a reason")
        origin = PipelineOrigin.MANUAL_DIAGNOSTIC
    else:
        raise PipelineConflict(f"round status {round_status} cannot compile")
    return factory(
        round_id=round_id,
        origin=origin.value,
        reason=reason,
        execution_signature=execution_signature,
    )


def advance_after_bmodel(
    *, origin: PipelineOrigin, compile_succeeded: bool, quality_gate_passed: bool
) -> RoundStatus:
    if not compile_succeeded or not quality_gate_passed:
        return (
            RoundStatus.DIAGNOSTIC_FAILED
            if origin == PipelineOrigin.MANUAL_DIAGNOSTIC
            else RoundStatus.BMODEL_QUALITY_FAILED
        )
    return (
        RoundStatus.MANUAL_EDGE_VALIDATING
        if origin == PipelineOrigin.MANUAL_DIAGNOSTIC
        else RoundStatus.EDGE_VALIDATION_QUEUED
    )


def advance_after_edge_validation(
    *, origin: PipelineOrigin, status: str, all_gates_passed: bool
) -> RoundStatus:
    passed = status == "COMPLETED" and all_gates_passed
    if origin == PipelineOrigin.MANUAL_DIAGNOSTIC:
        return (
            RoundStatus.DIAGNOSTIC_COMPLETED
            if passed
            else RoundStatus.DIAGNOSTIC_FAILED
        )
    return RoundStatus.PUBLISH_READY if passed else RoundStatus.EDGE_VALIDATION_FAILED
