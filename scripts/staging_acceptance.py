#!/usr/bin/env python3
"""7GAI Evolution Platform staging acceptance probe.

The default run is read-only.  It is intentionally dependency-free so it can
run on a staging host without the application virtualenv.  Use environment
variables (or CLI flags) to point it at a real staging service and optional
resource IDs.

Examples::

    python scripts/staging_acceptance.py --base-url https://staging.example
    SEVEN_GAI_USERNAME=qa@example.com SEVEN_GAI_PASSWORD='...' \
      python scripts/staging_acceptance.py

Exit status is non-zero when a required probe fails.  A JSON evidence file is
written with ``--evidence`` and can be attached to the release record.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OPENAPI_CONTRACT_PATH = Path(__file__).resolve().parent / "ops" / "7gai_openapi_contract.json"


def _load_openapi_contract() -> tuple[
    frozenset[str], tuple[str, ...], frozenset[str], tuple[dict[str, Any], ...]
]:
    """Load the single route contract used by staging and deployment gates."""
    with OPENAPI_CONTRACT_PATH.open(encoding="utf-8") as stream:
        contract = json.load(stream)
    required = contract.get("required_paths")
    retired = contract.get("retired_prefixes")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("OpenAPI contract required_paths must be a string list")
    if not isinstance(retired, list) or not all(isinstance(item, str) for item in retired):
        raise ValueError("OpenAPI contract retired_prefixes must be a string list")
    allowed = contract.get("allowed_paths", [])
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise ValueError("OpenAPI contract allowed_paths must be a string list")
    write_probes = contract.get("retired_write_probes", [])
    if not isinstance(write_probes, list):
        raise ValueError("OpenAPI contract retired_write_probes must be a list")
    normalized: list[dict[str, Any]] = []
    for probe in write_probes:
        if not isinstance(probe, dict):
            raise ValueError("OpenAPI contract retired write probe must be an object")
        name = probe.get("name")
        method = probe.get("method")
        path = probe.get("path")
        expected_status = probe.get("expected_status")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(method, str)
            or not method.strip()
            or not isinstance(path, str)
            or not path.startswith("/")
            or not isinstance(expected_status, list)
            or not expected_status
            or not all(isinstance(status, int) for status in expected_status)
        ):
            raise ValueError("OpenAPI contract retired write probe is invalid")
        body = probe.get("body")
        if body is not None and not isinstance(body, dict):
            raise ValueError("OpenAPI contract retired write probe body must be an object")
        normalized.append(
            {
                "name": name,
                "method": method.upper(),
                "path": path,
                "body": body,
                "expected_status": tuple(expected_status),
            }
        )
    return frozenset(required), tuple(retired), frozenset(allowed), tuple(normalized)


# 一期验收只依赖任务、数据集、训练、模型、边端和第三方回流主链。
# 项目/组织、设备发布、OTA 和柔性工作流是已退休控制面，不能成为
# 验收必需路径；OpenAPI 中重新暴露这些路径也应直接判定失败。
(
REQUIRED_OPENAPI_PATHS,
RETIRED_OPENAPI_PREFIXES,
ALLOWED_OPENAPI_PATHS,
RETIRED_WRITE_PROBES,
) = _load_openapi_contract()

JSON_PROBE_NAMES = frozenset(
    {
        "liveness",
        "readiness",
        "version",
        "openapi json",
        "canonical OpenAPI paths",
        "退休写接口探针",
    }
)

# 生产只读路由复核只检查这些入口的 HTTP 可达性和 OpenAPI 方法登记。
# 其中 edge-device-jobs 只为确认旧写入口是否仍被暴露；本模式绝不调用其 POST。
READONLY_ROUTE_AUDIT_PATHS = (
    "/healthz",
    "/readyz",
    "/version",
    "/openapi.json",
    *sorted(REQUIRED_OPENAPI_PATHS),
    "/api/v1/edge-device-jobs",
)


def inspect_canonical_openapi(payload: Any) -> tuple[bool, str]:
    """Validate the canonical OpenAPI document and return (passed, detail)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
        return False, "response has no paths object"
    paths = set(payload["paths"])
    missing = sorted(REQUIRED_OPENAPI_PATHS - paths)
    retired = sorted(
        path
        for path in paths
        if path not in ALLOWED_OPENAPI_PATHS
        and any(path.startswith(prefix) for prefix in RETIRED_OPENAPI_PREFIXES)
    )
    detail_parts = []
    if missing:
        detail_parts.append("缺少: " + ", ".join(missing))
    if retired:
        detail_parts.append("已退休路径仍暴露: " + ", ".join(retired))
    return not missing and not retired, "; ".join(detail_parts)


