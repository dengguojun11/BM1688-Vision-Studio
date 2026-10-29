from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RuleTemplateResponse(BaseModel):
    id: int
    project_id: int
    template_key: str
    name: str
    algorithm_key: str
    description: str | None = None
    status: str
    created_by: int
    created_at: datetime | None = None


class RuleVersionResponse(BaseModel):
    id: int
    template_id: int
    version_no: int
    schema_version: str
    config: dict[str, Any] = Field(default_factory=dict)
    config_sha256: str
    status: str
    created_by: int
    signing_payload_sha256: str | None = None
    signature_algorithm: str | None = None
    signature_key_id: str | None = None
    server_signature_status: str = "NOT_PROVIDED"
    signature_verification_scope: str = "SERVER_ONLY"
    device_verification_status: str = "UNVERIFIED"
    signature_verified_at: datetime | None = None
    created_at: datetime | None = None


class RuleBindingResponse(BaseModel):
    id: int
    project_id: int
    device_id: str
    rule_version_id: int
    status: str
    created_by: int
    device_verification_status: str = "UNVERIFIED"
    device_report_sequence: int = 0
    last_device_report_id: str | None = None
    device_verified_at: datetime | None = None
    updated_at: datetime | None = None
    created_at: datetime | None = None


class RuleAuditResponse(BaseModel):
    id: int
    binding_id: int
    project_id: int
    device_id: str
    operation: str
    from_status: str | None = None
    to_status: str | None = None
    actor_id: int | None = None
    reason: str | None = None
    idempotency_key: str | None = None
    created_at: datetime | None = None


class RuleVersionAuditResponse(BaseModel):
    id: int
    version_id: int
    template_id: int
    project_id: int
    version_no: int
    operation: str
    from_status: str | None = None
    to_status: str | None = None
    actor_id: int | None = None
    reason: str | None = None
    idempotency_key: str | None = None
    created_at: datetime | None = None


class RuleDeviceSignatureReportResponse(BaseModel):
    id: int
    report_id: str
    sequence: int
    project_id: int
    device_id: str
    rule_binding_id: int
    rule_version_id: int
    payload_sha256: str
    key_id: str
    key_state: str
    reported_status: str
    effective_status: str
    failure_code: str | None = None
    reason: str | None = None
    reported_at: datetime | None = None
    received_at: datetime | None = None
