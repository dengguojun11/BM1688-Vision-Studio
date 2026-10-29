from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OrganizationResponse(BaseModel):
    id: int
    owner_id: int
    name: str
    description: str | None = None
    status: str
    created_by: int
    updated_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SceneResponse(BaseModel):
    id: int
    project_id: int
    scene_key: str
    name: str
    description: str | None = None
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: int
    updated_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