@dataclass
class Probe:
    name: str
    method: str
    path: str
    status: int | None
    passed: bool
    required: bool
    detail: str = ""
    elapsed_ms: int = 0
    response_kind: str = ""


class Client:
    def __init__(self, base_url: str, token: str | None, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any, int]:
        url = f"{self.base_url}{path}"
        payload = None
        headers = {"Accept": "application/json, text/html"}
        if self.token:
            # The LabelU-compatible login endpoint returns the conventional
            # ``Bearer <jwt>`` value, while operators may also provide the raw
            # JWT through an environment variable. Accept both forms without
            # ever emitting ``Bearer Bearer ...``.
            token = str(self.token).strip()
            headers["Authorization"] = (
                token if token.lower().startswith("bearer ") else f"Bearer {token}"
            )
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        started = time.monotonic()
        request = Request(url, data=payload, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                status = response.status
        except HTTPError as exc:
            raw = exc.read()
            status = exc.code
        elapsed = int((time.monotonic() - started) * 1000)
        text = raw.decode("utf-8", errors="replace")
        try:
            data: Any = json.loads(text)
        except json.JSONDecodeError:
            data = text
        return status, data, elapsed


def _data(payload: Any) -> Any:
    return payload.get("data") if isinstance(payload, dict) else None


def _probe(
    client: Client,
    results: list[Probe],
    name: str,
    method: str,
    path: str,
    expected: set[int],
    required: bool = True,
    body: dict[str, Any] | None = None,
    expect_json: bool = False,
) -> tuple[int | None, Any]:
    try:
        status, payload, elapsed = client.request(method, path, body)
        response_kind = (
            "json"
            if isinstance(payload, (dict, list))
            else "text"
            if isinstance(payload, str)
            else "empty"
            if payload is None
            else "other"
        )
        passed = status in expected
        detail = "" if passed else f"expected {sorted(expected)}, got {status}"
        if passed and expect_json and response_kind != "json":
            passed = False
            detail = (
                "expected JSON response, got "
                f"{response_kind}; refuse SPA HTML or other non-JSON response"
            )
    except (URLError, TimeoutError, OSError) as exc:
        status, payload, elapsed, passed, response_kind = None, None, 0, False, "error"
        detail = f"{type(exc).__name__}: {exc}"
    results.append(
        Probe(
            name,
            method,
            path,
            status,
            passed,
            required,
            detail,
            elapsed,
            response_kind,
        )
    )
    return status, payload


def login(
    client: Client, username: str, password: str, results: list[Probe]
) -> str | None:
    status, payload = _probe(
        client,
        results,
        "login",
        "POST",
        "/api/v1/users/login",
        {200},
        body={"username": username, "password": password},
        expect_json=True,
    )
    token = _data(payload).get("token") if isinstance(_data(payload), dict) else None
    if not token:
        results[-1].passed = False
        results[-1].detail = "login response did not contain data.token"
    return token


def run_readonly_route_audit(
    args: argparse.Namespace,
) -> tuple[list[Probe], dict[str, Any]]:
    """Audit route exposure without authentication or mutations.

    Every request in this mode is GET and has no body. OpenAPI is evaluated
    separately from unauthenticated HTTP status codes so a 401/403 cannot hide
    a missing route, and a 404 on a retired route is recorded as read-only
    evidence rather than a write probe.
    """

    results: list[Probe] = []
    client = Client(args.base_url, None, args.timeout)
    openapi_payload: Any = None
    for path in READONLY_ROUTE_AUDIT_PATHS:
        _, payload = _probe(
            client,
            results,
            "只读路由 " + path,
            "GET",
            path,
            {200, 401, 403, 404, 405},
            required=True,
            expect_json=path in {"/healthz", "/readyz", "/version", "/openapi.json"},
        )
        if path == "/openapi.json":
            openapi_payload = payload

    if isinstance(openapi_payload, dict) and isinstance(
        openapi_payload.get("paths"), dict
    ):
        passed, detail = inspect_canonical_openapi(openapi_payload)
        results.append(
            Probe(
                "只读 OpenAPI 契约",
                "GET",
                "/openapi.json#paths",
                200,
                passed,
                True,
                detail,
                response_kind="json",
            )
        )
        openapi_paths = openapi_payload["paths"]
    else:
        results.append(
            Probe(
                "只读 OpenAPI 契约",
                "GET",
                "/openapi.json#paths",
                None,
                False,
                True,
                "response has no paths object",
            )
        )
        openapi_paths = {}

    return results, {
        "authenticated": False,
        "mode": "readonly_route_audit",
        "method_allowlist": ["GET"],
        "openapi_path_count": len(openapi_paths),
        "retired_write_probes_executed": False,
    }


def run(args: argparse.Namespace) -> tuple[list[Probe], dict[str, Any]]:
    results: list[Probe] = []
    client = Client(args.base_url, None, args.timeout)

    # Root probes are deliberately unauthenticated and must work behind Nginx.
    _probe(client, results, "liveness", "GET", "/healthz", {200}, expect_json=True)
    _probe(client, results, "readiness", "GET", "/readyz", {200}, expect_json=True)
    _probe(client, results, "version", "GET", "/version", {200}, expect_json=True)
    _probe(client, results, "swagger docs", "GET", "/docs", {200})
    _, openapi = _probe(
        client,
        results,
        "openapi json",
        "GET",
        "/openapi.json",
        {200},
        expect_json=True,
    )
    if isinstance(openapi, dict) and isinstance(openapi.get("paths"), dict):
        passed, detail = inspect_canonical_openapi(openapi)
        results.append(
            Probe(
                "canonical OpenAPI paths",
                "GET",
                "/openapi.json#paths",
                200,
                passed,
                True,
                detail,
                response_kind="json",
            )
        )
    else:
        results.append(
            Probe(
                "canonical OpenAPI paths",
                "GET",
                "/openapi.json#paths",
                200,
                False,
                True,
                "response has no paths object",
            )
        )

    _probe(
        client,
        results,
        "unauthenticated API rejected",
        "GET",
        "/api/v1/tasks",
        {401, 403},
    )

    if not args.username or not args.password:
        results.append(
            Probe(
                "staging credentials configured",
                "N/A",
                "/api/v1/users/login",
                None,
                False,
                True,
                "provide --username/--password or SEVEN_GAI_USERNAME/SEVEN_GAI_PASSWORD",
            )
        )
        if args.mutating:
            results.append(
                Probe(
                    "mutating acceptance",
                    "N/A",
                    "N/A",
                    None,
                    False,
                    True,
                    "--mutating requires an explicit fixture; no mutation was executed",
                )
            )
        return results, {"authenticated": False}
    token = login(client, args.username, args.password, results)
    if not token:
        return results, {"authenticated": False}
    client.token = token
    _probe(client, results, "current user", "GET", "/api/v1/users/me", {200})

    # Retired write probes are dangerous by definition: a legacy production
    # deployment may still accept the request and create a record before the
    # caller notices.  The normal acceptance run therefore records that the
    # probes were intentionally skipped.  Operators may opt in only with the
    # explicit dangerous flag when a disposable staging fixture is available.
    if args.allow_retired_write_probes:
        for retired_probe in RETIRED_WRITE_PROBES:
            _probe(
                client,
                results,
                retired_probe["name"],
                retired_probe["method"],
                retired_probe["path"],
                set(retired_probe["expected_status"]),
                body=retired_probe.get("body"),
                expect_json=True,
            )
    else:
        results.append(
            Probe(
                "退休写接口探针",
                "SKIP",
                ",".join(probe["path"] for probe in RETIRED_WRITE_PROBES),
                None,
                True,
                False,
                "默认跳过；仅在一次性预生产夹具上显式启用 --allow-retired-write-probes",
            )
        )

    # All collection probes are intentionally unscoped. Visibility is derived
    # from the authenticated user's task permissions; no project/space ID is
    # accepted or required by the一期主流程.
    list_checks = [
        ("任务", "/api/v1/tasks"),
        ("数据集", "/api/v1/datasets"),
        ("算法目录", "/api/v1/algorithms"),
        ("训练作业", "/api/v1/yolo-train-jobs"),
        ("模型版本", "/api/v1/model-versions"),
        ("已发布模型目录", "/api/v1/published-models"),
        ("BM1688 编译作业", "/api/v1/yolo-bm1688-exports"),
        ("边端验证作业", "/api/v1/yolo-edge-validation/jobs"),
        ("第三方 S3 配置", "/api/v1/strategy-integration-s3-config"),
        ("工作台汇总", "/api/v1/workspace/summary"),
    ]
    for name, path in list_checks:
        _probe(client, results, name, "GET", path, {200})

    # Optional detail checks use IDs supplied by the staging fixture.  They are
    # required when supplied, but omitted environments remain valid read-only
    # smoke runs.
    details = [
        ("provider job detail", args.provider_job_id, "/api/v1/provider-jobs/{}"),
        ("prediction set detail", args.prediction_set_id, "/api/v1/prediction-sets/{}"),
        ("model version detail", args.model_version_id, "/api/v1/model-versions/{}"),
    ]
    for name, resource_id, template in details:
        if resource_id:
            _probe(client, results, name, "GET", template.format(resource_id), {200})

    if args.mutating:
        # Deliberately fail closed until a staging fixture supplies explicit
        # commands. This prevents an accidental production data mutation.
        results.append(
            Probe(
                "mutating acceptance",
                "N/A",
                "N/A",
                None,
                False,
                True,
                "--mutating requires an explicit fixture; no mutation was executed",
            )
        )

    return results, {"authenticated": True, "scope": "task_and_dataset_permissions"}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", default=os.getenv("SEVEN_GAI_BASE_URL", "http://127.0.0.1:9888")
    )
    parser.add_argument(
        "--username", default=os.getenv("SEVEN_GAI_USERNAME", "admin@123.com")
    )
    parser.add_argument("--password", default=os.getenv("SEVEN_GAI_PASSWORD", ""))
    parser.add_argument("--provider-job-id", type=int, default=None)
    parser.add_argument("--prediction-set-id", type=int, default=None)
    parser.add_argument("--model-version-id", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--evidence", type=Path, default=None, help="write JSON evidence to this path"
    )
    parser.add_argument(
        "--mutating",
        action="store_true",
        help="reserved fail-closed mode; never mutates without a fixture",
    )
    parser.add_argument(
        "--allow-retired-write-probes",
        action="store_true",
        help="危险操作：向已退休写接口发送探针；仅允许一次性预生产夹具",
    )
    parser.add_argument(
        "--readonly-route-audit",
        action="store_true",
        help="只读核对 OpenAPI 和一期路由；只发送 GET，不登录、不写入",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.readonly_route_audit:
        if args.mutating or args.allow_retired_write_probes:
            raise SystemExit(
                "--readonly-route-audit 不能与 --mutating 或 --allow-retired-write-probes 一起使用"
            )
        results, context = run_readonly_route_audit(args)
    else:
        results, context = run(args)
    required_failures = [item for item in results if item.required and not item.passed]
    evidence = {
        "product": "7GAI Evolution Platform",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "context": context,
        "passed": not required_failures,
        "probes": [asdict(item) for item in results],
    }
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    for item in results:
        state = "PASS" if item.passed else ("FAIL" if item.required else "WARN")
        print(
            f"[{state}] {item.name}: {item.method} {item.path} ({item.status or '-'}) {item.elapsed_ms}ms {item.detail}"
        )
    return 1 if required_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
