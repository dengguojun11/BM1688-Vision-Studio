from __future__ import annotations

import pytest

from labelu.internal.application.service.auto_training_events import publish_event
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import AutoTrainingOutbox


def _kwargs(event_name: str) -> dict:
    return {
        "event_name": event_name,
        "aggregate_type": "test",
        "aggregate_id": "1",
        "payload": {"source": "event-catalog-test"},
        "idempotency_key": "event-catalog:test:1",
    }


def test_publish_event_accepts_legacy_name_without_rewriting_it(db):
    with begin_transaction(db):
        row = publish_event(db, **_kwargs("task.created.v1"))
    assert row.event_name == "task.created.v1"


def test_publish_event_accepts_registered_canonical_name(db):
    params = _kwargs("7gai.training.worker.command.v1")
    params["idempotency_key"] = "event-catalog:test:canonical"
    with begin_transaction(db):
        row = publish_event(db, **params)
    assert row.event_name == "7gai.training.worker.command.v1"


def test_publish_event_rejects_unknown_legacy_before_outbox_mutation(db):
    before = db.query(AutoTrainingOutbox).count()
    with pytest.raises(ValueError, match="compatibility allow-list"):
        with begin_transaction(db):
            publish_event(db, **_kwargs("test.unregistered.event.v1"))
    assert db.query(AutoTrainingOutbox).count() == before


def test_publish_event_rejects_malformed_namespaced_event_before_outbox_mutation(db):
    before = db.query(AutoTrainingOutbox).count()
    with pytest.raises(ValueError, match="canonical event name"):
        with begin_transaction(db):
            publish_event(db, **_kwargs("7gai.training.worker.command"))
    assert db.query(AutoTrainingOutbox).count() == before


def test_publish_event_rejects_unregistered_namespaced_event(db):
    with pytest.raises(ValueError, match="not registered"):
        with begin_transaction(db):
            publish_event(db, **_kwargs("7gai.training.custom.v1"))


def test_enqueue_alias_uses_the_same_validation(db):
    from labelu.internal.application.service.auto_training_events import (
        enqueue_auto_training_event,
    )

    with pytest.raises(ValueError, match="compatibility allow-list"):
        with begin_transaction(db):
            enqueue_auto_training_event(db, **_kwargs("not-registered"))
