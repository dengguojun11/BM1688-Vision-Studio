from __future__ import annotations

from datetime import datetime, timedelta

from labelu.internal.application.service import training_scheduler
from labelu.internal.application.service.scheduler_lease import SchedulerLeaseToken


def test_scheduler_once_runs_fenced_tick_and_releases(monkeypatch):
    token = SchedulerLeaseToken(
        "test", "owner", 4, datetime.now() + timedelta(seconds=30)
    )
    calls = []
    monkeypatch.setattr(
        training_scheduler, "acquire_scheduler_lease", lambda *a, **k: token
    )
    monkeypatch.setattr(
        training_scheduler, "scheduler_lease_is_current", lambda *a, **k: True
    )
    monkeypatch.setattr(
        training_scheduler, "release_scheduler_lease", lambda *a, **k: True
    )
    result = training_scheduler.run_scheduler_once(
        object(),
        owner="owner",
        lease_name="test",
        tick=lambda db, lease: calls.append((db, lease.fencing_token)),
    )
    assert result.acquired is True
    assert result.ticked is True
    assert result.released is True
    assert len(calls) == 1
    assert calls[0][1] == 4


def test_scheduler_once_skips_when_another_leader_holds_lease(monkeypatch):
    monkeypatch.setattr(
        training_scheduler, "acquire_scheduler_lease", lambda *a, **k: None
    )
    result = training_scheduler.run_scheduler_once(
        object(), owner="owner", tick=lambda *_: None
    )
    assert result.acquired is False


def test_scheduler_loop_closes_sessions_and_stops(monkeypatch):
    sessions = []
    ticks = []

    class Session:
        def close(self):
            sessions.append("closed")

    stop_calls = iter([False, True])
    monkeypatch.setattr(
        training_scheduler,
        "run_scheduler_once",
        lambda db, **kwargs: ticks.append((db, kwargs["owner"])),
    )
    training_scheduler.run_scheduler_loop(
        lambda: Session(),
        tick=lambda *_: None,
        owner="loop-owner",
        interval_seconds=0.01,
        stop=lambda: next(stop_calls),
        sleep=lambda value: None,
    )
    assert len(ticks) == 1
    assert sessions == ["closed"]
    assert ticks[0][1] == "loop-owner"
