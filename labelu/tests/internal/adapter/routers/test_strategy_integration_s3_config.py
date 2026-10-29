from __future__ import annotations

from fastapi.testclient import TestClient
from botocore.exceptions import ClientError

from labelu.internal.application.service import strategy_integration_s3_config as service
from labelu.internal.common.config import settings


class _ProbeClient:
    def __init__(self, *, fail_stage: str | None = None):
        self.fail_stage = fail_stage
        self.head_calls: list[dict] = []
        self.list_calls: list[dict] = []
        self.list_buckets_calls = 0

    def list_buckets(self):
        self.list_buckets_calls += 1
        return {"Buckets": [{"Name": "unused-discovery-bucket"}]}

    def head_bucket(self, **kwargs):
        self.head_calls.append(kwargs)
        if self.fail_stage == "head_bucket":
            raise RuntimeError("simulated head failure")
        return {}

    def list_objects_v2(self, **kwargs):
        self.list_calls.append(kwargs)
        if self.fail_stage == "list_objects":
            raise RuntimeError("simulated list failure")
        return {"Contents": [{"Key": "one.jpg"}], "IsTruncated": False}


class _ConsolePortProbeClient(_ProbeClient):
    def list_buckets(self):
        raise ClientError(
            {
                "Error": {
                    "Code": "InvalidArgument",
                    "Message": "S3 API Requests must be made to API port.",
                }
            },
            "ListBuckets",
        )


def _install_connection(monkeypatch, *, client: _ProbeClient):
    monkeypatch.setattr(
        service,
        "load_s3_connection_store",
        lambda: {
            "active_connection_id": "s3_probe",
            "connections": [
                {
                    "id": "s3_probe",
                    "name": "测试 MinIO",
                    "endpoint": "http://minio.test:9000",
                }
            ],
        },
    )
    monkeypatch.setattr(service, "_build_probe_client", lambda endpoint: client)
    monkeypatch.setattr(settings, "S3_BUCKET", "test-bucket")


def test_s3_connection_test_runs_read_only_probe(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    monkeypatch,
):
    probe = _ProbeClient()
    _install_connection(monkeypatch, client=probe)

    response = client.post(
        "/api/v1/strategy-integration-s3-config/connections/s3_probe/test",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["endpoint_reachable"] is True
    assert data["head_bucket_ok"] is True
    assert data["list_objects_ok"] is True
    assert data["bucket"] == "test-bucket"
    assert data["object_count"] == 1
    assert data["error_stage"] is None
    assert probe.head_calls == [{"Bucket": "test-bucket"}]
    assert probe.list_calls == [
        {"Bucket": "test-bucket", "Prefix": "", "MaxKeys": 1}
    ]
    assert probe.list_buckets_calls == 0


def test_s3_connection_test_reports_head_bucket_failure_without_download(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    monkeypatch,
):
    probe = _ProbeClient(fail_stage="head_bucket")
    _install_connection(monkeypatch, client=probe)

    response = client.post(
        "/api/v1/strategy-integration-s3-config/connections/s3_probe/test",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_stage"] == "head_bucket"
    assert "存储桶" in data["message"]
    assert probe.list_calls == []


def test_s3_connection_test_identifies_minio_console_port(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    monkeypatch,
):
    probe = _ConsolePortProbeClient()
    _install_connection(monkeypatch, client=probe)
    monkeypatch.setattr(settings, "S3_BUCKET", "")

    response = client.post(
        "/api/v1/strategy-integration-s3-config/connections/s3_probe/test",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["endpoint_reachable"] is True
    assert data["error_stage"] == "s3_api"
    assert "Console" in data["message"]


def test_s3_connection_test_reports_object_list_failure(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    monkeypatch,
):
    probe = _ProbeClient(fail_stage="list_objects")
    _install_connection(monkeypatch, client=probe)

    response = client.post(
        "/api/v1/strategy-integration-s3-config/connections/s3_probe/test",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ok"] is False
    assert data["error_stage"] == "list_objects"
    assert "对象列表" in data["message"]
    assert probe.list_calls == [
        {"Bucket": "test-bucket", "Prefix": "", "MaxKeys": 1}
    ]


def test_s3_connection_test_discovers_bucket_when_global_bucket_is_empty(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    monkeypatch,
):
    probe = _ProbeClient()
    _install_connection(monkeypatch, client=probe)
    monkeypatch.setattr(settings, "S3_BUCKET", "")

    response = client.post(
        "/api/v1/strategy-integration-s3-config/connections/s3_probe/test",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["ok"] is True
    assert data["bucket"] == "unused-discovery-bucket"
    assert probe.list_buckets_calls == 1
    assert probe.head_calls == [{"Bucket": "unused-discovery-bucket"}]


def test_s3_connection_test_requires_system_admin(client: TestClient):
    response = client.post(
        "/api/v1/strategy-integration-s3-config/connections/s3_probe/test"
    )

    assert response.status_code in {401, 403}


def test_s3_connection_test_unknown_connection_is_404(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    monkeypatch,
):
    _install_connection(monkeypatch, client=_ProbeClient())

    response = client.post(
        "/api/v1/strategy-integration-s3-config/connections/missing/test",
        headers=testadmin_token_headers,
    )

    assert response.status_code == 404, response.text
