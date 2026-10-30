"""Commands for the image-cleaning policy editor."""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class PatchImageCleaningPolicyCommand(BaseModel):
    """A partial update for the image-cleaning policy.

    Thresholds are kept as dictionaries at the API boundary (for example,
    ``{"standard": {"threshold": 0.95}}``).  The service owns the full
    policy validation so that values loaded from the governance store follow
    the same rules as values submitted by a client.
    """

    active_preset: Optional[str] = Field(default=None, max_length=32)
    presets: Optional[Dict[str, Any]] = Field(default=None)
    expected_revision: Optional[int] = Field(default=None, ge=1)
