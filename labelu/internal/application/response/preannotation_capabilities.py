from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class PreannotationCapabilityItem(BaseModel):
    """A task-scoped pre-annotation entry point.

    ``visible`` is deliberately separate from ``available``: remote providers
    without an administrator-managed, tested configuration are hidden, while
    a visible local provider can still explain that no compatible model exists.
    """

    key: str
    label: str
    kind: str = "local"
    visible: bool = False
    available: bool = False
    reason: Optional[str] = None
    mode: str = ""
    supports_batch: bool = False
    supports_sample: bool = False
    model_count: int = 0
    config_id: Optional[str] = None
    endpoint: Optional[str] = None
    models: List[Any] = Field(default_factory=list)


class PreannotationCapabilitiesResponse(BaseModel):
    items: List[PreannotationCapabilityItem]
