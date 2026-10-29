from __future__ import annotations

import pytest

from labelu.internal.common.local_dev_session import ENV_SESSION_ID


def test_local_dev_session_endpoint_returns_payload(client, monkeypatch):
    monkeypatch.setenv(ENV_SESSION_ID, "dev-session-001")

    resp = client.get("/api/v1/local-dev/session")

    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["session_id"] == "dev-session-001"
    assert 9888 in body["data"]["app_ports"]


def test_local_dev_session_endpoint_404_without_env(client, monkeypatch):
    monkeypatch.delenv(ENV_SESSION_ID, raising=False)

    resp = client.get("/api/v1/local-dev/session")

    assert resp.status_code == 404
