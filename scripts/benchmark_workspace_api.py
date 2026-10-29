#!/usr/bin/env python3
"""Bounded, read-only latency baseline for the 7GAI workspace APIs.

The benchmark intentionally exercises only GET requests.  It defaults to a
loopback server so a local run cannot accidentally probe production; remote
hosts require the explicit ``--allow-remote-readonly`` flag.  No fixture data
is created or changed by this script.

Examples::

    python scripts/benchmark_workspace_api.py --base-url http://127.0.0.1:8000 \
        --token "$LABELU_BENCHMARK_TOKEN" --iterations 25
    python scripts/benchmark_workspace_api.py --format json > .tmp/workspace-baseline.json
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import subprocess
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_ITERATIONS = 25
DEFAULT_WARMUPS = 3
MAX_ITERATIONS = 500
MAX_WARMUPS = 100
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_WORKSPACE_P95_BUDGET_MS = 1000.0
DEFAULT_LIST_P95_BUDGET_MS = 1500.0


@dataclass(frozen=True)
class Endpoint:
    name: str
    path: str
    paginated: bool = False


ENDPOINTS = (
    Endpoint("workspace_summary", "/api/v1/workspace/summary"),
    Endpoint("workspace_recent_jobs", "/api/v1/workspace/recent-jobs", paginated=True),
    Endpoint("workspace_alerts", "/api/v1/workspace/alerts", paginated=True),
)


def percentile(values: Iterable[float], quantile: float) -> float:
    """Return a linearly interpolated percentile in milliseconds.

    Keeping this implementation local makes the baseline runnable with the
    standard library only and gives stable results across Python versions.
    """

    samples = sorted(float(value) for value in values)
    if not samples:
        raise ValueError("percentile requires at least one sample")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between 0 and 1")
    if len(samples) == 1:
        return round(samples[0], 3)
    position = (len(samples) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        result = samples[lower]
    else:
        weight = position - lower
        result = samples[lower] + (samples[upper] - samples[lower]) * weight
    return round(result, 3)


def summarize_latencies(latencies_ms: Iterable[float]) -> dict[str, float | int]:
    values = [float(value) for value in latencies_ms]
    if not values:
        raise ValueError("at least one latency sample is required")
    return {
        "sample_count": len(values),
        "p50_ms": percentile(values, 0.50),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(sum(values) / len(values), 3),
    }


def _is_loopback_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    normalized = hostname.strip("[]").lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        # Resolve local aliases such as the machine hostname without treating
        # arbitrary DNS names as local.
        try:
            return all(
                ipaddress.ip_address(info[4][0]).is_loopback
                for info in socket.getaddrinfo(normalized, None)
            )
        except (OSError, ValueError):
            return False


def ensure_safe_base_url(base_url: str, *, allow_remote_readonly: bool = False) -> str:
    """Validate and normalize a benchmark base URL.

    Only HTTP(S) URLs with a hostname are accepted.  Remote URLs are rejected
    unless the caller explicitly acknowledges that this is a read-only probe.
    """

    parsed = urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an http(s) URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("credentials in the base URL are not allowed")
    if not _is_loopback_host(parsed.hostname) and not allow_remote_readonly:
        raise ValueError(
            "remote base URL rejected; pass --allow-remote-readonly for an explicit GET-only probe"
        )
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def endpoint_url(base_url: str, endpoint: Endpoint, *, page: int, size: int) -> str:
    path = f"{base_url.rstrip('/')}{endpoint.path}"
    if not endpoint.paginated:
        return path
    query = urlencode({"page": page, "size": size})
    return f"{path}?{query}"


def fetch_once(
    url: str, *, token: str | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> int:
    """Perform one GET and return the HTTP status code.

    The response body is deliberately not retained.  This keeps the probe
    bounded for large list responses and avoids writing application data to
    disk.  A non-2xx response raises ``RuntimeError`` with status only.
    """

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            response.read(1)
    except HTTPError as exc:
        raise RuntimeError(
            f"GET {urlsplit(url).path} returned HTTP {exc.code}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"GET {urlsplit(url).path} failed: {exc.reason}") from exc
    if status < 200 or status >= 300:
        raise RuntimeError(f"GET {urlsplit(url).path} returned HTTP {status}")
    return status


def benchmark_endpoint(
    endpoint: Endpoint,
    *,
    base_url: str,
    token: str | None,
    iterations: int,
    warmups: int,
    page: int,
    size: int,
    timeout: float,
    request_fn: Callable[..., int] = fetch_once,
) -> dict[str, object]:
    url = endpoint_url(base_url, endpoint, page=page, size=size)
    for _ in range(warmups):
        request_fn(url, token=token, timeout=timeout)
    latencies: list[float] = []
    statuses: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter()
        status = request_fn(url, token=token, timeout=timeout)
        latencies.append((time.perf_counter() - started) * 1000.0)
        statuses.append(status)
    return {
        "endpoint": endpoint.name,
        "method": "GET",
        "url": url,
        "page": page if endpoint.paginated else None,
        "size": size if endpoint.paginated else None,
        "warmups": warmups,
        "latency": summarize_latencies(latencies),
        "status_codes": dict(
            sorted((str(code), statuses.count(code)) for code in set(statuses))
        ),
    }


def run_benchmark(
    *,
    base_url: str = DEFAULT_BASE_URL,
    token: str | None = None,
    iterations: int = DEFAULT_ITERATIONS,
    warmups: int = DEFAULT_WARMUPS,
    page: int = 0,
    size: int = 50,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    allow_remote_readonly: bool = False,
    workspace_p95_budget_ms: float | None = DEFAULT_WORKSPACE_P95_BUDGET_MS,
    list_p95_budget_ms: float | None = DEFAULT_LIST_P95_BUDGET_MS,
    request_fn: Callable[..., int] = fetch_once,
) -> dict[str, object]:
    if not 1 <= iterations <= MAX_ITERATIONS:
        raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}")
    if not 0 <= warmups <= MAX_WARMUPS:
        raise ValueError(f"warmups must be between 0 and {MAX_WARMUPS}")
    if page < 0:
        raise ValueError("page must be >= 0")
    if not 1 <= size <= 200:
        raise ValueError("size must be between 1 and 200")
    if timeout <= 0:
        raise ValueError("timeout must be > 0")
    for name, budget in (("workspace_p95_budget_ms", workspace_p95_budget_ms), ("list_p95_budget_ms", list_p95_budget_ms)):
        if budget is not None and budget <= 0:
            raise ValueError(f"{name} must be > 0 when provided")
    normalized_url = ensure_safe_base_url(
        base_url, allow_remote_readonly=allow_remote_readonly
    )
    started = time.perf_counter()
    results = [
        benchmark_endpoint(
            endpoint,
            base_url=normalized_url,
            token=token,
            iterations=iterations,
            warmups=warmups,
            page=page,
            size=size,
            timeout=timeout,
            request_fn=request_fn,
        )
        for endpoint in ENDPOINTS
    ]
    budgets = {
        "workspace_summary_p95_ms": workspace_p95_budget_ms,
        "list_p95_ms": list_p95_budget_ms,
    }
    for result in results:
        budget = workspace_p95_budget_ms if result["endpoint"] == "workspace_summary" else list_p95_budget_ms
        observed = float(result["latency"]["p95_ms"])
        result["budget_ms"] = budget
        result["budget_passed"] = budget is None or observed <= budget
    return {
        "schema_version": 1,
        "read_only": True,
        "base_url": normalized_url,
        "iterations": iterations,
        "warmups": warmups,
        "timeout_seconds": timeout,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "budgets": budgets,
        "budget_passed": all(bool(result["budget_passed"]) for result in results),
        "results": results,
    }


def _runtime_evidence() -> dict[str, object]:
    """Capture non-secret revision and worktree evidence for the baseline."""
    evidence: dict[str, object] = {}
    try:
        evidence["revision"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--short"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
        )
        evidence["worktree_dirty"] = bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        evidence["revision"] = None
        evidence["worktree_dirty"] = None
    path = os.path.abspath(__file__)
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        evidence["benchmark_sha256"] = digest.hexdigest()
    except OSError:
        evidence["benchmark_sha256"] = None
    return evidence


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--token",
        default=os.environ.get("LABELU_BENCHMARK_TOKEN"),
        help="Bearer token (or LABELU_BENCHMARK_TOKEN); never printed",
    )
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmups", type=int, default=DEFAULT_WARMUPS)
    parser.add_argument("--page", type=int, default=0)
    parser.add_argument("--size", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--workspace-p95-budget-ms", type=float, default=DEFAULT_WORKSPACE_P95_BUDGET_MS)
    parser.add_argument("--list-p95-budget-ms", type=float, default=DEFAULT_LIST_P95_BUDGET_MS)
    parser.add_argument(
        "--allow-remote-readonly",
        action="store_true",
        help="allow an explicit GET-only probe against a non-loopback host",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    return parser


def _print_table(payload: dict[str, object]) -> None:
    print(
        f"7GAI workspace API baseline (GET-only, n={payload['iterations']}, "
        f"warmups={payload['warmups']}, base={payload['base_url']})"
    )
    print("endpoint                 p50_ms   p95_ms   p99_ms   mean_ms  statuses")
    for result in payload["results"]:
        latency = result["latency"]
        statuses = ",".join(
            f"{key}:{value}" for key, value in result["status_codes"].items()
        )
        print(
            f"{result['endpoint']:<24} {latency['p50_ms']:>7.3f} {latency['p95_ms']:>8.3f} "
            f"{latency['p99_ms']:>8.3f} {latency['mean_ms']:>8.3f}  {statuses}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = run_benchmark(
            base_url=args.base_url,
            token=args.token,
            iterations=args.iterations,
            warmups=args.warmups,
            page=args.page,
            size=args.size,
            timeout=args.timeout,
            allow_remote_readonly=args.allow_remote_readonly,
            workspace_p95_budget_ms=args.workspace_p95_budget_ms,
            list_p95_budget_ms=args.list_p95_budget_ms,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 2
    payload["evidence"] = _runtime_evidence()
    payload["evidence"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["evidence"]["result_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_table(payload)
    return 0 if payload["budget_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
