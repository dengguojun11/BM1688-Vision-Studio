from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateRuleTemplateCommand(BaseModel):
    project_id: int = Field(gt=0)
    template_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=160)
    algorithm_key: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)


class CreateRuleVersionCommand(BaseModel):
    config: dict[str, Any]
    schema_version: str = Field(default="v1", min_length=1, max_length=32)


class BindRuleCommand(BaseModel):
    rule_version_id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)


class RuleSignatureCommand(BaseModel):
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    key_id: str = Field(min_length=1, max_length=128)
    payload_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")


class RuleTransitionCommand(BaseModel):
    reason: str | None = Field(default=None, max_length=4000)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)
    signature: RuleSignatureCommand | None = None


class ReportRuleSignatureCommand(BaseModel):
    project_id: int = Field(gt=0)
    rule_version_id: int = Field(gt=0)
    payload_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")
    key_id: str = Field(min_length=1, max_length=128)
    report_id: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9._:-]+$")
    sequence: int = Field(ge=1)
    reported_status: Literal["ACK", "REJECTED"]
    failure_code: str | None = Field(default=None, max_length=128)
    reason: str | None = Field(default=None, max_length=4000)
    reported_at: datetime | None = None
