from __future__ import annotations

from contextlib import contextmanager
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from labelu.internal.application.command.edge_device_registry import (
    PatchEdgeDeviceCommand,
)
from labelu.internal.application.service import (
    edge_device_registry as edge_device_service,
)
from labelu.internal.application.service import (
    yolo_edge_validation_scheduler as scheduler,
)
from labelu.internal.common import edge_device_registry as registry


def test_device_mutation_lock_serializes_threads_and_can_be_reacquired(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(registry.settings, "BASE_DATA_DIR", str(tmp_path))
    entered = Event()
    release = Event()
    holder_result: list[bool] = []

    def hold_lock() -> None:
        with registry.device_mutation_lock(timeout_sec=1) as acquired:
            holder_result.append(acquired)
            entered.set()
            release.wait(2)

    thread = Thread(target=hold_lock)
    thread.start()
    assert entered.wait(2)

    with registry.device_mutation_lock(timeout_sec=0) as acquired:
        assert acquired is False

    release.set()
    thread.join(2)
    assert not thread.is_alive()
    assert holder_result == [True]

    with registry.device_mutation_lock(timeout_sec=1) as acquired:
        assert acquired is True


def test_device_mutation_lock_releases_after_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(registry.settings, "BASE_DATA_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="boom"):
        with registry.device_mutation_lock(timeout_sec=1) as acquired:
            assert acquired is True
            raise RuntimeError("boom")

    with registry.device_mutation_lock(timeout_sec=1) as acquired:
        assert acquired is True


def test_device_mutation_lock_is_reentrant_for_persistence_wrappers(monkeypatch, tmp_path):
    monkeypatch.setattr(registry.settings, "BASE_DATA_DIR", str(tmp_path))

    with registry.device_mutation_lock(timeout_sec=1) as outer:
        assert outer is True
        with registry.device_mutation_lock(timeout_sec=0) as inner:
            assert inner is True
        with registry.device_mutation_lock(timeout_sec=0) as inner_again:
            assert inner_again is True

    with registry.device_mutation_lock(timeout_sec=1) as acquired:
        assert acquired is True


def test_device_mutation_lock_fails_closed_when_file_lock_cannot_be_acquired(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(registry.settings, "BASE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        registry,
        "_try_acquire_device_mutation_file_lock",
        lambda **_kwargs: (None, False),
    )

    with registry.device_mutation_lock(timeout_sec=1) as acquired:
        assert acquired is False

    monkeypatch.undo()
    with registry.device_mutation_lock(timeout_sec=1) as acquired:
        assert acquired is True


def test_scheduler_claim_fails_closed_when_device_mutation_lock_is_unavailable(
    monkeypatch,
):
    @contextmanager
    def unavailable_lock(**_kwargs):
        yield False

    monkeypatch.setattr(
        scheduler.edge_device_registry,
        "device_mutation_lock",
        unavailable_lock,
    )

    result = scheduler._claim_job(
        None,
        SimpleNamespace(id=1),
        {"id": "edge-test", "name": "test"},
    )

    assert result is False


def test_device_update_fails_closed_when_device_mutation_lock_is_unavailable(
    monkeypatch,
):
    @contextmanager
    def unavailable_lock(**_kwargs):
        yield False

    monkeypatch.setattr(
        edge_device_service.registry_io,
        "device_mutation_lock",
        unavailable_lock,
    )
    monkeypatch.setattr(edge_device_service.settings, "YOLO_TRAIN_ENABLED", True)
    find_record = lambda _device_id: pytest.fail("busy check must not run")
    monkeypatch.setattr(
        edge_device_service.registry_io, "find_device_record", find_record
    )

    with pytest.raises(Exception) as exc_info:
        edge_device_service.update_edge_device(
            "edge-test", PatchEdgeDeviceCommand(name="new-name")
        )

    assert exc_info.value.status_code == 503
    assert "其他变更" in str(exc_info.value.detail)


def test_registry_normalization_rechecks_under_mutation_lock(monkeypatch):
    raw_store = {
        "version": 1,
        "active_device_id": "edge-old",
        "devices": [{"id": "edge-old", "name": "old"}],
    }
    current_store = {
        "version": 1,
        "active_device_id": "edge-current",
        "devices": [{"id": "edge-current", "name": "current"}],
    }
    normalized_current_store = registry._restore_store(current_store)
    reads = iter(
        [
            {"device_store": raw_store},
            {"device_store": normalized_current_store},
        ]
    )
    writes: list[dict] = []

    monkeypatch.setattr(registry, "read_group_values", lambda _group: next(reads))
    monkeypatch.setattr(
        registry,
        "write_group_values",
        lambda _group, values, **_kwargs: writes.append(values) or True,
    )

    result = registry.load_device_store()

    assert result["active_device_id"] == "edge-current"
    assert writes == []
