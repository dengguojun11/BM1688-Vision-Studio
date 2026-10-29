"""Versioned event naming and compatibility catalog for 7GAI.

The platform has a large amount of persisted ``event_name`` data using the
historical, un-namespaced form (for example ``training.worker.command.v1``).
This module deliberately does not rewrite those records.  New producers can
use the namespaced form while readers classify old values as ``legacy``.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


# A canonical event has a product prefix, a domain, at least one event
# segment, and an explicit positive schema version.  Segments are intentionally
# lower-case so event names are stable across Python, SQL and message brokers.
CANONICAL_EVENT_NAME_RE = re.compile(
    r"^7gai\.[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\.v[1-9][0-9]*$"
)

EventNameStatus = Literal["canonical", "legacy"]


# Persisted event names emitted by the current application.  This is a write
# compatibility allow-list, not a claim that these names are the preferred
# long-term contract.  New producers must use ``7gai.*`` names instead.
LEGACY_EVENT_NAMES = frozenset(
    {
        "auto.plan.config.approved.v1",
        "auto.plan.reconcile.requested.v1",
        "auto.plan.round.evaluate.v1",
        "auto.plan.scan.preview.requested.v1",
        "auto.round.bm1688.execute.v1",
        "auto.round.bm1688.requested.v1",
        "auto.round.edge.execute.v1",
        "auto.round.edge.requested.v1",
        "auto.round.quality.evaluate.v1",
        "auto.round.retry.requested.v1",
        "auto.round.stage.completed.v1",
        "auto.round.train.dispatch.v1",
        "sample.annotation.changed.v1",
        "sample.review.changed.v1",
        "task.created.v1",
        "task.training_ready.v1",
        "training.worker.command.v1",
        "training.worker.result.v1",
        "workflow.created",
        "workflow.replay",
        "workflow.replayed",
        "workflow.step.waiting",
        "workflow.step.succeeded",
        "outbox.replayed",
        "outbox.failed",
        "workflow.transition",
    }
)


@dataclass(frozen=True)
class EventSpec:
    """Catalog metadata for one canonical event contract."""

    name: str
    domain: str
    description: str
    producer: tuple[str, ...] = ()
    consumers: tuple[str, ...] = ()
    legacy_aliases: tuple[str, ...] = ()
    owner: str = "ARCH-05"
    replay_strategy: str = "idempotent_outbox"
    compatibility_cutoff: str = "TBD"
    deployment_evidence: str = "external/TBD"


# These entries are the first contracts migrated to the namespaced scheme.
# Existing producers still emit the aliases until their owning epic is
# migrated; readers should accept both spellings during that window.
EVENT_CATALOG: tuple[EventSpec, ...] = (
    EventSpec(
        name="7gai.training.worker.command.v1",
        domain="training",
        description="Dispatch one idempotent training worker command.",
        producer=("auto_training_dispatch",),
        consumers=("training_worker_runtime",),
        legacy_aliases=("training.worker.command.v1",),
    ),
    EventSpec(
        name="7gai.training.worker.result.v1",
        domain="training",
        description="Persist one training worker result or dead-letter outcome.",
        producer=("training_worker_runtime",),
        consumers=("auto_training_reconciliation",),
        legacy_aliases=("training.worker.result.v1",),
    ),
    EventSpec(
        name="7gai.workflow.transition.v1",
        domain="workflow",
        description="Record a workflow state transition.",
        producer=("workflow_service",),
        consumers=("workflow_audit", "workflow_executor"),
        legacy_aliases=("workflow.transition",),
    ),
    EventSpec(
        name="7gai.workflow.created.v1",
        domain="workflow",
        description="Create a workflow and enqueue its initial dispatch.",
        producer=("workflow_service",),
        consumers=("workflow_executor",),
        legacy_aliases=("workflow.created",),
    ),
    EventSpec(
        name="7gai.workflow.replay.v1",
        domain="workflow",
        description="Replay a dead-letter workflow.",
        producer=("workflow_service",),
        consumers=("workflow_executor",),
        legacy_aliases=("workflow.replay",),
    ),
    EventSpec(
        name="7gai.workflow.replayed.v1",
        domain="workflow",
        description="Record a workflow replay transition.",
        producer=("workflow_service",),
        consumers=("workflow_audit",),
        legacy_aliases=("workflow.replayed",),
    ),
    EventSpec(
        name="7gai.workflow.step.waiting.v1",
        domain="workflow",
        description="Record a workflow step waiting for a prerequisite.",
        producer=("workflow_service",),
        consumers=("workflow_audit",),
        legacy_aliases=("workflow.step.waiting",),
    ),
    EventSpec(
        name="7gai.workflow.step.succeeded.v1",
        domain="workflow",
        description="Record a successful workflow step.",
        producer=("workflow_service",),
        consumers=("workflow_audit",),
        legacy_aliases=("workflow.step.succeeded",),
    ),
    EventSpec(
        name="7gai.workflow.outbox.replayed.v1",
        domain="workflow",
        description="Record an Outbox retry or replay.",
        producer=("workflow_service",),
        consumers=("workflow_audit", "workflow_executor"),
        legacy_aliases=("outbox.replayed",),
    ),
    EventSpec(
        name="7gai.workflow.outbox.failed.v1",
        domain="workflow",
        description="Record an Outbox failure or dead-letter transition.",
        producer=("workflow_service",),
        consumers=("workflow_audit",),
        legacy_aliases=("outbox.failed",),
    ),
    EventSpec(
        name="7gai.workflow.callback.dispatch.v1",
        domain="workflow",
        description="Dispatch one idempotent third-party callback delivery.",
        producer=("strategy_evolution_callback_bridge",),
        consumers=("workflow_callback_worker",),
    ),
    EventSpec(
        name="7gai.workflow.callback.succeeded.v1",
        domain="workflow",
        description="Record a successful third-party callback delivery.",
        producer=("workflow_callback_worker",),
        consumers=("workflow_audit",),
    ),
    EventSpec(
        name="7gai.workflow.callback.failed.v1",
        domain="workflow",
        description="Record a retryable or dead-lettered callback delivery.",
        producer=("workflow_callback_worker",),
        consumers=("workflow_audit", "workflow_watchdog"),
    ),
    EventSpec(
        name="7gai.workflow.thresholds.applied.v1",
        domain="workflow",
        description="Record one approved threshold recommendation applied to a model export.",
        producer=("workflow_service",),
        consumers=("workflow_audit", "deployment_config"),
    ),
    EventSpec(
        name="7gai.workflow.strategy.callback_received.v1",
        domain="workflow",
        description="Record one inbound strategy platform callback.",
        producer=("strategy_platform_callback",),
        consumers=("strategy_evolution", "workflow_audit"),
    ),
)


def _validate_catalog(specs: tuple[EventSpec, ...]) -> None:
    names: set[str] = set()
    aliases: set[str] = set()
    for item in specs:
        if item.name in names:
            raise ValueError(f"duplicate canonical event: {item.name}")
        names.add(item.name)
        if not CANONICAL_EVENT_NAME_RE.fullmatch(item.name):
            raise ValueError(f"invalid canonical event in catalog: {item.name}")
        for alias in item.legacy_aliases:
            if not alias or alias in aliases or alias in names:
                raise ValueError(f"duplicate event alias: {alias}")
            aliases.add(alias)


_validate_catalog(EVENT_CATALOG)
_CANONICAL_BY_NAME = {item.name: item for item in EVENT_CATALOG}
_CANONICAL_BY_ALIAS = {
    alias: item.name
    for item in EVENT_CATALOG
    for alias in item.legacy_aliases
}


def event_name_status(value: str) -> EventNameStatus:
    """Classify an event name without changing its persisted representation.

    Any non-empty, non-namespaced value is considered legacy for compatibility.
    A value beginning with ``7gai.`` must satisfy the canonical grammar; this
    catches typos in new producers instead of silently treating them as old
    events.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("event name must be a non-empty string")
    name = value
    if name.startswith("7gai."):
        if not CANONICAL_EVENT_NAME_RE.fullmatch(name):
            raise ValueError(
                "canonical event name must match 7gai.<domain>.<event>.vN"
            )
        return "canonical"
    return "legacy"


