from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

RequestType = Literal["model_optimization", "threshold_experiment", "scene_training"]
PublishDecisionType = Literal["PENDING", "AUTO", "MANUAL_PENDING", "APPROVED", "REJECTED"]


class ExternalImagePayload(BaseModel):
    external_image_id: str = Field(min_length=1, max_length=160)
    external_event_id: str | None = Field(default=None, max_length=160)
    package_id: str | None = Field(default=None, max_length=160)
    task_id: int | None = Field(default=None, ge=1)
    sample_id: int | None = Field(default=None, ge=1)
    event_time: datetime | None = None
    event_label: str | None = Field(default=None, max_length=160)
    scene: dict[str, Any] = Field(default_factory=dict)


class BindPackagesCommand(BaseModel):
    package_ids: list[str] = Field(default_factory=list, max_length=10000)
    images: list[ExternalImagePayload] = Field(default_factory=list, max_length=100000)

    @field_validator("package_ids")
    @classmethod
    def normalize_package_ids(cls, value: list[str]) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()
        for item in value or []:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text[:160])
        return items


class CreateOptimizationRequestCommand(BaseModel):
    external_request_id: str = Field(min_length=1, max_length=160)
    source_system: str = Field(default="third_party", min_length=1, max_length=64)
    client_id: str | None = Field(default=None, max_length=128)
    request_type: RequestType = "model_optimization"
    external_model_id: str | None = Field(default=None, max_length=160)
    labelu_model_id: int | None = Field(default=None, ge=1)
    export_id: int | None = Field(default=None, ge=1)
    event_label: str | None = Field(default=None, max_length=160)
    scene: dict[str, Any] = Field(default_factory=dict)
    event_time_from: datetime | None = None
    event_time_to: datetime | None = None
    require_complete: bool = True
    allow_partial: bool = False
    min_sample_count: int = Field(default=50, ge=1, le=100000)
    callback_url: str | None = Field(default=None, max_length=1024)
    deadline_at: datetime | None = None
    package_ids: list[str] = Field(default_factory=list, max_length=10000)
    images: list[ExternalImagePayload] = Field(default_factory=list, max_length=100000)

    @field_validator("source_system", "external_request_id")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("value cannot be empty")
        return text

    @field_validator("package_ids")
    @classmethod
    def normalize_package_ids(cls, value: list[str]) -> list[str]:
        items: list[str] = []
        seen: set[str] = set()
        for item in value or []:
            text = str(item or "").strip()[:160]
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        return items

    @field_validator("callback_url")
    @classmethod
    def validate_callback_url(cls, value: str | None) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if not text.startswith(("http://", "https://")):
            raise ValueError("callback_url must use http or https")
        if "@" in text.split("://", 1)[-1].split("/", 1)[0]:
            raise ValueError("callback_url must not contain credentials")
        return text

    @model_validator(mode="after")
    def validate_window_and_type(self):
        if (
            self.event_time_from is not None
            and self.event_time_to is not None
            and self.event_time_from > self.event_time_to
        ):
            raise ValueError("event_time_from must be before or equal to event_time_to")
        if self.request_type == "threshold_experiment":
            if not self.external_model_id:
                raise ValueError("external_model_id is required for threshold_experiment")
            if not self.event_label:
                raise ValueError("event_label is required for threshold_experiment")
            if self.event_time_from is None or self.event_time_to is None:
                raise ValueError("event time window is required for threshold_experiment")
        if self.request_type == "scene_training" and not self.scene:
            raise ValueError("scene is required for scene_training")
        return self


class CreateThresholdExperimentCommand(CreateOptimizationRequestCommand):
    request_type: RequestType = "threshold_experiment"


class CreateSceneTrainingCommand(CreateOptimizationRequestCommand):
    request_type: RequestType = "scene_training"


class PublishDecisionUpsertCommand(BaseModel):
    candidate_model_id: int | None = Field(default=None, ge=1)
    baseline_model_id: int | None = Field(default=None, ge=1)
    candidate_export_id: int | None = Field(default=None, ge=1)
    metrics: dict[str, Any] = Field(default_factory=dict)
    auto_publish_eligible: bool = False
    decision: PublishDecisionType = "PENDING"
    reason: str = Field(default="", max_length=4000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ApprovePublishCommand(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    reason: str = Field(default="", max_length=4000)
