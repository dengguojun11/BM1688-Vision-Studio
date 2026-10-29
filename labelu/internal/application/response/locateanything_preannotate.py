from datetime import datetime
from typing import Union

from pydantic import BaseModel


class LocateAnythingPreannotateJobResponse(BaseModel):
    id: int
    task_id: int
    status: str
    sample_count: int
    processed_count: int
    success_count: int
    failed_count: int
    model: str
    error_message: Union[str, None] = None
    created_at: Union[datetime, None] = None
