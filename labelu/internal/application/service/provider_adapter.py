"""Provider execution SPI. Concrete adapters must be registered explicitly."""

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class ProviderRequest:
    provider_key: str
    provider_version: str
    asset_ids: list[int]
    contract_version: str
    config: dict[str, Any] = field(default_factory=dict)
    model_version_id: int | None = None
    assets: tuple["ProviderInputAsset", ...] = ()


@dataclass(frozen=True)
class ProviderInputAsset:
    """Immutable media identity passed to an adapter for lineage evidence."""

    id: int
    object_uri: str | None = None
    sha256: str | None = None
    media_type: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class ProviderResult:
    payload_uri: str | None = None
    payload_sha256: str | None = None
    result_manifest_uri: str | None = None
    result_sha256: str | None = None
    item_count: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    # Small result envelopes can stay inline for review and legacy projection.
    # Large results should continue to use payload_uri/result_manifest_uri.
    payload: dict[str, Any] | None = None


class ProviderAdapter(Protocol):
    key: str
    version: str
    runtime_adapter: str

    def predict(self, request: ProviderRequest) -> ProviderResult: ...


class DeterministicLocalAdapter:
    """Credential-free adapter for local contract and lineage tests only.

    It intentionally performs no inference and is never registered by import.
    Callers must explicitly register it in a test/development process; the
    provider registry still requires real image/toolchain/HIL evidence before
    reporting production availability.
    """

    key = "local_deterministic"
    version = "1.0.0"
    runtime_adapter = "local_deterministic"

    def predict(self, request: ProviderRequest) -> ProviderResult:
        assets = [
            {
                "id": int(asset.id),
                "object_uri": asset.object_uri,
                "sha256": asset.sha256,
                "media_type": asset.media_type,
                "size_bytes": asset.size_bytes,
            }
            for asset in request.assets
        ]
        trace = {
            "provider_key": request.provider_key,
            "provider_version": request.provider_version,
            "model_version_id": request.model_version_id,
            "input_assets": assets,
            "contract_version": request.contract_version,
        }
        canonical = json.dumps(
            trace, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return ProviderResult(
            payload_uri=f"local://deterministic-provider/{digest}.json",
            payload_sha256=digest,
            item_count=len(assets),
            metrics={"adapter": self.runtime_adapter, "lineage": trace},
        )


class HttpProviderAdapter:
    """HTTP adapter for model servers implementing the annotation contract.

    The adapter is intentionally explicit and is never registered implicitly.
    A deployment wires one instance to a Provider registry row after the
    endpoint, image digest, toolchain and HIL evidence have been verified.
    ``config.labels`` must contain the model label objects expected by the
    server; this keeps the Provider SPI independent from task tables.
    """

    def __init__(
        self,
        *,
        key: str,
        version: str,
        runtime_adapter: str,
        endpoint: str,
        timeout_seconds: float = 30.0,
        client_factory=httpx.Client,
    ) -> None:
        self.key = str(key).strip().lower()
        self.version = str(version).strip()
        self.runtime_adapter = str(runtime_adapter).strip().lower()
        self.endpoint = str(endpoint).strip()
        self.timeout_seconds = float(timeout_seconds)
        self._client_factory = client_factory
        parsed = httpx.URL(self.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("provider endpoint must be an absolute HTTP(S) URL")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    def _health_url(self) -> str:
        return self.endpoint.rstrip("/") + "/health"

    def health(self) -> bool:
        try:
            with self._client_factory(timeout=self.timeout_seconds) as client:
                response = client.get(self._health_url())
                return response.status_code == 200
        except Exception:
            return False

    def ready(self) -> bool:
        # The model server's health endpoint is the readiness contract.  A
        # separate readiness probe can be added by a concrete adapter later.
        return self.health()

    def predict(self, request: ProviderRequest) -> ProviderResult:
        labels = request.config.get("labels")
        if not isinstance(labels, list) or not labels:
            raise ValueError("provider config.labels must be a non-empty list")
        constraints = request.config.get("constraints")
        if not isinstance(constraints, dict):
            constraints = {
                "allowed_tools": ["rectTool"],
                "max_results_per_label": 100,
                "filter_by_labels": True,
            }
        prompt = request.config.get("prompt")
        items: list[dict[str, Any]] = []
        result_count = 0
        with self._client_factory(timeout=self.timeout_seconds) as client:
            for asset in request.assets:
                image_url = str(asset.object_uri or "").strip()
                if not image_url:
                    raise ValueError(f"input asset {asset.id} has no object_uri")
                body = {
                    "request_id": f"provider-{asset.id}",
                    "image_url": image_url,
                    "labels": labels,
                    "constraints": constraints,
                    "prompt": prompt,
                }
                response = client.post(self.endpoint, json=body)
                response.raise_for_status()
                value = response.json()
                if not isinstance(value, dict):
                    raise ValueError("provider response must be a JSON object")
                results = value.get("results")
                if isinstance(results, list):
                    result_count += len(results)
                items.append({"asset_id": int(asset.id), "response": value})
        payload = {
            "format": "7gai.provider_prediction.v1",
            "provider_key": request.provider_key,
            "provider_version": request.provider_version,
            "contract_version": request.contract_version,
            "items": items,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return ProviderResult(
            payload_sha256=digest,
            item_count=result_count,
            metrics={"adapter": self.runtime_adapter, "asset_count": len(items)},
            payload=payload,
        )


_ADAPTERS: dict[tuple[str, str], ProviderAdapter] = {}


@dataclass(frozen=True)
class ProviderAdapterRuntimeStatus:
    provider_key: str
    provider_version: str
    runtime_adapter: str | None
    registered: bool
    healthy: bool
    ready: bool
    last_error: str | None = None


def _identity(adapter: ProviderAdapter) -> tuple[str, str, str]:
    provider_key = str(getattr(adapter, "key", "")).strip().lower()
    provider_version = str(getattr(adapter, "version", "")).strip()
    runtime_adapter = (
        str(getattr(adapter, "runtime_adapter", None) or provider_key).strip().lower()
    )
    return provider_key, provider_version, runtime_adapter


def register_provider_adapter(
    adapter: ProviderAdapter,
    *,
    provider_key: str | None = None,
    provider_version: str | None = None,
    runtime_adapter: str | None = None,
) -> None:
    key, version, runtime_name = _identity(adapter)
    if (
        not key
        or not version
        or not runtime_name
        or not callable(getattr(adapter, "predict", None))
    ):
        raise ValueError("provider adapter requires key, version and predict")
    if provider_key is not None and str(provider_key).strip().lower() != key:
        raise ValueError("provider adapter key does not match registration key")
    if provider_version is not None and str(provider_version).strip() != version:
        raise ValueError("provider adapter version does not match registration version")
    if (
        runtime_adapter is not None
        and str(runtime_adapter).strip().lower() != runtime_name
    ):
        raise ValueError(
            "provider runtime adapter does not match registration identity"
        )
    _ADAPTERS[(key, version)] = adapter


def resolve_provider_adapter(key: str, version: str) -> ProviderAdapter:
    adapter = _ADAPTERS.get((str(key).strip().lower(), str(version).strip()))
    if adapter is None:
        raise LookupError(f"no executable adapter registered for {key}@{version}")
    return adapter


def inspect_provider_adapter(key: str, version: str) -> ProviderAdapterRuntimeStatus:
    provider_key = str(key).strip().lower()
    provider_version = str(version).strip()
    adapter = _ADAPTERS.get((provider_key, provider_version))
    if adapter is None:
        return ProviderAdapterRuntimeStatus(
            provider_key=provider_key,
            provider_version=provider_version,
            runtime_adapter=None,
            registered=False,
            healthy=False,
            ready=False,
            last_error=f"no executable adapter registered for {key}@{version}",
        )

    _, _, runtime_name = _identity(adapter)
    try:
        health_check = getattr(adapter, "health", None)
        healthy = bool(health_check()) if callable(health_check) else True
        readiness_check = getattr(adapter, "ready", None)
        ready = bool(readiness_check()) if callable(readiness_check) else healthy
        if not healthy:
            ready = False
        return ProviderAdapterRuntimeStatus(
            provider_key=provider_key,
            provider_version=provider_version,
            runtime_adapter=runtime_name,
            registered=True,
            healthy=healthy,
            ready=ready,
            last_error=(
                None
                if healthy and ready
                else "adapter health or readiness check failed"
            ),
        )
    except Exception as exc:
        return ProviderAdapterRuntimeStatus(
            provider_key=provider_key,
            provider_version=provider_version,
            runtime_adapter=runtime_name,
            registered=True,
            healthy=False,
            ready=False,
            last_error=str(exc)[:1000],
        )


def unregister_provider_adapter(key: str, version: str) -> None:
    """Remove one runtime registration, primarily for isolated tests/reloads."""
    _ADAPTERS.pop((str(key).strip().lower(), str(version).strip()), None)


def list_registered_provider_adapters() -> list[ProviderAdapterRuntimeStatus]:
    """Return a stable runtime snapshot without exposing adapter instances."""
    return [
        inspect_provider_adapter(key, version) for key, version in sorted(_ADAPTERS)
    ]
