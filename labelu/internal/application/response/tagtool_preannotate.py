from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TagtoolClassificationModelResponse(BaseModel):
    id: int
    display_name: Optional[str] = None
    category: Optional[str] = None
    original_filename: Optional[str] = None
    file_size: Optional[int] = None
    label_heads: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None


class TagtoolPreannotateCandidateResponse(BaseModel):
    kind: str = Field(description="imported")
    id: int
    title: str
    detail: str = ""
    category: str = ""
    label_heads: List[str] = Field(default_factory=list)
    provider_key: Optional[str] = None


class TagtoolPreannotateModelsResponse(BaseModel):
    items: List[TagtoolPreannotateCandidateResponse]
    total: int = Field(default=0, description="搜索结果总数")
    page: int = Field(default=0, ge=0, description="从 0 开始的页码")
    size: int = Field(default=0, ge=0, description="当前页返回条数")


class TagtoolPreannotateJobResponse(BaseModel):
    id: int
    task_id: int
    status: str
    sample_count: int
    processed_count: int
    success_count: int
    failed_count: int
    model_kind: str
    model_id: int
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
