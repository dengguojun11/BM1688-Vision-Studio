from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AnnotationPromptResponse(BaseModel):
    id: Optional[int] = None
    dataset_id: Optional[int] = None
    contract_id: int
    contract_revision: int
    operation: str
    provider_key: str
    model_name: Optional[str] = None
    profile_key: str
    profile_version: str
    compiler_version: str
    label_set_signature: str
    class_order_signature: str
    prompt_spec: Dict[str, Any] = Field(default_factory=dict)
    generated_prompt: str = ""
    edited_prompt: Optional[str] = None
    effective_prompt: str = ""
    prompt_sha256: str = ""
    warnings: List[str] = Field(default_factory=list)
    status: str = "PREVIEW"
    created_by: Optional[int] = None
    published_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
