from typing import Any

from pydantic import BaseModel, Field


class CreateOrganizationCommand(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)


class CreateSceneCommand(BaseModel):
    scene_key: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    metadata: dict[str, Any] | None = None
