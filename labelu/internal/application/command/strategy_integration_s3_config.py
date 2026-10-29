from typing import Optional

from pydantic import BaseModel, Field


class CreateStrategyIntegrationS3ConnectionCommand(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    endpoint: str = Field(..., min_length=1, max_length=500)
    remark: str = Field(default="", max_length=200)
    set_active: bool = True


class UpdateStrategyIntegrationS3ConnectionCommand(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    endpoint: Optional[str] = Field(default=None, max_length=500)
    remark: Optional[str] = Field(default=None, max_length=200)


class SetActiveStrategyIntegrationS3ConnectionCommand(BaseModel):
    connection_id: str = Field(..., min_length=1, max_length=80)
