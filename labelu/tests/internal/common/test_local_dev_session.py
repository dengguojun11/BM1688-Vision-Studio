from __future__ import annotations

import os

import pytest

from labelu.internal.common.local_dev_session import (
    ENV_APP_PORTS,
    ENV_SESSION_ID,
    maybe_inject_local_dev_reload,
    read_dev_session_payload,
)


def test_maybe_inject_local_dev_reload_adds_meta_and_script(monkeypatch):
    monkeypatch.setenv(ENV_SESSION_ID, "session-123")
    monkeypatch.setenv(ENV_APP_PORTS, "9888")

    html = "<html><head><title>demo</title></head><body></body></html>"
    injected = maybe_inject_local_dev_reload(html)

    assert 'name="labelu-local-dev-ports"' in injected
    assert "labelu_local_dev_reload.js" in injected


def test_maybe_inject_local_dev_reload_skips_when_not_local(monkeypatch):
    monkeypatch.delenv(ENV_SESSION_ID, raising=False)

    html = "<html><head></head><body></body></html>"
    assert maybe_inject_local_dev_reload(html) == html


def test_read_dev_session_payload_requires_env(monkeypatch):
    monkeypatch.delenv(ENV_SESSION_ID, raising=False)
    assert read_dev_session_payload() is None

    monkeypatch.setenv(ENV_SESSION_ID, "abc")
    monkeypatch.setenv(ENV_APP_PORTS, "9888")
    payload = read_dev_session_payload()
    assert payload is not None
    assert payload["session_id"] == "abc"
    assert payload["app_ports"] == [9888]
