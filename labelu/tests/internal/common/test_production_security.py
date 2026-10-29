import pytest

from labelu.internal.common.config import settings
from labelu.internal.common.production_security import (
    enforce_production_security,
    validate_production_security,
)


@pytest.fixture
def restore_security_settings():
    keys = (
        "APP_ENV",
        "PASSWORD_SECRET_KEY",
        "STORAGE_BACKEND",
        "S3_BUCKET",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID",
        "STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY",
        "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS",
        "STRATEGY_EVOLUTION_OUTBOUND_CALLBACK_BASE_URL",
        "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
        "BOARD_AGENT_ENABLED",
        "BOARD_AGENT_TOKEN",
        "SEVEN_GAI_AGENT_OTA_SIGNING_KEY",
        "SEVEN_GAI_AGENT_REQUIRE_OTA_SIGNATURE",
        "SEVEN_GAI_AGENT_REQUIRE_PROJECT",
        "MEDIA_VIRUS_SCAN_ENABLED",
        "MEDIA_VIRUS_SCAN_REQUIRED",
        "MEDIA_VIRUS_SCAN_COMMAND",
        "AI_AUTO_LABEL_ENABLED",
        "AI_PROVIDER",
        "VISION_QWEN_API_KEY",
    )
    snapshot = {key: getattr(settings, key) for key in keys}
    yield
    for key, value in snapshot.items():
        object.__setattr__(settings, key, value)


def _production_defaults(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "PASSWORD_SECRET_KEY", "x" * 64)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID", "dedicated-user")
    monkeypatch.setattr(settings, "STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY", "dedicated-secret")
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS", False)
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_OUTBOUND_CALLBACK_BASE_URL", "")
    monkeypatch.setattr(settings, "BOARD_AGENT_ENABLED", False)
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_ENABLED", False)
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_REQUIRED", False)
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_COMMAND", "")
    monkeypatch.setattr(settings, "AI_AUTO_LABEL_ENABLED", False)
    monkeypatch.setattr(settings, "AI_PROVIDER", "local_http")
    monkeypatch.setattr(settings, "VISION_QWEN_API_KEY", "")


def test_development_allows_compatibility_defaults(monkeypatch, restore_security_settings):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "PASSWORD_SECRET_KEY", "")
    monkeypatch.setattr(settings, "STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID", "minioadmin")
    report = validate_production_security(jwt_from_env=False)
    assert report.valid


def test_production_rejects_ephemeral_jwt_and_default_minio(monkeypatch, restore_security_settings):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "PASSWORD_SECRET_KEY", "x" * 64)
    monkeypatch.setattr(settings, "STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID", "minioadmin")
    monkeypatch.setattr(settings, "STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY", "minioadmin")
    report = validate_production_security(jwt_from_env=False)
    assert not report.valid
    assert any("PASSWORD_SECRET_KEY" in error for error in report.errors)
    assert any("MinIO" in error for error in report.errors)


def test_production_requires_external_storage_and_signed_callbacks(monkeypatch, restore_security_settings):
    _production_defaults(monkeypatch)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS", True)
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_OUTBOUND_CALLBACK_BASE_URL", "https://example.test/callback")
    report = validate_production_security(jwt_from_env=True)
    assert not report.valid
    assert any("S3_BUCKET" in error for error in report.errors)
    assert any("ALLOW_UNSIGNED" in error for error in report.errors)
    assert any("CALLBACK_HMAC_SECRET" in error for error in report.errors)


def test_enforce_production_security_passes_with_complete_configuration(monkeypatch, restore_security_settings):
    _production_defaults(monkeypatch)
    enforce_production_security(jwt_from_env=True)


def test_production_requires_media_scan_when_policy_enabled(monkeypatch, restore_security_settings):
    _production_defaults(monkeypatch)
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_REQUIRED", True)
    report = validate_production_security(jwt_from_env=True)
    assert not report.valid
    assert any("媒体病毒扫描" in error for error in report.errors)

    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_VIRUS_SCAN_COMMAND", "clamscan --no-summary {path}")
    assert validate_production_security(jwt_from_env=True).valid


