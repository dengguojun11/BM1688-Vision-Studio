"""Response models for transient AI prompt trials."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AnnotationPromptTrialResponse(BaseModel):
    status: str = "COMPLETED"
    dataset_id: int
    contract_id: int
    contract_revision: int
    prompt_id: Optional[int] = None
    operation: str
    provider_key: str
    model_name: Optional[str] = None
    profile_key: str
    profile_version: str
    prompt_sha256: str
    effective_prompt: str
    rendered_prompt: str = ""
    image_width: int
    image_height: int
    model_image_width: int
    model_image_height: int
    latency_ms: Optional[int] = None
    model_response: Dict[str, Any] = Field(default_factory=dict)
    normalized_result: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    binding: Dict[str, Any] = Field(default_factory=dict)
