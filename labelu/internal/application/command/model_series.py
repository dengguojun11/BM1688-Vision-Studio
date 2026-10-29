from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, validator


class CreateModelSeriesCommand(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)
    model_family: Optional[str] = Field(default=None, max_length=128)

    @validator("name", "description", "model_family", pre=True)
    def normalize_strings(cls, value):
        if value is None:
            return value
        return str(value).strip()


class UpdateModelSeriesCommand(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=2000)
    model_family: Optional[str] = Field(default=None, max_length=128)
    status: Optional[str] = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")

    @validator("name", "description", "model_family", "status", pre=True)
    def normalize_strings(cls, value):
        if value is None:
            return value
        return str(value).strip()


class ResolveModelLineageCommand(BaseModel):
    selection_contract: Dict[str, Any] = Field(default_factory=dict)
    branch_key: Optional[str] = Field(default=None, min_length=1, max_length=64)
    series_id: Optional[int] = Field(default=None, gt=0)
    variant_id: Optional[int] = Field(default=None, gt=0)
    parent_model_version_id: Optional[int] = Field(default=None, gt=0)
    parent_training_job_id: Optional[int] = Field(default=None, gt=0)
    inheritance_mode: Optional[str] = Field(default=None, max_length=32)
    series_name: Optional[str] = Field(default=None, max_length=128)
    variant_name: Optional[str] = Field(default=None, max_length=128)
    model_family: Optional[str] = Field(default=None, max_length=128)
    create_if_missing: bool = True

    @validator("branch_key", "inheritance_mode", "series_name", "variant_name", "model_family", pre=True)
    def normalize_strings(cls, value):
        if value is None:
            return value
        return str(value).strip()

