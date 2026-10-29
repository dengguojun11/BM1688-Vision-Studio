from typing import Union

from pydantic import BaseModel, Field


class BatchLocateAnythingPreannotateCommand(BaseModel):
    filter_by_labels: bool = Field(default=True, description="only keep results matching configured rectTool labels")
    target_labels: Union[list[str], None] = Field(
        default=None,
        description="selected task rectTool label values; the service validates them against the task contract",
    )
    include_done_samples: bool = Field(
        default=True,
        description="If true, include DONE samples as well as NEW. SKIPPED is always excluded.",
    )
    overwrite: bool = Field(default=True, description="replace previous LocateAnything generated pre-annotations")
    sample_ids: Union[list[int], None] = Field(default=None, description="explicit sample ids to process")
    sample_id: Union[int, None] = Field(default=None, description="single sample id convenience field")
