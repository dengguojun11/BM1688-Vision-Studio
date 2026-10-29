import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    path = Path("scripts/benchmark_workspace_api.py").resolve()
    spec = importlib.util.spec_from_file_location("benchmark_workspace_api", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_percentiles_are_interpolated_and_summary_is_bounded():
    module = _load_module()

    assert module.percentile([1, 2, 3, 4, 100], 0.50) == 3.0
    assert module.percentile([1, 2, 3, 4, 100], 0.95) == 80.8
    assert module.percentile([1, 2, 3, 4, 100], 0.99) == 96.16
    assert module.summarize_latencies([10, 20, 30])["sample_count"] == 3


def test_remote_hosts_require_explicit_read_only_acknowledgement():
    module = _load_module()

    assert (
        module.ensure_safe_base_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    )
    with pytest.raises(ValueError, match="remote base URL rejected"):
        module.ensure_safe_base_url("https://example.com")
    assert (
        module.ensure_safe_base_url(
            "https://example.com/platform/", allow_remote_readonly=True
        )
        == "https://example.com/platform"
    )


def test_run_benchmark_only_uses_fixed_get_endpoints_and_reports_statuses():
    module = _load_module()
    calls = []

    def fake_request(url, *, token, timeout):
        calls.append((url, token, timeout))
        return 200

    payload = module.run_benchmark(
        base_url="http://127.0.0.1:8000",
        token="redacted-token",
        iterations=2,
        warmups=1,
        page=1,
        size=20,
        timeout=2,
        request_fn=fake_request,
    )

    assert payload["read_only"] is True
    assert len(calls) == 3 * (1 + 2)
    assert all(call[1] == "redacted-token" for call in calls)
    assert all("/api/v1/workspace/" in call[0] for call in calls)
    assert all("POST" not in call[0] for call in calls)
    assert {result["endpoint"] for result in payload["results"]} == {
        "workspace_summary",
        "workspace_recent_jobs",
        "workspace_alerts",
    }
    assert all(result["latency"]["sample_count"] == 2 for result in payload["results"])
    assert all(result["status_codes"] == {"200": 2} for result in payload["results"])
    assert payload["budget_passed"] is True
    assert all(result["budget_passed"] is True for result in payload["results"])


def test_run_benchmark_fails_closed_when_a_p95_budget_is_exceeded(monkeypatch):
    module = _load_module()
    ticks = iter([0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07])
    monkeypatch.setattr(module.time, "perf_counter", lambda: next(ticks))

    payload = module.run_benchmark(
        iterations=1,
        warmups=0,
        workspace_p95_budget_ms=5,
        list_p95_budget_ms=5,
        request_fn=lambda url, *, token, timeout: 200,
    )

    assert payload["budget_passed"] is False
    assert any(result["budget_passed"] is False for result in payload["results"])


def test_runtime_evidence_contains_no_token_and_has_script_hash():
    module = _load_module()

    evidence = module._runtime_evidence()

    assert len(evidence["benchmark_sha256"]) == 64
    assert "token" not in evidence


def test_run_benchmark_bounds_iteration_and_page_size():
    module = _load_module()
    with pytest.raises(ValueError, match="iterations"):
        module.run_benchmark(iterations=module.MAX_ITERATIONS + 1)
    with pytest.raises(ValueError, match="size"):
        module.run_benchmark(size=201)
    with pytest.raises(ValueError, match="workspace_p95_budget_ms"):
        module.run_benchmark(workspace_p95_budget_ms=0)
