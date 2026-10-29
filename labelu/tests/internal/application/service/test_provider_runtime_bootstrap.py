from __future__ import annotations

from labelu.internal.application.service import provider_runtime_bootstrap as bootstrap
from labelu.internal.application.service.provider_adapter import (
    inspect_provider_adapter,
    unregister_provider_adapter,
)


def test_provider_bootstrap_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(bootstrap.settings, "PROVIDER_RUNTIME_AUTO_REGISTER", False)
    result = bootstrap.register_configured_provider_adapters()
    assert result.enabled is False
    assert result.registered == ()


def test_provider_bootstrap_registers_locateanything_only_when_explicitly_enabled(
    monkeypatch,
):
    monkeypatch.setattr(bootstrap.settings, "PROVIDER_RUNTIME_AUTO_REGISTER", True)
    monkeypatch.setattr(
        bootstrap.settings, "LA_MODEL_ENDPOINT", "http://127.0.0.1:59999/"
    )
    monkeypatch.setattr(bootstrap.settings, "LA_MODEL_TIMEOUT_SECONDS", 1)
    try:
        result = bootstrap.register_configured_provider_adapters()
        assert result.enabled is True
        assert result.registered == ("locateanything@1.0.0",)
        status = inspect_provider_adapter("locateanything", "1.0.0")
        assert status.registered is True
        assert status.runtime_adapter == "locateanything_preannotate"
        assert status.healthy is False
    finally:
        unregister_provider_adapter("locateanything", "1.0.0")


def test_provider_bootstrap_reports_missing_endpoint(monkeypatch):
    monkeypatch.setattr(bootstrap.settings, "PROVIDER_RUNTIME_AUTO_REGISTER", True)
    monkeypatch.setattr(bootstrap.settings, "LA_MODEL_ENDPOINT", "")
    result = bootstrap.register_configured_provider_adapters()
    assert result.enabled is True
    assert result.registered == ()
    assert result.skipped and "LA_MODEL_ENDPOINT" in result.skipped[0]
