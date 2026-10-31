"""数据库维护的标注taxonomy：拉框预设、全局分类维度与选项。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from labelu.internal.common.db import Base


class LabelPreset(Base):
    """侧边栏可选的拉框标签方案（如：垃圾暴露）。"""

    __tablename__ = "label_preset"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    training_profile = Column(
        String(64),
        nullable=True,
        index=True,
        comment="复合训练配置标识；为空时按普通矩形检测预设处理",
    )
    training_profile_config = Column(
        Text,
        nullable=True,
        comment="复合训练配置JSON文本",
    )
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    items = relationship(
        "LabelPresetItem",
        back_populates="preset",
        cascade="all, delete-orphan",
        order_by="LabelPresetItem.sort_order",
    )


class LabelPresetItem(Base):
    """单个预设标签及其绘制几何类型。"""

    __tablename__ = "label_preset_item"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    preset_id = Column(Integer, ForeignKey("label_preset.id"), nullable=False, index=True)
    label_key = Column(String(128), nullable=False)
    label_value = Column(String(128), nullable=False)
    color = Column(String(32), nullable=False)
    annotation_kind = Column(
        String(32),
        nullable=False,
        default="bbox",
        server_default="bbox",
        comment="标签绘制几何类型：bbox 或 polygon",
    )
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now)

    preset = relationship("LabelPreset", back_populates="items")


class AnnotationLabel(Base):
    """Canonical, globally unique annotation label."""

    __tablename__ = "annotation_label"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    label_key = Column(String(128), nullable=False)
    label_value = Column(String(128), nullable=False)
    normalized_label_value = Column(String(128), nullable=False, unique=True, index=True)
    annotation_kind = Column(String(32), nullable=False, default="bbox", index=True)
    color = Column(String(32), nullable=False, default="#1677ff", unique=True)
    attributes_json = Column(Text, nullable=False, default="[]")
    description = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


class AnnotationLabelAlias(Base):
    __tablename__ = "annotation_label_alias"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "normalized_external_value",
            name="uq_annotation_label_alias_source_value",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    source_system = Column(String(64), nullable=False, index=True)
    external_label_value = Column(String(128), nullable=False)
    normalized_external_value = Column(String(128), nullable=False)
    label_id = Column(Integer, ForeignKey("annotation_label.id"), nullable=False, index=True)
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    label = relationship("AnnotationLabel")


class LabelPresetLabel(Base):
    __tablename__ = "label_preset_label"
    __table_args__ = (
        UniqueConstraint("preset_id", "label_id", name="uq_label_preset_label"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    preset_id = Column(Integer, ForeignKey("label_preset.id"), nullable=False, index=True)
    label_id = Column(Integer, ForeignKey("annotation_label.id"), nullable=False, index=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.now)

    label = relationship("AnnotationLabel")


class DatasetCategory(Base):
    __tablename__ = "dataset_category"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    category_key = Column(String(128), nullable=False, unique=True, index=True)
    name = Column(String(128), nullable=False)
    description = Column(String(512), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="active", index=True)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


class TrainingRecipe(Base):
    __tablename__ = "training_recipe"
    __table_args__ = (
        UniqueConstraint("recipe_key", "version", name="uq_training_recipe_key_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    recipe_key = Column(String(128), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    name = Column(String(128), nullable=False)
    job_type = Column(String(64), nullable=False, default="yolo_detection")
    model_family = Column(String(64), nullable=False, default="yolov11")
    annotation_kind = Column(String(32), nullable=False, default="bbox")
    component_contract_json = Column(Text, nullable=False, default="{}")
    default_hyperparams_json = Column(Text, nullable=False, default="{}")
    status = Column(String(32), nullable=False, default="active", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


class RuntimePublishTemplate(Base):
    __tablename__ = "runtime_publish_template"
    __table_args__ = (
        UniqueConstraint(
            "template_key",
            "environment_id",
            "interface_version",
            name="uq_runtime_publish_template_contract",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    template_key = Column(String(128), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    environment_id = Column(String(128), nullable=False, default="default")
    runtime_profile = Column(String(128), nullable=False, index=True)
    interface_version = Column(Integer, nullable=False, default=1)
    aicm_model_set_id = Column(String(128), nullable=True)
    expected_model_alg_type = Column(String(128), nullable=True)
    capability_contract_json = Column(Text, nullable=False, default="{}")
    template_sha256 = Column(String(64), nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    status = Column(String(32), nullable=False, default="active", index=True)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


class TaxonomyAuditLog(Base):
    __tablename__ = "taxonomy_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    entity_type = Column(String(64), nullable=False, index=True)
    entity_id = Column(Integer, nullable=False, index=True)
    action = Column(String(32), nullable=False)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    change_reason = Column(String(512), nullable=True)
    request_id = Column(String(128), nullable=True)
    source_ip = Column(String(64), nullable=True)
    changed_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    changed_at = Column(DateTime(timezone=True), default=datetime.now, index=True)


class ThirdPartyRequestReceipt(Base):
    __tablename__ = "third_party_request_receipt"
    __table_args__ = (
        UniqueConstraint(
            "source_system", "nonce", name="uq_third_party_request_source_nonce"
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    source_system = Column(String(64), nullable=False, index=True)
    nonce = Column(String(128), nullable=False)
    request_method = Column(String(16), nullable=False)
    request_path = Column(String(512), nullable=False)
    request_sha256 = Column(String(64), nullable=False)
    received_at = Column(DateTime(timezone=True), default=datetime.now, index=True)


class GlobalTagDimension(Base):
    """全局属性维度（如场景类型、光照）。"""

    __tablename__ = "global_tag_dimension"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    field_key = Column(String(64), unique=True, nullable=False, index=True)
    name_cn = Column(String(128), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    options = relationship(
        "GlobalTagOption",
        back_populates="dimension",
        cascade="all, delete-orphan",
        order_by="GlobalTagOption.sort_order",
    )


class GlobalTagOption(Base):
    """维度下的可选值。"""

    __tablename__ = "global_tag_option"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    dimension_id = Column(
        Integer, ForeignKey("global_tag_dimension.id"), nullable=False, index=True
    )
    name_cn = Column(String(128), nullable=False)
    value_en = Column(String(128), nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now)

    dimension = relationship("GlobalTagDimension", back_populates="options")
