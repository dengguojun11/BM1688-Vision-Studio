import pytest

from labelu.internal.application.service.provider_adapter import (
    DeterministicLocalAdapter,
    HttpProviderAdapter,
    ProviderInputAsset,
    ProviderRequest,
    ProviderResult,
    inspect_provider_adapter,
    list_registered_provider_adapters,
    register_provider_adapter,
    unregister_provider_adapter,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Client:
    def __init__(self):
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        return _Response({"status": "ok"})

    def post(self, url, json):
        self.posts.append((url, json))
        return _Response({"model": "fake", "results": [{"label": "person"}]})


class _Adapter:
    key = "adapter_contract"
    version = "1.2.3"
    runtime_adapter = "adapter_runtime"

    def predict(self, request):
        return ProviderResult(
            payload_uri="file:///unused.json", payload_sha256="a" * 64
        )


def test_registration_rejects_mismatched_declared_identity():
    with pytest.raises(ValueError, match="key does not match"):
        register_provider_adapter(_Adapter(), provider_key="different")
    with pytest.raises(ValueError, match="runtime adapter does not match"):
        register_provider_adapter(_Adapter(), runtime_adapter="different_runtime")


def test_runtime_status_snapshot_is_explicit_and_removable():
    register_provider_adapter(_Adapter())
    try:
        status = inspect_provider_adapter("adapter_contract", "1.2.3")
        assert status.registered is True
        assert status.healthy is True
        assert status.ready is True
        assert status.runtime_adapter == "adapter_runtime"
        assert status in list_registered_provider_adapters()
    finally:
        unregister_provider_adapter("adapter_contract", "1.2.3")
    assert inspect_provider_adapter("adapter_contract", "1.2.3").registered is False


def test_deterministic_local_adapter_is_stable_and_contains_lineage():
    adapter = DeterministicLocalAdapter()
    request = ProviderRequest(
        provider_key=adapter.key,
        provider_version=adapter.version,
        asset_ids=[31],
        contract_version="prediction.v1",
        model_version_id=77,
        assets=(
            ProviderInputAsset(
                id=31,
                object_uri="s3://bucket/frame.jpg",
                sha256="b" * 64,
                media_type="IMAGE",
                size_bytes=1234,
            ),
        ),
    )
    first = adapter.predict(request)
    second = adapter.predict(request)
    assert first == second
    assert first.payload_sha256 == first.payload_uri.rsplit("/", 1)[-1].removesuffix(
        ".json"
    )
    assert first.item_count == 1
    assert first.metrics["lineage"]["model_version_id"] == 77
    assert first.metrics["lineage"]["input_assets"][0]["sha256"] == "b" * 64


def test_http_provider_adapter_returns_hashed_inline_prediction_payload():
    client = _Client()
    adapter = HttpProviderAdapter(
        key="locateanything",
        version="1.0.0",
        runtime_adapter="locateanything_preannotate",
        endpoint="http://model.test/",
        client_factory=lambda timeout: client,
    )
    assert adapter.health() is True
    result = adapter.predict(
        ProviderRequest(
            provider_key=adapter.key,
            provider_version=adapter.version,
            asset_ids=[31],
            contract_version="prediction.v1",
            config={"labels": [{"name": "person", "tool": "rectTool"}]},
            assets=(ProviderInputAsset(id=31, object_uri="file:///sample.jpg"),),
        )
    )
    assert result.payload is not None
    assert result.payload["format"] == "7gai.provider_prediction.v1"
    assert result.item_count == 1
    assert len(result.payload_sha256) == 64
    assert client.posts[0][1]["image_url"] == "file:///sample.jpg"
