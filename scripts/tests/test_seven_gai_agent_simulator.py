from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.ops import seven_gai_agent_simulator as simulator


def test_download_release_preserves_partial_bytes_for_resume():
    chunks = {0: b"aa", 1: b"bb", 2: b"cc"}

    def load(_base, _agent, _release, index, _token):
        return chunks[index]

    with pytest.raises(simulator.DownloadInterrupted) as caught:
        simulator.download_release(
            "http://local",
            agent_id="sim-1",
            release_id=3,
            token="token",
            chunk_count=3,
            disconnect_after=2,
            chunk_loader=load,
        )
    interruption = caught.value
    assert interruption.last_index == 1
    assert interruption.partial == bytearray(b"aabb")

    resumed, last_index = simulator.download_release(
        "http://local",
        agent_id="sim-1",
        release_id=3,
        token="token",
        chunk_count=3,
        start_index=interruption.last_index + 1,
        chunk_loader=load,
    )
    combined = interruption.partial + resumed
    assert combined == bytearray(b"aabbcc")
    assert last_index == 2


def test_download_release_rejects_invalid_resume_index():
    with pytest.raises(ValueError, match="start_index"):
        simulator.download_release(
            "http://local",
            agent_id="sim-1",
            release_id=1,
            token="token",
            chunk_count=1,
            start_index=2,
            chunk_loader=lambda *_: b"x",
        )


def test_run_verify_failure_can_rollback(monkeypatch, capsys):
    calls = []

    def request(url, *, method="GET", payload=None, token=None):
        calls.append((url, method, payload, token))
        if url.endswith("/register"):
            return {}, {"data": {"agent": {"agent_id": "sim-1"}, "agent_token": "agent-token"}}
        if url.endswith("/manifest"):
            return {}, {"data": {"release": {"chunk_count": 1, "version": "1.0.0"}}}
        if url.endswith("/verify"):
            raise RuntimeError("verify rejected")
        if url.endswith("/rollback"):
            return {}, {"data": {"state": "ROLLED_BACK"}}
        return {}, {"data": {}}

    monkeypatch.setattr(simulator, "_request", request)
    monkeypatch.setattr(
        simulator,
        "download_release",
        lambda *args, **kwargs: (bytearray(b"artifact"), 0),
    )
    args = SimpleNamespace(
        base_url="http://local/api/v1",
        enrollment_token="enroll",
        agent_id="sim-1",
        release_id=7,
        resume_from=0,
        disconnect_after=None,
        auto_resume=False,
        fail_verify=True,
        fail_activate=False,
        rollback_on_failure=True,
    )
    assert simulator.run(args) == 0
    output = capsys.readouterr().out
    assert "ROLLED_BACK" in output
    assert any(url.endswith("/rollback") for url, *_ in calls)