def validate_event_name(
    value: str,
    *,
    require_registered: bool = False,
    allow_legacy: bool = True,
) -> str:
    """Validate and return a normalized event name.

    ``require_registered`` is intended for CI or new producer registration.
    Runtime readers should leave it false so historical event names continue
    to replay safely.
    """

    name = value
    status = event_name_status(name)
    if status == "legacy":
        if not allow_legacy:
            raise ValueError("legacy event names are disabled for this producer")
        if name not in LEGACY_EVENT_NAMES:
            raise ValueError(f"legacy event is not in the compatibility allow-list: {name}")
    if require_registered and status == "canonical" and name not in _CANONICAL_BY_NAME:
        raise ValueError(f"event is not registered in the 7GAI catalog: {name}")
    return name


def canonical_event_name(value: str) -> str:
    """Resolve a known legacy alias to its canonical name.

    Unknown legacy values are returned unchanged so old outbox rows remain
    readable.  This function is opt-in; it does not mutate database records.
    """

    try:
        name = validate_event_name(value)
    except ValueError:
        # A reader may encounter a legacy name written by an older release
        # that is not yet in this process's allow-list.  Keep it readable and
        # leave migration of that alias to the owning epic.
        if event_name_status(value) == "legacy":
            return value
        raise
    return _CANONICAL_BY_ALIAS.get(name, name)


def get_event_spec(value: str) -> EventSpec | None:
    """Return catalog metadata for a canonical name or known legacy alias."""

    try:
        name = validate_event_name(value)
    except ValueError:
        if event_name_status(value) == "legacy":
            return None
        raise
    canonical = _CANONICAL_BY_ALIAS.get(name, name)
    return _CANONICAL_BY_NAME.get(canonical)


def registered_event_names() -> tuple[str, ...]:
    """Return canonical names in deterministic catalog order."""

    return tuple(item.name for item in EVENT_CATALOG)
