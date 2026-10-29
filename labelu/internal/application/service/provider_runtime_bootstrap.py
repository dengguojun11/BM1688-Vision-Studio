"""Explicit startup registration for configured HTTP Provider adapters."""

from __future__ import annotations

from dataclasses import dataclass

from labelu.internal.application.service.provider_adapter import (
    HttpProviderAdapter,
    register_provider_adapter,
)
from labelu.internal.common.config import settings


@dataclass(frozen=True)
class ProviderBootstrapResult:
    enabled: bool
    registered: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def register_configured_provider_adapters() -> ProviderBootstrapResult:
    """Register only explicitly enabled, endpoint-backed adapters.

    The endpoint may still be unhealthy; the registry's health/readiness
    probe decides runtime availability. This function only installs the
    executable adapter identity so operators can diagnose that distinction.
    """

    if not bool(getattr(settings, "PROVIDER_RUNTIME_AUTO_REGISTER", False)):
        return ProviderBootstrapResult(enabled=False)

    registered: list[str] = []
    skipped: list[str] = []
    endpoint = str(getattr(settings, "LA_MODEL_ENDPOINT", "") or "").strip()
    if endpoint:
        register_provider_adapter(
            HttpProviderAdapter(
                key="locateanything",
                version="1.0.0",
                runtime_adapter="locateanything_preannotate",
                endpoint=endpoint,
                timeout_seconds=float(
                    getattr(settings, "LA_MODEL_TIMEOUT_SECONDS", 300)
                ),
            )
        )
        registered.append("locateanything@1.0.0")
    else:
        skipped.append("locateanything@1.0.0: LA_MODEL_ENDPOINT 未配置")
    return ProviderBootstrapResult(
        enabled=True, registered=tuple(registered), skipped=tuple(skipped)
    )


__all__ = ["ProviderBootstrapResult", "register_configured_provider_adapters"]
