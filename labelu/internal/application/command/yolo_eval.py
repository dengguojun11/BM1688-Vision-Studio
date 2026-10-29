from pydantic import BaseModel, Field


class YoloEvalStartCommand(BaseModel):
    report_kind: str = Field(
        default="full_analysis",
        description="full_analysis | next_iteration",
    )
