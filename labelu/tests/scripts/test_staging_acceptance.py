import pytest

from scripts import staging_acceptance


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.token = None

    def request(self, method, path, body=None):
        if path.endswith("/users/login"):
            return 200, {"data": {"token": "staging-token"}}, 1
        if path.endswith("/openapi.json"):
            return (
                200,
                {
                    "paths": {
                        path: {}
                        for path in staging_acceptance.REQUIRED_OPENAPI_PATHS
                    }
                },
                1,
            )
        if path == "/api/v1/edge-device-jobs":
            return 403, {
                "msg": "该能力已下线，请使用任务、训练、BM1688 编译、真机验证和模型发布主流程",
                "err_code": 30004,
            }, 1
        if path == "/api/v1/tasks" and not self.token:
            return 401, {"detail": "unauthorized"}, 1
        return 200, {"data": []}, 1


def test_client_accepts_bearer_prefixed_and_raw_tokens(monkeypatch):
    seen = []

    class RequestClient(staging_acceptance.Client):
        pass

    def fake_urlopen(request, timeout):
        seen.append(request.headers.get("Authorization"))

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b"{}"

        return Response()

    monkeypatch.setattr(staging_acceptance, "urlopen", fake_urlopen)
    RequestClient("http://staging", "Bearer jwt-one", 1).request("GET", "/healthz")
    RequestClient("http://staging", "jwt-two", 1).request("GET", "/healthz")
    assert seen == ["Bearer jwt-one", "Bearer jwt-two"]


def test_read_only_probe_covers_authenticated_domains(monkeypatch):
    monkeypatch.setattr(staging_acceptance, "Client", FakeClient)
    args = staging_acceptance.parse_args(
        [
            "--base-url",
            "http://staging",
        ]
        + ["--password", "fake-password"]
    )
    results, context = staging_acceptance.run(args)
    assert context["authenticated"] is True
    assert all(item.passed for item in results)
    assert any(item.name == "训练作业" for item in results)
    assert any(item.name == "已发布模型目录" for item in results)
    assert any(
        item.name == "工作台汇总" and item.path.endswith("/workspace/summary")
        for item in results
    )
    retired = next(item for item in results if item.name == "退休写接口探针")
    assert retired.passed is True
    assert retired.required is False
    assert retired.status is None
    assert "默认跳过" in retired.detail


def test_default_acceptance_never_calls_retired_write_endpoint(monkeypatch):
    calls = []

    class RecordingClient(FakeClient):
        def request(self, method, path, body=None):
            calls.append((method, path))
            return super().request(method, path, body)

    monkeypatch.setattr(staging_acceptance, "Client", RecordingClient)
    args = staging_acceptance.parse_args(
        ["--base-url", "http://staging", "--password", "fake-password"]
    )
    staging_acceptance.run(args)
    assert ("POST", "/api/v1/edge-device-jobs") not in calls


def test_mutating_mode_fails_closed(monkeypatch):
    monkeypatch.setattr(staging_acceptance, "Client", FakeClient)
    args = staging_acceptance.parse_args(["--base-url", "http://staging", "--mutating"])
    results, _ = staging_acceptance.run(args)
    mutation = next(item for item in results if item.name == "mutating acceptance")
    assert mutation.passed is False
    assert "no mutation" in mutation.detail


def test_staging_acceptance_has_no_project_scope_or_retired_required_paths():
    assert not any(
        path.startswith("/api/v1/projects")
        for path in staging_acceptance.REQUIRED_OPENAPI_PATHS
    )
    with pytest.raises(SystemExit):
        staging_acceptance.parse_args(["--base-url", "http://staging", "--project-id", "1"])


def test_canonical_openapi_rejects_missing_required_path():
    payload = {"paths": {path: {} for path in staging_acceptance.REQUIRED_OPENAPI_PATHS}}
    payload["paths"].pop("/api/v1/tasks")

    passed, detail = staging_acceptance.inspect_canonical_openapi(payload)

    assert passed is False
    assert "/api/v1/tasks" in detail


def test_canonical_openapi_rejects_retired_path():
    payload = {"paths": {path: {} for path in staging_acceptance.REQUIRED_OPENAPI_PATHS}}
    payload["paths"]["/api/v1/edge-device-jobs"] = {}

    passed, detail = staging_acceptance.inspect_canonical_openapi(payload)

    assert passed is False
    assert "edge-device-jobs" in detail


def test_canonical_openapi_accepts_phase_one_contract():
    payload = {"paths": {path: {} for path in staging_acceptance.REQUIRED_OPENAPI_PATHS}}

    passed, detail = staging_acceptance.inspect_canonical_openapi(payload)

    assert passed is True
    assert detail == ""


