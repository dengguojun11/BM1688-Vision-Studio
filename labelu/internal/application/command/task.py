from typing import List, Optional, Union, Literal
from pydantic import BaseModel, Field, validator

from labelu.internal.domain.models.task import MediaType


class BasicConfigCommand(BaseModel):
    name: str = Field(description="description: task name", max_length=50)
    media_type: Union[MediaType, None] = Field(
        default=None, description="description: media type of task files"
    )
    description: str = Field(
        default=None, description="description: task description", max_length=500
    )
    tips: Union[str, None] = Field(
        default=None, description="description: task tips", max_length=1000
    )
    dataset_category_id: Optional[int] = Field(default=None, gt=0)
    dataset_id: Optional[int] = Field(default=None, gt=0)
    dataset_label_contract_id: Optional[int] = Field(default=None, gt=0)
    task_label_scope_mode: Optional[Literal["FULL", "SUBSET"]] = None


class CollaboratorUpdateCommand(BaseModel):
    user_id: int = Field(description="description: user id")


class CollaboratorBatchCommand(BaseModel):
    user_ids: List[int] = Field(description="description: user ids")


class UpdateCommand(BaseModel):
    name: Union[str, None] = Field(
        default=None, description="description: task name", max_length=50
    )
    description: Union[str, None] = Field(
        default=None, description="description: task description", max_length=500
    )
    tips: Union[str, None] = Field(
        default=None, description="description: task tips", max_length=1000
    )
    media_type: Union[MediaType, None] = Field(
        default=None, description="description: media type of task files"
    )
    config: Union[str, None] = Field(
        default=None, description="description: task config content"
    )
    dataset_category_id: Optional[int] = Field(default=None, gt=0)
    dataset_id: Optional[int] = Field(default=None, gt=0)
    dataset_label_contract_id: Optional[int] = Field(default=None, gt=0)
    task_label_scope_mode: Optional[Literal["FULL", "SUBSET"]] = None


class AssignTaskProjectCommand(BaseModel):
    project_id: int = Field(gt=0)


class BatchAssignTaskProjectCommand(BaseModel):
    task_ids: List[int] = Field(min_items=1, max_items=500)
    project_id: int = Field(gt=0)

    @validator("task_ids")
    def _normalize_project_task_ids(cls, task_ids):
        result = []
        for value in task_ids:
            task_id = int(value)
            if task_id <= 0:
                raise ValueError("task_ids 只能包含正整数")
            if task_id not in result:
                result.append(task_id)
        return result


class BatchAssignDatasetCategoryCommand(BaseModel):
    task_ids: List[int] = Field(min_items=1, max_items=500)
    dataset_category_id: Optional[int] = Field(default=None, gt=0)
    sync_unclassified_downstream: bool = True

    @validator("task_ids")
    def _normalize_task_ids(cls, task_ids):
        result = []
        for value in task_ids:
            task_id = int(value)
            if task_id <= 0:
                raise ValueError("task_ids 只能包含正整数")
            if task_id not in result:
                result.append(task_id)
        return result
