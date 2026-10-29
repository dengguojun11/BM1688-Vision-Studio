"""add the read-only 7GAI Provider capability catalog

This is an additive migration.  It registers the existing pre-annotation
adapters without changing their job tables or writing any prediction data.
Image digests are intentionally NULL until MLOps supplies reproducible
runtime evidence; the API therefore reports these rows as registered rather
than AVAILABLE.
"""

from datetime import datetime
import json

from alembic import op
import sqlalchemy as sa


revision = "g8h9i0j1k2l3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def _rows():
    pending = "已登记现有兼容适配器，等待镜像摘要、工具链和HIL证据"
    common = {
        "input_contract_version": "annotation_contract.v1",
        "output_contract_version": "prediction.v1",
        "config_schema_json": json.dumps({"type": "object"}, separators=(",", ":")),
        "status": "ACTIVE",
        "image_digest": None,
        "toolchain_version": None,
        "availability_reason": pending,
        "metadata_json": json.dumps({"legacy_compatibility": True}, separators=(",", ":")),
    }
    specs = (
        (
            "yolo",
            "11.0.1",
            "YOLO目标检测",
            ["DETECTION", "BATCH", "VIDEO_FRAME"],
            "yolo_preannotate",
            ["IMAGE", "VIDEO_FRAME"],
            ["CPU", "CUDA", "BM1688", "RK3588"],
        ),
        (
            "tagtool_cls",
            "1.0.0",
            "TagTool图像分类",
            ["CLASSIFICATION", "BATCH"],
            "tagtool_preannotate",
            ["IMAGE"],
            ["CPU", "CUDA"],
        ),
        (
            "locateanything",
            "1.0.0",
            "LocateAnything检测",
            ["DETECTION", "BATCH"],
            "locateanything_preannotate",
            ["IMAGE"],
            ["CPU", "CUDA"],
        ),
        (
            "vlx_seek",
            "1.0.0",
            "VLX目标检索",
            ["DETECTION", "BATCH"],
            "vlx_seek_preannotate",
            ["IMAGE"],
            ["CPU", "CUDA"],
        ),
        (
            "rex_omni",
            "1.0.0",
            "Rex Omni检测",
            ["DETECTION", "BATCH"],
            "rex_omni_preannotate",
            ["IMAGE"],
            ["CPU", "CUDA"],
        ),
        (
            "vision_llm",
            "1.0.0",
            "视觉大模型辅助标注",
            ["CLASSIFICATION", "DETECTION", "BATCH"],
            "auto_label",
            ["IMAGE"],
            ["CPU", "CUDA"],
        ),
    )
    now = datetime.now()
    result = []
    for key, version, name, capabilities, adapter, media, chips in specs:
        result.append(
            {
                **common,
                "provider_key": key,
                "provider_version": version,
                "display_name": name,
                "provider_kind": "PREANNOTATION",
                "capabilities_json": json.dumps(capabilities, separators=(",", ":")),
                "runtime_adapter": adapter,
                "supported_media_types_json": json.dumps(media, separators=(",", ":")),
                "supported_chips_json": json.dumps(chips, separators=(",", ":")),
                "created_at": now,
                "updated_at": now,
            }
        )
    return result


def upgrade() -> None:
    bind = op.get_bind()
    if "provider_registry" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "provider_registry",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("provider_key", sa.String(length=128), nullable=False),
            sa.Column("provider_version", sa.String(length=64), nullable=False),
            sa.Column("display_name", sa.String(length=128), nullable=False),
            sa.Column("provider_kind", sa.String(length=32), nullable=False),
            sa.Column("capabilities_json", sa.Text(), nullable=False),
            sa.Column("input_contract_version", sa.String(length=64), nullable=False),
            sa.Column("output_contract_version", sa.String(length=64), nullable=False),
            sa.Column("runtime_adapter", sa.String(length=128), nullable=False),
            sa.Column("supported_media_types_json", sa.Text(), nullable=False),
            sa.Column("supported_chips_json", sa.Text(), nullable=False),
            sa.Column("config_schema_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("image_digest", sa.String(length=256), nullable=True),
            sa.Column("toolchain_version", sa.String(length=128), nullable=True),
            sa.Column("availability_reason", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "provider_key", "provider_version", name="uq_provider_registry_key_version"
            ),
        )
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("provider_registry")}
    for name, columns in (
        ("ix_provider_registry_provider_key", ["provider_key"]),
        ("ix_provider_registry_status", ["status"]),
        ("idx_provider_registry_status_kind", ["status", "provider_kind"]),
    ):
        if name not in indexes:
            op.create_index(name, "provider_registry", columns, unique=False)

    # Seed is idempotent and preserves any operator-created/edited row.
    existing = {
        (row[0], row[1])
        for row in bind.execute(
            sa.text("SELECT provider_key, provider_version FROM provider_registry")
        )
    }
    rows = [row for row in _rows() if (row["provider_key"], row["provider_version"]) not in existing]
    if rows:
        table = sa.table(
            "provider_registry",
            sa.column("provider_key", sa.String()),
            sa.column("provider_version", sa.String()),
            sa.column("display_name", sa.String()),
            sa.column("provider_kind", sa.String()),
            sa.column("capabilities_json", sa.Text()),
            sa.column("input_contract_version", sa.String()),
            sa.column("output_contract_version", sa.String()),
            sa.column("runtime_adapter", sa.String()),
            sa.column("supported_media_types_json", sa.Text()),
            sa.column("supported_chips_json", sa.Text()),
            sa.column("config_schema_json", sa.Text()),
            sa.column("status", sa.String()),
            sa.column("image_digest", sa.String()),
            sa.column("toolchain_version", sa.String()),
            sa.column("availability_reason", sa.Text()),
            sa.column("metadata_json", sa.Text()),
            sa.column("created_at", sa.DateTime()),
            sa.column("updated_at", sa.DateTime()),
        )
        op.bulk_insert(table, rows)


def downgrade() -> None:
    bind = op.get_bind()
    if "provider_registry" not in sa.inspect(bind).get_table_names():
        return
    for name in (
        "idx_provider_registry_status_kind",
        "ix_provider_registry_status",
        "ix_provider_registry_provider_key",
    ):
        indexes = {item["name"] for item in sa.inspect(bind).get_indexes("provider_registry")}
        if name in indexes:
            op.drop_index(name, table_name="provider_registry")
    op.drop_table("provider_registry")
