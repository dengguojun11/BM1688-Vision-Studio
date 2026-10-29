"""Provider capability catalog used by the 7GAI anti-corruption layer.

The catalog is deliberately read-only from the public API.  It describes
which existing pre-annotation adapters are registered; it does not own a
training or inference job.  Those jobs remain in the legacy models until the
ProviderJob/PredictionSet migration is ready.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.schema import UniqueConstraint

from labelu.internal.common.db import Base


class ProviderRegistry(Base):
    __tablename__ = "provider_registry"
    __table_args__ = (
        UniqueConstraint(
            "provider_key",
            "provider_version",
            name="uq_provider_registry_key_version",
        ),
        Index(
            "idx_provider_registry_status_kind",
            "status",
            "provider_kind",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_key = Column(String(128), nullable=False, index=True)
    provider_version = Column(String(64), nullable=False)
    display_name = Column(String(128), nullable=False)
    provider_kind = Column(String(32), nullable=False, default="PREANNOTATION")
    capabilities_json = Column(Text, nullable=False, default="[]")
    input_contract_version = Column(String(64), nullable=False, default="annotation_contract.v1")
    output_contract_version = Column(String(64), nullable=False, default="prediction.v1")
    runtime_adapter = Column(String(128), nullable=False)
    supported_media_types_json = Column(Text, nullable=False, default="[]")
    supported_chips_json = Column(Text, nullable=False, default="[]")
    config_schema_json = Column(Text, nullable=False, default='{"type":"object"}')
    # ACTIVE only means the catalog entry is usable for discovery.  A provider
    # must also have an image digest/toolchain evidence before a future write
    # API may expose it as AVAILABLE.
    status = Column(String(24), nullable=False, default="ACTIVE", index=True)
    image_digest = Column(String(256), nullable=True)
    toolchain_version = Column(String(128), nullable=True)
    availability_reason = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
