"""Board Agent identity and unified task-token request headers."""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

import pytest


def _load_agent():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "ops"
        / "labelu_board_agent.py"
    )
    spec = importlib.util.spec_from_file_location(
        "labelu_board_agent_identity_test", path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"data": null}'


def test_http_requests_carry_device_and_task_identity(monkeypatch):
    agent = _load_agent()
    monkeypatch.setenv("BOARD_AGENT_ID", "edge_test_01")
    seen = {}

    def fake_urlopen(request, timeout=0):
        seen["request"] = request
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(agent, "urlopen", fake_urlopen)
    result = agent._http_json(
        "GET",
        "http://labelu.test/api/v1/board-agent/jobs/next",
        "board-token",
        task_token="job-token",
    )
    assert result == {"data": None}
    request = seen["request"]
    assert request.get_header("X-board-agent-token") == "board-token"
    assert request.get_header("X-board-agent-id") == "edge_test_01"
    assert request.get_header("X-board-agent-task-token") == "job-token"


def test_progress_returns_server_renewed_task_token(monkeypatch):
    agent = _load_agent()
    seen = []

    def fake_http_json(method, url, token, body=None, timeout=0, task_token=None):
        seen.append(task_token)
        return {"data": {"task_token": "renewed-token"}}

    monkeypatch.setattr(agent, "_http_json", fake_http_json)

    refreshed = agent._post_progress(
        "http://labelu.test/api/v1/board-agent/jobs/1/progress",
        "board-token",
        "infer",
        "running",
        task_token="initial-token",
    )

    assert refreshed == "renewed-token"
    assert seen == ["initial-token"]


def test_progress_keeps_legacy_token_when_response_has_no_token(monkeypatch):
    agent = _load_agent()
    monkeypatch.setattr(
        agent,
        "_http_json",
        lambda *_args, **_kwargs: {"data": {"status": "PROCESSING"}},
    )

    assert (
        agent._post_progress(
            "http://labelu.test/api/v1/board-agent/jobs/1/progress",
            "board-token",
            "infer",
            "running",
            task_token="initial-token",
        )
        is None
    )


def test_resident_heartbeat_runs_during_long_task_and_stops(monkeypatch):
    agent = _load_agent()
    monkeypatch.setenv("BOARD_AGENT_ID", "edge_test_01")
    calls = []
    first_call = threading.Event()

    def fake_post_heartbeat(base, token, demo_root):
        calls.append((base, token, demo_root))
        first_call.set()

    monkeypatch.setattr(agent, "_post_heartbeat", fake_post_heartbeat)
    keeper = agent._HeartbeatKeeper(
        "http://labelu.test",
        "board-token",
        Path("/tmp/sophon-demo"),
        interval_seconds=1,
    )
    # Shorten only this test's wait; production values remain clamped to a
    # safe one-to-sixty-second range.
    keeper._interval_seconds = 0.01
    keeper.start()

    assert first_call.wait(1)
    keeper.stop()
    call_count_after_stop = len(calls)
    time.sleep(0.05)

    assert call_count_after_stop >= 1
    assert len(calls) == call_count_after_stop
    assert calls[0][0:2] == ("http://labelu.test", "board-token")


def test_resident_heartbeat_failure_does_not_escape_worker(monkeypatch):
    agent = _load_agent()
    monkeypatch.setenv("BOARD_AGENT_ID", "edge_test_01")
    attempted = threading.Event()

    def failing_post_heartbeat(*_args):
        attempted.set()
        raise RuntimeError("temporary network failure")

    monkeypatch.setattr(agent, "_post_heartbeat", failing_post_heartbeat)
    keeper = agent._HeartbeatKeeper(
        "http://labelu.test",
        "board-token",
        Path("/tmp/sophon-demo"),
        interval_seconds=1,
    )
    keeper._interval_seconds = 0.01
    keeper.start()

    assert attempted.wait(1)
    # The exception is contained by _run; the caller can stop the worker
    # cleanly and the test process does not receive an uncaught thread error.
    keeper.stop()
    assert keeper._thread is None


def test_resident_heartbeat_skips_legacy_agent_without_identity(monkeypatch):
    agent = _load_agent()
    monkeypatch.delenv("BOARD_AGENT_ID", raising=False)
    monkeypatch.delenv("BM1688_DEVICE_ID", raising=False)
    called = []
    monkeypatch.setattr(agent, "_post_heartbeat", lambda *_args: called.append(True))

    keeper = agent._HeartbeatKeeper(
        "http://labelu.test",
        "board-token",
        Path("/tmp/sophon-demo"),
        interval_seconds=1,
    )
    keeper.start()
    time.sleep(0.05)
    keeper.stop()

    assert called == []