def test_canonical_openapi_allows_workflow_outbox_operations_only():
    payload = {"paths": {path: {} for path in staging_acceptance.REQUIRED_OPENAPI_PATHS}}
    payload["paths"].update(
        {path: {} for path in staging_acceptance.ALLOWED_OPENAPI_PATHS}
    )

    passed, detail = staging_acceptance.inspect_canonical_openapi(payload)

    assert passed is True
    assert detail == ""


def test_canonical_openapi_rejects_non_outbox_workflow_path():
    payload = {"paths": {path: {} for path in staging_acceptance.REQUIRED_OPENAPI_PATHS}}
    payload["paths"]["/api/v1/workflows"] = {}

    passed, detail = staging_acceptance.inspect_canonical_openapi(payload)

    assert passed is False
    assert "/api/v1/workflows" in detail


def test_health_probe_rejects_spa_html_even_when_status_is_200(monkeypatch):
    class SpaClient(FakeClient):
        def request(self, method, path, body=None):
            if path == "/healthz":
                return 200, "<!doctype html><html><body>LabelU</body></html>", 1
            return super().request(method, path, body)

    monkeypatch.setattr(staging_acceptance, "Client", SpaClient)
    args = staging_acceptance.parse_args(["--base-url", "http://staging", "--password", "fake-password"])
    results, _ = staging_acceptance.run(args)
    liveness = next(item for item in results if item.name == "liveness")
    assert liveness.passed is False
    assert liveness.status == 200
    assert liveness.response_kind == "text"
    assert "SPA HTML" in liveness.detail


def test_retired_write_probe_rejects_success_status_even_when_response_is_json(monkeypatch):
    class ActiveLegacyClient(FakeClient):
        def request(self, method, path, body=None):
            if path == "/api/v1/edge-device-jobs":
                return 201, {"data": {"id": 99}}, 1
            return super().request(method, path, body)

    monkeypatch.setattr(staging_acceptance, "Client", ActiveLegacyClient)
    args = staging_acceptance.parse_args(
        [
            "--base-url",
            "http://staging",
            "--password",
            "fake-password",
            "--allow-retired-write-probes",
        ]
    )
    results, _ = staging_acceptance.run(args)
    retired = next(item for item in results if item.name == "一期已禁用边端作业写入")
    assert retired.passed is False
    assert retired.status == 201
    assert retired.response_kind == "json"


def test_readonly_route_audit_only_uses_get_and_skips_auth_and_retired_writes(monkeypatch):
    calls = []

    class ReadonlyClient(FakeClient):
        def request(self, method, path, body=None):
            calls.append((method, path, body))
            assert method == "GET"
            assert body is None
            return super().request(method, path, body)

    monkeypatch.setattr(staging_acceptance, "Client", ReadonlyClient)
    args = staging_acceptance.parse_args(
        ["--base-url", "http://production", "--readonly-route-audit"]
    )
    results, context = staging_acceptance.run_readonly_route_audit(args)

    assert context["mode"] == "readonly_route_audit"
    assert context["authenticated"] is False
    assert context["method_allowlist"] == ["GET"]
    assert context["retired_write_probes_executed"] is False
    assert all(method == "GET" and body is None for method, _, body in calls)
    assert not any(path.endswith("/users/login") for _, path, _ in calls)
    assert not any(
        path == "/api/v1/edge-device-jobs" and method != "GET"
        for method, path, _ in calls
    )


def test_readonly_route_audit_fails_when_openapi_drifts(monkeypatch):
    class DriftedClient(FakeClient):
        def request(self, method, path, body=None):
            if path == "/openapi.json":
                return 200, {"paths": {"/api/v1/edge-device-jobs": {"post": {}}}}, 1
            return 404, {}, 1

    monkeypatch.setattr(staging_acceptance, "Client", DriftedClient)
    args = staging_acceptance.parse_args(
        ["--base-url", "http://production", "--readonly-route-audit"]
    )
    results, context = staging_acceptance.run_readonly_route_audit(args)

    contract = next(item for item in results if item.name == "只读 OpenAPI 契约")
    assert contract.passed is False
    assert "缺少" in contract.detail
    assert "edge-device-jobs" in contract.detail
    assert context["retired_write_probes_executed"] is False


def test_readonly_route_audit_cli_rejects_mutation_flags():
    with pytest.raises(SystemExit):
        staging_acceptance.main(
            ["--readonly-route-audit", "--allow-retired-write-probes"]
        )
