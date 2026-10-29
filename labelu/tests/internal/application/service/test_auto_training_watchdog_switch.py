from __future__ import annotations

from labelu.internal.application.service import auto_training_watchdog as watchdog
from labelu.internal.common.config import settings


def test_phase_one_read_only_gate_skips_database_and_thread(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    monkeypatch.setattr(watchdog, "_WATCHDOG_STARTED", False)
    monkeypatch.setattr(
        watchdog,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database must not be opened")),
    )

    result = watchdog.run_auto_training_watchdog_cycle(owner="test")
    watchdog.schedule_auto_training_watchdog()

    assert result.enabled is False
    assert watchdog._WATCHDOG_STARTED is False


def test_api_watchdog_can_be_disabled_for_dedicated_scheduler(monkeypatch):
    monkeypatch.setenv("LABELU_AUTO_TRAINING_API_WATCHDOG_ENABLED", "false")
    monkeypatch.setattr(watchdog, "_WATCHDOG_STARTED", False)
    watchdog.schedule_auto_training_watchdog()
    assert watchdog._WATCHDOG_STARTED is False
