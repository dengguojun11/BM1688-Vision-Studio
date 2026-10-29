from typing import Union

from pydantic import BaseModel, Field


class AutoLabelCommand(BaseModel):
    overwrite: bool = Field(default=True, description="overwrite latest ai-generated pre-annotation")
    overwrite_existing_annotations: bool = Field(
        default=False,
        description=(
            "If true, AI pre-annotation may replace existing geometry on canvas. "
            "Single-image AI defaults to false to keep manual rects."
        ),
    )
    template_id: Union[int, None] = Field(default=None, description="reserved prompt template id")
    prompt: Union[str, None] = Field(default=None, description="optional prompt override")
    filter_by_labels: bool = Field(default=True, description="only keep results matching configured labels")
    include_geometry_tools: bool = Field(
        default=False,
        description=(
            "If true, AI may output rect/polygon/point/line as configured. "
            "If false, only global tagTool (tag_dimensions) is requested and stored."
        ),
    )


class BatchAutoLabelCommand(BaseModel):
    mode: str = Field(
        default="TAGS_ONLY",
        description="TAGS_ONLY or BOX_AND_TAGS. BOX_AND_TAGS is downgraded per sample when geometry is not allowed.",
    )
    filter_by_labels: bool = Field(default=True, description="only keep results matching configured labels")
    include_geometry_tools: bool = Field(
        default=False,
        description=(
            "If true, AI may output rect/polygon/point/line as configured. "
            "If false, only global tagTool (tag_dimensions) is requested and stored."
        ),
    )
    include_done_samples: bool = Field(
        default=True,
        description=(
            "If true, include DONE samples as well as NEW (SKIPPED excluded). "
            "If false, only NEW — legacy batch behavior."
        ),
    )
    overwrite_existing_annotations: bool = Field(
        default=True,
        description="apply generated pre-annotation as an overwrite candidate for existing sample annotations",
    )
    sample_states: Union[list[str], None] = Field(
        default=None,
        description=(
            "Selected sample states for batch AI: NEW, INCOMPLETE, DONE. "
            "When omitted, include_done_samples selects all three states or only NEW."
        ),
    )
    skip_complete_global_tags: bool = Field(
        default=True,
        description="If true, samples whose formal global tagTool is already complete are skipped before model calls.",
    )
