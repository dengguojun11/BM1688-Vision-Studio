from typing import Literal

from pydantic import BaseModel, Field


ProjectStatus = Literal["DRAFT", "ACTIVE", "ARCHIVED"]
ProjectRole = Literal["owner", "admin", "member", "viewer"]


class CreateProjectCommand(BaseModel):
    organization_id: int | None = Field(default=None, gt=0)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class UpdateProjectCommand(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    status: ProjectStatus | None = None


class AddProjectMemberCommand(BaseModel):
    user_id: int = Field(gt=0)
    role: ProjectRole = "member"
