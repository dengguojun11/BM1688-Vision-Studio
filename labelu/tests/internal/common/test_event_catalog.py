from __future__ import annotations

import pytest

from labelu.internal.common.event_catalog import (
    CANONICAL_EVENT_NAME_RE,
    EVENT_CATALOG,
    LEGACY_EVENT_NAMES,
    canonical_event_name,
    event_name_status,
    get_event_spec,
    registered_event_names,
    validate_event_name,
)


def test_catalog_names_are_namespaced_and_unique():
    names = registered_event_names()
    assert len(names) == len(set(names))
    assert all(CANONICAL_EVENT_NAME_RE.fullmatch(name) for name in names)
    assert all(item.name in names for item in EVENT_CATALOG)


def test_event_inventory_metadata_is_explicit_and_external_by_default():
    """Inventory fields are review metadata, not fabricated production proof."""
    for item in EVENT_CATALOG:
        assert item.owner
        assert item.replay_strategy in {"idempotent_outbox", "idempotent_state_transition", "manual_review"}
        assert item.compatibility_cutoff
        assert item.deployment_evidence == "external/TBD" or item.deployment_evidence.startswith("verified:")


def test_known_legacy_event_is_allowed_and_resolves_to_canonical():
    legacy = "training.worker.command.v1"
    assert legacy in LEGACY_EVENT_NAMES
    assert event_name_status(legacy) == "legacy"
    assert validate_event_name(legacy) == legacy
    assert canonical_event_name(legacy) == "7gai.training.worker.command.v1"
    spec = get_event_spec(legacy)
    assert spec is not None
    assert spec.domain == "training"


def test_unknown_legacy_event_is_rejected_for_new_outbox_writes():
    with pytest.raises(ValueError, match="compatibility allow-list"):
        validate_event_name("unregistered.legacy.v1")
    assert event_name_status("unregistered.legacy.v1") == "legacy"
    assert canonical_event_name("unregistered.legacy.v1") == "unregistered.legacy.v1"
    assert get_event_spec("unregistered.legacy.v1") is None


@pytest.mark.parametrize(
    "value",
    [
        "7gai.training.worker.command",
        "7gai.Training.worker.command.v1",
        "7gai.training.worker.command.v0",
        "7gai.training.v1",
        "7gai.training.worker.command.v1 ",
    ],
)
def test_malformed_canonical_event_is_rejected(value: str):
    with pytest.raises(ValueError, match="canonical event name"):
        validate_event_name(value)


def test_legacy_can_be_disabled_for_strict_new_producer():
    with pytest.raises(ValueError, match="legacy event names are disabled"):
        validate_event_name("task.created.v1", allow_legacy=False)


def test_empty_or_non_string_event_is_rejected():
    with pytest.raises(ValueError, match="non-empty string"):
        validate_event_name(" ")
    with pytest.raises(ValueError, match="non-empty string"):
        validate_event_name(None)  # type: ignore[arg-type]
