from enum import Enum
from datetime import datetime
from typing import Annotated, List, Union
from pydantic import BaseModel, Field

from labelu.internal.domain.models.sample import SampleState


class ExportType(str, Enum):
    """
    export samle result file type
    """

    JSON = "JSON"
    MASK = "MASK"
    COCO = "COCO"
    YOLO = "YOLO"
    CSV = "CSV"
    XML = "XML"
    LABEL_ME = "LABEL_ME"
    TF_RECORD = "TF_RECORD"
    PASCAL_VOC = "PASCAL_VOC"


class CreateSampleCommand(BaseModel):
    file_id: int = Field(
        gt=0,
        description="description: attachment file id",
    )
    data: Union[dict, None] = Field(
        default=None,
        description="description: annotation result of sample",
    )


class CopySampleToTaskCommand(BaseModel):
    source_task_id: int = Field(
        gt=0,
        description="description: source task id",
    )
    source_sample_id: int = Field(
        gt=0,
        description="description: source sample id",
    )


class CopySamplesByLabelCommand(BaseModel):
    label_id: int | None = Field(default=None, gt=0, description="Backward-compatible single label ID")
    label_ids: List[int] | None = Field(default=None, min_length=1, description="Global label IDs used to select source images")

    @property
    def normalized_label_ids(self) -> list[int]:
        values = list(self.label_ids or [])
        if self.label_id:
            values.append(int(self.label_id))
        return list(dict.fromkeys(int(value) for value in values if int(value) > 0))


class DeleteSampleCommand(BaseModel):
    sample_ids: List[int] = Field(
        min_length=1,
        description="description: attachment file id",
    )


class PatchSampleCommand(BaseModel):
    data: Union[dict, None] = Field(
        default=None,
        description="description: sample data, include result",
    )
    annotated_count: Union[int, None] = Field(
        default=0, description="description: annotate result count"
    )
    state: Union[SampleState, None] = Field(
        default=None,
        description="description: sample file state, must be 'SKIPPED', 'NEW', 'DONE', 'ABNORMAL', 'INCOMPLETE', or None",
    )
    expected_updated_at: Union[datetime, None] = Field(
        default=None,
        description="样本编辑页面读取到的版本时间；不匹配时拒绝覆盖其他用户的新标注",
    )
    expected_task_contract_revision: Union[int, None] = Field(
        default=None,
        ge=1,
        description="标注页面读取到的任务标签契约版本",
    )


class ExportSampleCommand(BaseModel):
    sample_ids: Union[List[Annotated[int, Field(gt=0)]], None] = Field(
        min_length=1,
        description="description: sample id",
    )
