from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, validator


class UpdateProviderEvidenceCommand(BaseModel):
    """Operator-supplied evidence used by Provider release gates.

    Runtime flags are recorded as an evidence snapshot.  The response still
    reports the live adapter probe separately, so an operator cannot mark an
    unregistered runtime as healthy through this endpoint.
    """

    provider_version: str | None = Field(default=None, min_length=1, max_length=64)
    image_digest: str | None = Field(default=None, min_length=1, max_length=256)
    toolchain_version: str | None = Field(default=None, max_length=128)
    availability_reason: str | None = Field(default=None, max_length=2000)
    hil_verified: bool | None = None
    hil_evidence: dict[str, Any] | None = None
    runtime_evidence: dict[str, Any] | None = None
    status: str | None = Field(default=None, max_length=24)

    @validator("provider_version", "toolchain_version", "availability_reason", pre=True)
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = str(value).strip()
        return value or None

    @validator("status")
    def normalize_status(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().upper()
        if value not in {"ACTIVE", "DISABLED", "DEPRECATED"}:
            raise ValueError("Provider 状态不受支持")
        return value

    @validator("image_digest")
    def validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized.startswith("sha256:"):
            normalized = normalized[7:]
        if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
            raise ValueError("image_digest 必须是 64 位 SHA256 摘要或 sha256:<摘要>")
        return f"sha256:{normalized}"

