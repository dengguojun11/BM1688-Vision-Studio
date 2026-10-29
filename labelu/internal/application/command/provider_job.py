from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, root_validator, validator


class CreateProviderJobCommand(BaseModel):
    provider_key: str = Field(min_length=1, max_length=128)
    provider_version: str | None = Field(default=None, max_length=64)
    model_version_id: int | None = Field(default=None, gt=0)
    input_asset_ids: list[int] = Field(min_length=1, max_length=10000)
    contract_version: str = Field(default="prediction.v1", min_length=1, max_length=64)
    config: dict[str, Any] | None = None
    request_signature: str | None = Field(default=None, min_length=8, max_length=128)

    @validator("provider_key")
    def normalize_key(cls, value: str) -> str:
        return value.strip().lower()

    @validator("provider_version", "contract_version")
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @validator("input_asset_ids")
    def validate_assets(cls, value: list[int]) -> list[int]:
        if any(int(item) <= 0 for item in value):
            raise ValueError("input_asset_ids 必须为正整数")
        if len(set(value)) != len(value):
            raise ValueError("input_asset_ids 不得重复")
        return [int(item) for item in value]


class CreatePredictionSetCommand(BaseModel):
    sample_id: int | None = Field(default=None, gt=0)
    version: int = Field(default=1, ge=1)
    annotation_contract_version: str = Field(default="annotation_contract.v1", min_length=1, max_length=64)
    prediction_schema_version: str = Field(default="prediction.v1", min_length=1, max_length=64)
    payload_uri: str | None = Field(default=None, max_length=1024)
    payload_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    result_manifest_uri: str | None = Field(default=None, max_length=1024)
    result_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    # Small, normalized prediction payload used for review and legacy
    # projection. Large inference results should continue to use payload_uri.
    payload: dict[str, Any] | None = None
    status: str = Field(default="COMPLETED", max_length=24)
    review_status: str = Field(default="PENDING", max_length=24)
    item_count: int = Field(default=0, ge=0)
    metrics: dict[str, Any] | None = None
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = None

    @validator("status", "review_status")
    def normalize_status(cls, value: str) -> str:
        return value.strip().upper()

    @validator("payload_sha256", "result_sha256")
    def normalize_hash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("sha256 必须是 64 位十六进制摘要")
        return value

    @root_validator(skip_on_failure=True)
    def require_result(cls, values):
        if values.get("status") not in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
            raise ValueError("PredictionSet 状态不受支持")
        if values.get("review_status") not in {"PENDING", "IN_REVIEW", "ACCEPTED", "REJECTED", "SUPERSEDED"}:
            raise ValueError("审核状态不受支持")
        if values.get("status") in {"COMPLETED", "PARTIAL"} and not (
            values.get("payload_uri")
            or values.get("result_manifest_uri")
            or values.get("payload") is not None
        ):
            raise ValueError(
                "完成的 PredictionSet 必须提供 payload_uri、result_manifest_uri 或 payload"
            )
        if values.get("payload_uri") and not values.get("payload_sha256"):
            raise ValueError("payload_uri 必须同时提供 payload_sha256")
        if values.get("result_manifest_uri") and not values.get("result_sha256"):
            raise ValueError("result_manifest_uri 必须同时提供 result_sha256")
        return values


class TransitionPredictionSetReviewCommand(BaseModel):
    target_status: str = Field(min_length=1, max_length=24)
    reason: str | None = Field(default=None, max_length=2000)

    @validator("target_status")
    def normalize_target(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in {"IN_REVIEW", "ACCEPTED", "REJECTED", "SUPERSEDED"}:
            raise ValueError("PredictionSet 审核状态不受支持")
        return value

    @validator("reason")
    def normalize_reason(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None
