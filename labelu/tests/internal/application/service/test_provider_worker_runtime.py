from __future__ import annotations

from dataclasses import dataclass

import pytest

from labelu.internal.application.service.provider_worker_runtime import (
    run_worker_loop,
    run_worker_once,
)


@dataclass
class _Db:
    closed: bool = False

    def close(self):
        self.closed = True


def test_run_worker_once_delegates_queue_claim_and_preserves_worker_id():
    calls = []

    def runner(db, *, worker_id, lease_seconds):
        calls.append((db, worker_id, lease_seconds))
        return True

    db = _Db()
    result = run_worker_once(
        db, worker_id="provider-test", lease_seconds=17, runner=runner
    )
    assert result.processed is True
    assert result.error is None
    assert calls == [(db, "provider-test", 17)]


def test_run_worker_once_converts_runner_errors_to_tick_errors():
    def runner(*args, **kwargs):
        raise RuntimeError("adapter unavailable")

    result = run_worker_once(_Db(), worker_id="provider-test", runner=runner)
    assert result.processed is False
    assert result.error == "adapter unavailable"


def test_run_worker_loop_closes_sessions_and_stops_after_one_tick():
    db = _Db()
    sleeps = []
    calls = []

    def factory():
        return db

    def runner(*args, **kwargs):
        calls.append(kwargs)
        return False

    stopped = iter([False, True])
    run_worker_loop(
        factory,
        worker_id="provider-loop",
        interval_seconds=0.25,
        stop=lambda: next(stopped),
        sleep=sleeps.append,
        runner=runner,
    )
    assert db.closed is True
    assert len(calls) == 1
    assert sleeps == [0.25]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"worker_id": "", "runner": lambda *args, **kwargs: False},
        {"worker_id": "worker", "lease_seconds": 0},
    ],
)
def test_run_worker_once_validates_runtime_parameters(kwargs):
    with pytest.raises(ValueError):
        run_worker_once(_Db(), **kwargs)
