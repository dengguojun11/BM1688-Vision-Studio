from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from labelu.internal.application.service import strategy_task_integration as service


def _unique_error():
    return IntegrityError(
        "uq_strategy_task_integration_external_task_id",
        {},
        RuntimeError("duplicate external_task_id"),
    )


def test_create_or_reload_integration_returns_committed_unique_key_winner(
    db, monkeypatch
):
    winner = SimpleNamespace(id=41, external_task_id="strategy-race-1")
    calls = {"create": 0, "lookup": 0}

    def competing_create(*, db, row):
        calls["create"] += 1
        raise _unique_error()

    def reload_winner(*, db, external_task_id, lock=False):
        calls["lookup"] += 1
        assert external_task_id == "strategy-race-1"
        assert lock is True
        return winner

    monkeypatch.setattr(service.crud_strategy_task_integration, "create", competing_create)
    monkeypatch.setattr(
        service.crud_strategy_task_integration,
        "get_by_external_task_id",
        reload_winner,
    )

    actual, raced = service._create_or_reload_integration(
        db,
        row=SimpleNamespace(external_task_id="strategy-race-1"),
        external_task_id="strategy-race-1",
    )

    assert actual is winner
    assert raced is True
    assert calls == {"create": 1, "lookup": 1}


def test_create_or_reload_integration_preserves_integrity_error_when_winner_missing(
    db, monkeypatch
):
    monkeypatch.setattr(
        service.crud_strategy_task_integration,
        "create",
        lambda **kwargs: (_ for _ in ()).throw(_unique_error()),
    )
    monkeypatch.setattr(
        service.crud_strategy_task_integration,
        "get_by_external_task_id",
        lambda **kwargs: None,
    )

    with pytest.raises(IntegrityError):
        service._create_or_reload_integration(
            db,
            row=SimpleNamespace(external_task_id="strategy-race-missing"),
            external_task_id="strategy-race-missing",
        )
