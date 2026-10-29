from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from scripts.ops import third_party_contract_simulator as simulator


def test_validate_local_base_url_rejects_non_loopback_before_network():
    assert simulator.validate_local_base_url("http://127.0.0.1:8003/") == "http://127.0.0.1:8003"
    assert simulator.validate_local_base_url("http://[::1]:8003") == "http://[::1]:8003"
    with pytest.raises(simulator.SimulatorError, match="仅允许访问"):
        simulator.validate_local_base_url("http://192.168.54.190:8003")
    with pytest.raises(simulator.SimulatorError, match="仅允许访问"):
        simulator.validate_local_base_url("https://example.com")


def test_payloads_match_third_party_contract():
    task = simulator.make_task_payload("sim-task", task_type="threshold_experiment")
    assert task["target"]["modelId"] == "536"
    assert task["data_window"]["start_time"]
    assert task["experiment"]["current_thresholds"]["edge_low_threshold"] == 0.35

    package = simulator.make_import_payload(
        "sim-package", "s3://acity-artifacts/strategy-datasets/sim-package/", sample_count=50
    )
    assert package["dataset"]["bucket"] == "acity-artifacts"
    assert package["dataset"]["image_prefix"].endswith("images/")
    assert package["dataset"]["metadata_key"].endswith("metadata.json")
    assert package["sample_count"] == 50
    assert package["dataset_category_key"] == ""


def test_callback_headers_use_platform_callback_signature():
    payload = simulator.make_callback_payload(
        "sim-task", callback_event_id="event-1", status="running"
    )
    secret = "local-test-secret"
    timestamp = 1_700_000_000
    headers = simulator.make_callback_headers(payload, secret, timestamp=timestamp)
    raw_body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected = hmac.new(
        secret.encode("utf-8"),
        b".".join((str(timestamp).encode(), b"event-1", raw_body)),
        hashlib.sha256,
    ).hexdigest()
    assert headers["X-LabelU-Signature"] == f"sha256={expected}"
    assert simulator.make_callback_payload(
        "sim-task", callback_event_id="event-2", status="completed"
    )["result"]["threshold_unit"] == "ratio_0_1"


def test_poll_import_until_terminal_keeps_distinct_worker_states():
    states = iter(["PENDING", "PROCESSING", "COMPLETED"])

    def fake_request(url, *, method="GET", payload=None, headers=None, timeout=20):
        assert method == "GET"
        assert url.endswith("/internal/v1/experiment-datasets/pkg-1")
        return 200, {"data": {"status": next(states)}}

    status, body, history = simulator._poll_import_until_terminal(
        request=fake_request,
        base_url="http://127.0.0.1:8003",
        package_id="pkg-1",
        headers={"X-7GAI-Service-Token": "local-token"},
        timeout=1,
        wait_seconds=1,
        poll_interval=0,
    )
    assert status == 200
    assert body == {"data": {"status": "COMPLETED"}}
    assert history == ["PENDING", "PROCESSING", "COMPLETED"]


def test_run_records_idempotency_conflict_and_callback_checks(tmp_path, monkeypatch):
    calls = []

    def fake_request(url, *, method="GET", payload=None, headers=None, timeout=20):
        calls.append((url, method, payload, headers, timeout))
        path = url.split("127.0.0.1:8003", 1)[-1]
        if path == "/api/v1/strategy-tasks" and method == "POST":
            if payload and payload.get("task_name") == "本地模拟-冲突请求":
                return 409, {"status": "rejected"}
            return 202, {"status": "accepted"}
        if path.startswith("/api/v1/strategy-tasks/"):
            return 200, {"data": {}}
        if path == "/api/v1/strategy-tasks" and method == "GET":
            return 200, {"data": []}
        if path == "/internal/v1/strategy-evolution/model-trainings":
            return 202, {"status": "accepted"}
        if path == "/internal/v1/strategy-evolution/threshold-experiments":
            return 202, {"status": "accepted"}
        if path == "/api/v1/integrations/strategy-tasks" and method == "POST":
            return 201, {"data": {"import_status": "PENDING"}}
        if path.startswith("/api/v1/integrations/strategy-tasks/"):
            return 200, {"data": {}}
        if path == "/internal/v1/experiment-datasets":
            return 202, {"status": "accepted"}
        if path.startswith("/internal/v1/experiment-datasets/"):
            return 200, {"data": {}}
        if path == "/api/v1/strategy-evolution/platform-callbacks":
            if payload and payload.get("status") == "running" and payload.get("callback_event_id", "").endswith("conflict"):
                return 409, {"status": 409}
            return 200, {"status": 0}
        if path.startswith("/internal/v1/strategy-evolution/tasks/") and path.endswith("/callback-retry"):
            return 200, {"data": {}}
        raise AssertionError((method, path))

    monkeypatch.setenv("SIM_SERVICE_TOKEN", "local-token")
    monkeypatch.setenv("SIM_HMAC_SECRET", "local-secret")
    args = SimpleNamespace(
        base_url="http://127.0.0.1:8003",
        service_token_env="SIM_SERVICE_TOKEN",
        hmac_secret_env="SIM_HMAC_SECRET",
        allow_unsigned_local=False,
        import_s3_uri="s3://local/sim-package/",
        sample_count=50,
        skip_import=False,
        callback_receiver_url="",
        evidence_dir=str(tmp_path),
        run_id="test-run",
        timeout=1,
        model_id="536",
        dataset_category_key="",
        seed_local_s3=False,
        s3_endpoint="http://192.168.54.190:29100",
        s3_bucket="acity-artifacts",
        s3_access_key_env="STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID",
        s3_secret_key_env="STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY",
    )
    assert simulator.run(args, request=fake_request) == 0
    evidence = json.loads((tmp_path / "test-run.json").read_text(encoding="utf-8"))
    assert evidence["checks"] == {
        "all_requests_passed": True,
        "idempotency": True,
        "conflict_rejected": True,
        "callback_idempotency": True,
        "callback_terminal_conflict": True,
    }
    assert len(calls) == 16

