from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ModelVariantResponse(BaseModel):
    id: int
    series_id: int
    name: str
    branch_key: str
    selection_contract_sha256: str
    selection_contract: Dict[str, Any] = Field(default_factory=dict)
    class_contract: Dict[str, Any] = Field(default_factory=dict)
    recipe: Dict[str, Any] = Field(default_factory=dict)
    parent_model_version_id: Optional[int] = None
    status: str
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ModelSeriesResponse(BaseModel):
    id: int
    owner_id: int
    name: str
    description: Optional[str] = None
    model_family: Optional[str] = None
    status: str
    variant_count: int = 0
    variants: List[ModelVariantResponse] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ModelLineageResolveResponse(BaseModel):
    series_id: int
    series_name: str
    model_family: Optional[str] = None
    variant_id: int
    variant_name: str
    branch_key: str
    selection_contract_sha256: str
    selection_contract: Dict[str, Any] = Field(default_factory=dict)
    parent_model_version_id: Optional[int] = None
    parent_training_job_id: Optional[int] = None
    inheritance_mode: str

