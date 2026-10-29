from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, validator


class RunProviderLocalHilCommand(BaseModel):
    """Input for the credential-free local Provider contract probe.

    The probe uses one synthetic media URI and never writes annotations or
    PredictionSet rows.  Its evidence is explicitly marked ``local`` and is
    therefore not a production HIL release attestation.
    """

    provider_version: str | None = Field(default=None, min_length=1, max_length=64)
    sample_uri: str = Field(default="file:///7gai-hil/sample.jpg", min_length=1, max_length=2000)
    labels: list[Any] = Field(default_factory=lambda: [{"name": "person", "tool": "rectTool"}], min_items=1)

    @validator("sample_uri", pre=True)
    def normalize_sample_uri(cls, value: str) -> str:
        return str(value).strip()