def test_resident_heartbeat_interval_uses_project_seconds_name(monkeypatch):
    agent = _load_agent()
    monkeypatch.setenv("BOARD_AGENT_HEARTBEAT_INTERVAL_SECONDS", "42")
    monkeypatch.delenv("BOARD_AGENT_HEARTBEAT_INTERVAL_SEC", raising=False)

    keeper = agent._HeartbeatKeeper(
        "http://labelu.test",
        "board-token",
        Path("/tmp/sophon-demo"),
    )

    assert keeper._interval_seconds == 42


def test_poll_once_owns_resident_heartbeat_for_claimed_job(monkeypatch, tmp_path):
    agent = _load_agent()
    monkeypatch.setenv("BOARD_AGENT_ID", "edge_test_01")
    monkeypatch.setattr(agent, "BOARD_JOBS_ROOT", tmp_path / "jobs")
    monkeypatch.setattr(agent, "_prepare_board_jobs_root", lambda: None)
    monkeypatch.setattr(agent, "_prune_stale_board_job_dirs", lambda **_kwargs: None)
    monkeypatch.setattr(agent, "_cleanup_job_workdir", lambda _path: None)

    lifecycle = []

    class FakeHeartbeatKeeper:
        def __init__(self, base, token, demo_root):
            lifecycle.append(("init", base, token, demo_root))

        def start(self):
            lifecycle.append("start")

        def stop(self):
            lifecycle.append("stop")

    monkeypatch.setattr(agent, "_HeartbeatKeeper", FakeHeartbeatKeeper)

    job = {
        "validation_id": 7,
        "export_id": 9,
        "models_zip_url": "http://labelu.test/models.zip",
        "report_url": "http://labelu.test/report",
        "progress_url": "http://labelu.test/progress",
    }

    def fake_http_json(method, url, token, body=None, timeout=0, task_token=None):
        if method == "GET":
            return {"data": job}
        return {"data": {}}

    monkeypatch.setattr(agent, "_http_json", fake_http_json)
    monkeypatch.setattr(agent, "_download_file", lambda _url, _token, dest, **_kwargs: dest.write_bytes(b"zip"))
    monkeypatch.setattr(agent, "_extract_models_zip", lambda _zip, target: target)
    monkeypatch.setattr(agent, "_load_run_config", lambda _path: {})
    monkeypatch.setattr(agent, "verify_edge_run_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        agent,
        "_load_threshold_contract",
        lambda _cfg: {
            "conf_thres": 0.25,
            "nms_thres": 0.45,
            "match_iou_thres": 0.5,
            "threshold_unit": "probability",
            "source": "test",
        },
    )
    monkeypatch.setattr(
        agent,
        "run_validation",
        lambda *_args, **_kwargs: {"passed": True, "summary": "ok", "steps": []},
    )
    monkeypatch.setattr(agent, "finalize_board_report", lambda report, _path: report)

    assert agent.poll_once("http://labelu.test", "board-token", tmp_path, False)
    assert lifecycle[0][0] == "init"
    assert lifecycle[1:] == ["start", "stop"]


def test_agent_uses_configured_edge_root_for_job_workspace(monkeypatch):
    monkeypatch.setenv("LABELU_EDGE_ROOT", "/srv/labelu-edge")
    agent = _load_agent()

    assert agent.EDGE_ROOT == Path("/srv/labelu-edge")
    assert agent.BOARD_JOBS_ROOT == Path("/srv/labelu-edge/jobs/board-agent")


def test_agent_keeps_default_edge_root_when_unconfigured(monkeypatch):
    monkeypatch.delenv("LABELU_EDGE_ROOT", raising=False)
    agent = _load_agent()

    assert agent.EDGE_ROOT == Path("/data/labelu_edge")
    assert agent.BOARD_JOBS_ROOT == Path("/data/labelu_edge/jobs/board-agent")


def test_main_demo_root_default_follows_configured_edge_root(monkeypatch):
    monkeypatch.setenv("LABELU_EDGE_ROOT", "/srv/labelu-edge")
    monkeypatch.delenv("SOPHON_DEMO_ROOT", raising=False)
    agent = _load_agent()
    captured = {}

    class _Parser:
        def add_argument(self, name, **kwargs):
            captured[name] = kwargs

        def parse_args(self):
            raise AssertionError("stop after parser defaults are rendered")

    class _Argparse:
        def ArgumentParser(self, **_kwargs):
            return _Parser()

    monkeypatch.setattr(agent, "argparse", _Argparse())
    with pytest.raises(AssertionError, match="stop after"):
        agent.main()

    assert Path(captured["--demo-root"]["default"]) == Path(
        "/srv/labelu-edge/runtime/sophon-demo/sample/YOLOv8_plus_det"
    )
