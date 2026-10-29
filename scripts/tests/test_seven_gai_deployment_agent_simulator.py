from types import SimpleNamespace

from scripts.ops import seven_gai_deployment_agent_simulator as simulator


def test_run_reports_staged_active_and_health_with_agent_token(monkeypatch, capsys):
    calls = []

    def request(url, *, method="GET", payload=None, agent_token=None, user_token=None):
        calls.append((url, method, payload, agent_token, user_token))
        if url.endswith("/register"):
            return {"data": {"agent_token": "agent-secret"}}
        if url.endswith("/heartbeat"):
            return {"data": {"status": "ONLINE"}}
        if url.endswith("/report"):
            return {"data": {"reported_state": payload["reported_state"]}}
        if url.endswith("/observe"):
            return {"data": {"status": payload["status"]}}
        raise AssertionError(url)

    monkeypatch.setattr(simulator, "_request", request)
    args = SimpleNamespace(
        base_url="http://local/api/v1",
        enrollment_token="enroll",
        agent_id="edge-sim",
        project_id=3,
        release_id=8,
        bundle_sha256="a" * 64,
        healthy=True,
        fail=False,
        failure_reason="failed",
        rollback=False,
        user_token=None,
        fps=20.0,
        latency_p95_ms=50.0,
    )
    assert simulator.run(args) == 0
    output = capsys.readouterr().out
    assert '"phase": "active"' in output
    report_calls = [item for item in calls if item[0].endswith("/report")]
    assert [item[2]["reported_state"] for item in report_calls] == ["STAGED", "ACTIVE"]
    assert all(item[3] == "agent-secret" for item in report_calls)
    assert any(item[0].endswith("/observe") for item in calls)


def test_run_failure_requests_operator_rollback_and_agent_ack(monkeypatch, capsys):
    calls = []

    def request(url, *, method="GET", payload=None, agent_token=None, user_token=None):
        calls.append((url, method, payload, agent_token, user_token))
        if url.endswith("/register"):
            return {"data": {"agent_token": "agent-secret"}}
        if url.endswith("/rollback"):
            return {"data": {"status": "ROLLBACK"}}
        if url.endswith("/report"):
            return {"data": {"reported_state": payload["reported_state"]}}
        return {"data": {}}

    monkeypatch.setattr(simulator, "_request", request)
    args = SimpleNamespace(
        base_url="http://local/api/v1",
        enrollment_token="enroll",
        agent_id="edge-sim",
        project_id=None,
        release_id=8,
        bundle_sha256=None,
        healthy=False,
        fail=True,
        failure_reason="failed",
        rollback=True,
        user_token="operator-secret",
        fps=20.0,
        latency_p95_ms=50.0,
    )
    assert simulator.run(args) == 0
    capsys.readouterr()
    rollback_calls = [item for item in calls if item[0].endswith("/rollback")]
    assert rollback_calls and rollback_calls[0][4] == "operator-secret"
    report_states = [item[2]["reported_state"] for item in calls if item[0].endswith("/report")]
    assert report_states == ["FAILED", "ROLLED_BACK"]
