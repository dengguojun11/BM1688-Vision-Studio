from __future__ import annotations

import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from labelu.internal.common import optional_dependencies as dependencies


@pytest.fixture(params=["ultralytics", "image_pipeline", "paramiko"])
def optional_dependency(request, monkeypatch):
    if request.param == "ultralytics":
        check = dependencies.ensure_ultralytics_installed
        import_name = "_try_import_ultralytics"
        available_name = "_ULTRALYTICS_AVAILABLE"
        attempted_name = "_ULTRALYTICS_INSTALL_ATTEMPTED"
        setting_name = "YOLO_AUTO_INSTALL_ULTRALYTICS"
    elif request.param == "paramiko":
        check = dependencies.ensure_paramiko_installed
        import_name = "_try_import_paramiko"
        available_name = "_PARAMIKO_AVAILABLE"
        attempted_name = "_PARAMIKO_INSTALL_ATTEMPTED"
        setting_name = "EDGE_SSH_AUTO_INSTALL_PARAMIKO"
    else:
        check = dependencies.ensure_image_pipeline_packages_installed
        import_name = "_try_import_image_pipeline"
        available_name = "_IMAGE_PIPELINE_AVAILABLE"
        attempted_name = "_IMAGE_PIPELINE_INSTALL_ATTEMPTED"
        setting_name = "IMAGE_PIPELINE_AUTO_INSTALL_DEPS"
    monkeypatch.setattr(dependencies, available_name, None)
    monkeypatch.setattr(dependencies, attempted_name, False, raising=False)
    monkeypatch.setattr(dependencies.settings, setting_name, True)
    monkeypatch.setattr(dependencies, import_name, lambda: False)
    return SimpleNamespace(
        check=check,
        import_name=import_name,
        setting_name=setting_name,
    )


@pytest.mark.parametrize("failure", ["exit", "timeout", "os_error", "import_missing"])
def test_failed_install_is_not_repeated(optional_dependency, monkeypatch, failure):
    calls = []

    def install(*args, **kwargs):
        calls.append(args)
        if failure == "timeout":
            raise subprocess.TimeoutExpired("pip", 1)
        if failure == "os_error":
            raise OSError("cannot execute pip")
        return SimpleNamespace(
            returncode=1 if failure == "exit" else 0,
            stdout="",
            stderr="No module named pip" if failure == "exit" else "",
        )

    monkeypatch.setattr(dependencies.subprocess, "run", install)

    assert [optional_dependency.check(reason="test") for _ in range(3)] == [
        False,
        False,
        False,
    ]
    assert len(calls) == 1


def test_concurrent_failed_install_runs_once(optional_dependency, monkeypatch):
    barrier = threading.Barrier(6)
    calls = []

    def install(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="No module named pip")

    def check(_):
        barrier.wait(timeout=5)
        return optional_dependency.check(reason="concurrent test")

    monkeypatch.setattr(dependencies.subprocess, "run", install)

    with ThreadPoolExecutor(max_workers=6) as executor:
        assert list(executor.map(check, range(6))) == [False] * 6
    assert len(calls) == 1


def test_manual_install_recovers_after_failed_attempt(optional_dependency, monkeypatch):
    calls = []

    def install(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="No module named pip")

    monkeypatch.setattr(dependencies.subprocess, "run", install)
    assert optional_dependency.check(reason="before manual install") is False

    monkeypatch.setattr(dependencies, optional_dependency.import_name, lambda: True)

    assert optional_dependency.check(reason="after manual install") is True
    assert optional_dependency.check(reason="cached availability") is True
    assert len(calls) == 1


def test_successful_auto_install_stays_available(optional_dependency, monkeypatch):
    installed = False
    calls = []

    def install(*args, **kwargs):
        nonlocal installed
        calls.append(args)
        installed = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dependencies, optional_dependency.import_name, lambda: installed)
    monkeypatch.setattr(dependencies.subprocess, "run", install)

    assert optional_dependency.check(reason="install") is True
    assert optional_dependency.check(reason="already installed") is True
    assert len(calls) == 1


def test_disabled_auto_install_can_be_enabled_later(optional_dependency, monkeypatch):
    calls = []

    def install(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="No module named pip")

    monkeypatch.setattr(dependencies.subprocess, "run", install)
    monkeypatch.setattr(dependencies.settings, optional_dependency.setting_name, False)
    assert optional_dependency.check(reason="disabled") is False
    assert calls == []

    monkeypatch.setattr(dependencies.settings, optional_dependency.setting_name, True)
    assert optional_dependency.check(reason="enabled") is False
    assert len(calls) == 1
