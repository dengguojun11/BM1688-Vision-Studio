from __future__ import annotations

import importlib.util
from pathlib import Path

def _load_start_labelu_local_module():
    module_path = Path(__file__).resolve().parents[2] / "start_labelu_local.py"
    spec = importlib.util.spec_from_file_location("start_labelu_local", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ensure_app_ports_ready_for_start_kills_listeners(monkeypatch):
    module = _load_start_labelu_local_module()
    stopped: list[int] = []
    waited: list[bool] = []

    monkeypatch.setattr(
        module,
        "get_listen_pid_map",
        lambda ports: {module.LOCAL_APP_PORT: 23456, module.LOCAL_VITE_PORT: 12345},
    )
    monkeypatch.setattr(module, "stop_process_tree", lambda pid: stopped.append(pid))
    monkeypatch.setattr(module, "wait_for_ports_to_close", lambda timeout_seconds=30: waited.append(True))

    module.ensure_app_ports_ready_for_start()

    assert stopped == [23456, 12345]
    assert waited == [True]


def test_resolve_public_host_prefers_env(monkeypatch):
    module = _load_start_labelu_local_module()

    monkeypatch.setenv("LABELU_PUBLIC_HOST", "192.168.1.66")

    assert module.resolve_public_host() == "192.168.1.66"


def test_build_env_uses_public_media_host():
    module = _load_start_labelu_local_module()

    env = module.build_env("192.168.1.66")

    assert env["DATABASE_URL"] == module.DATABASE_URL
    assert env["LABELU_ALLOW_DEV_MYSQL"] == "true"
    assert env["MEDIA_HOST"] == "http://192.168.1.66:9888"
    assert env["PYTHONIOENCODING"].endswith(":replace")
    assert env["PYTHONUTF8"] == "1"
    assert env[module.LOCAL_LAUNCHER_ENV] == "1"
    assert env["LABELU_LOCAL_DEV_SESSION"]
    assert env["LABELU_LOCAL_DEV_APP_PORTS"] == "9888"
    assert env["LABELU_FORCE_NO_CACHE"] == "1"
