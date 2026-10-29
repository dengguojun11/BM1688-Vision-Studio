from datetime import datetime

from pydantic import BaseModel


class ProjectResponse(BaseModel):
    id: int
    organization_id: int | None = None
    owner_id: int
    name: str
    description: str | None = None
    status: str
    created_by: int
    updated_by: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectMemberResponse(BaseModel):
    project_id: int
    user_id: int
    role: str
    status: str
    invited_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
