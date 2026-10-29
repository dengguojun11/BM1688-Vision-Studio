from typing import List, Literal, Optional

from pydantic import BaseModel, Field, root_validator


class BatchTagtoolPreannotateCommand(BaseModel):
    """Run image-level tagTool classification and write pre-annotations."""

    model_kind: Literal["imported", "remote"] = Field(
        default="imported",
        description="imported=本地权重；remote=系统管理中配置的远程模型服务。",
    )
    model_id: int = Field(default=0, ge=0, description="本地模型编号；远程模型固定为 0")
    provider_key: Optional[Literal["tagtool"]] = Field(
        default=None, description="远程模型服务标识，远程全局标签固定为 tagtool"
    )
    overwrite: bool = Field(default=True, description="Overwrite existing TagTool classification pre-annotations")
    include_done_samples: bool = Field(
        default=False,
        description="When sample_ids are omitted: false=NEW only; true=NEW+DONE, excluding SKIPPED.",
    )
    sample_ids: Optional[List[int]] = Field(
        default=None,
        max_length=500,
        description="Only process these sample ids; skipped samples are ignored.",
    )
    sample_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Single sample id; merged with sample_ids.",
    )
    device: Optional[str] = Field(
        default=None,
        description="Optional torch device, e.g. cpu or cuda:0. Empty uses auto.",
    )

    @root_validator(skip_on_failure=True)
    def validate_model_source(cls, values):
        kind = values.get("model_kind")
        model_id = int(values.get("model_id") or 0)
        if kind == "imported" and model_id <= 0:
            raise ValueError("本地 TagTool 模型必须提供大于 0 的模型编号")
        if kind == "remote":
            values["model_id"] = 0
            values["provider_key"] = "tagtool"
        return values
