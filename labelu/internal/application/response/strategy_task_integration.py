from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StrategyTaskIntegrationResponse(BaseModel):
    id: int
    external_task_id: str
    labelu_task_id: int | None = None
    datasource_id: int | None = None
    dataset_category_id: int | None = None
    dataset_category_key: str | None = None
    task_name: str
    task_type: str
    created: bool
    import_status: str
    sample_count: int = 0
    pre_annotation_count: int = 0
    metadata_consumed: bool = False
    package_id: str | None = None
    status_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