def test_production_requires_ota_signature_gate_when_board_agent_enabled(monkeypatch, restore_security_settings):
    _production_defaults(monkeypatch)
    monkeypatch.setattr(settings, "BOARD_AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "BOARD_AGENT_TOKEN", "board-token")
    report = validate_production_security(jwt_from_env=True)
    assert not report.valid
    assert any("OTA_SIGNING_KEY" in error for error in report.errors)
    assert any("REQUIRE_OTA_SIGNATURE" in error for error in report.errors)
    assert any("REQUIRE_PROJECT" in error for error in report.errors)

    monkeypatch.setattr(settings, "SEVEN_GAI_AGENT_OTA_SIGNING_KEY", "x" * 64)
    monkeypatch.setattr(settings, "SEVEN_GAI_AGENT_REQUIRE_OTA_SIGNATURE", True)
    monkeypatch.setattr(settings, "SEVEN_GAI_AGENT_REQUIRE_PROJECT", True)
    assert validate_production_security(jwt_from_env=True).valid


def test_production_rejects_template_placeholders(monkeypatch, restore_security_settings):
    _production_defaults(monkeypatch)
    monkeypatch.setattr(settings, "PASSWORD_SECRET_KEY", "CHANGE_ME_generate_with_openssl_rand_hex_32")
    report = validate_production_security(jwt_from_env=True)
    assert not report.valid
    assert any("PASSWORD_SECRET_KEY" in error for error in report.errors)

    monkeypatch.setattr(settings, "PASSWORD_SECRET_KEY", "x" * 64)
    monkeypatch.setattr(settings, "AI_AUTO_LABEL_ENABLED", True)
    monkeypatch.setattr(settings, "AI_PROVIDER", "vision_llm_chain")
    monkeypatch.setattr(settings, "VISION_QWEN_API_KEY", "CHANGE_ME")
    monkeypatch.setattr(
        "labelu.internal.common.model_access_config.resolve_model_integration",
        lambda *_args, **_kwargs: None,
    )
    report = validate_production_security(jwt_from_env=True)
    assert any("Coding Plan" in error for error in report.errors)
    assert all("QWEN" not in error for error in report.errors)


def test_production_accepts_tested_admin_volcengine_without_qwen(monkeypatch):
    _production_defaults(monkeypatch)
    monkeypatch.setattr(settings, "AI_AUTO_LABEL_ENABLED", True)
    monkeypatch.setattr(settings, "AI_PROVIDER", "vision_llm_chain")
    monkeypatch.setattr(
        "labelu.internal.common.model_access_config.resolve_model_integration",
        lambda key: {"id": "tested-volcengine"} if key == "volcengine" else None,
    )

    assert validate_production_security(jwt_from_env=True).valid


def test_production_does_not_accept_qwen_key_instead_of_volcengine(monkeypatch):
    _production_defaults(monkeypatch)
    monkeypatch.setattr(settings, "AI_AUTO_LABEL_ENABLED", True)
    monkeypatch.setattr(settings, "AI_PROVIDER", "vision_llm_chain")
    monkeypatch.setattr(settings, "VISION_QWEN_API_KEY", "retired-provider-key")
    monkeypatch.setattr(
        "labelu.internal.common.model_access_config.resolve_model_integration",
        lambda *_args, **_kwargs: None,
    )

    report = validate_production_security(jwt_from_env=True)
    assert not report.valid
    assert any("Coding Plan" in error for error in report.errors)
    assert all("QWEN" not in error for error in report.errors)


def test_production_cloud_configuration_read_failure_fails_closed(monkeypatch):
    _production_defaults(monkeypatch)
    monkeypatch.setattr(settings, "AI_AUTO_LABEL_ENABLED", True)
    monkeypatch.setattr(settings, "AI_PROVIDER", "vision_llm_chain")

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("configuration store unavailable")

    monkeypatch.setattr(
        "labelu.internal.common.model_access_config.resolve_model_integration", unavailable
    )
    report = validate_production_security(jwt_from_env=True)
    assert any("Coding Plan" in error for error in report.errors)
