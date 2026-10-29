"""Add label attributes and enforce globally unique colors.

Revision ID: x6y7z8a9b0c1
Revises: w5x6y7z8a9b0
"""

from __future__ import annotations

import colorsys
import hashlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "x6y7z8a9b0c1"
down_revision = "w5x6y7z8a9b0"
branch_labels = None
depends_on = None


def _generated_color(label_id: int, attempt: int) -> str:
    digest = hashlib.sha256(f"labelu-label-color:{label_id}:{attempt}".encode()).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535.0
    saturation = 0.62 + (digest[2] / 255.0) * 0.18
    value = 0.72 + (digest[3] / 255.0) * 0.18
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def _deduplicate_colors(bind) -> None:
    metadata = sa.MetaData()
    labels = sa.Table("annotation_label", metadata, autoload_with=bind)
    rows = bind.execute(
        sa.select(labels.c.id, labels.c.color).order_by(labels.c.id)
    ).mappings().all()
    used: set[str] = set()
    for row in rows:
        label_id = int(row["id"])
        color = str(row.get("color") or "#1677ff").strip().lower()
        if color in used:
            attempt = 0
            color = _generated_color(label_id, attempt)
            while color in used:
                attempt += 1
                color = _generated_color(label_id, attempt)
        used.add(color)
        if color != row.get("color"):
            bind.execute(
                labels.update().where(labels.c.id == label_id).values(color=color)
            )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("annotation_label")}
    if "attributes_json" not in columns:
        with op.batch_alter_table("annotation_label") as batch:
            batch.add_column(sa.Column("attributes_json", sa.Text(), nullable=True))
    bind.execute(
        sa.text(
            "UPDATE annotation_label SET attributes_json = '[]' "
            "WHERE attributes_json IS NULL OR TRIM(attributes_json) = ''"
        )
    )
    columns = {item["name"]: item for item in inspect(bind).get_columns("annotation_label")}
    if columns["attributes_json"].get("nullable", True):
        with op.batch_alter_table("annotation_label") as batch:
            batch.alter_column(
                "attributes_json", existing_type=sa.Text(), nullable=False
            )

    _deduplicate_colors(bind)
    unique_names = {
        item.get("name")
        for item in inspect(bind).get_unique_constraints("annotation_label")
    }
    if "uq_annotation_label_color" not in unique_names:
        with op.batch_alter_table("annotation_label") as batch:
            batch.create_unique_constraint("uq_annotation_label_color", ["color"])


def downgrade() -> None:
    bind = op.get_bind()
    unique_names = {
        item.get("name")
        for item in inspect(bind).get_unique_constraints("annotation_label")
    }
    with op.batch_alter_table("annotation_label") as batch:
        if "uq_annotation_label_color" in unique_names:
            batch.drop_constraint("uq_annotation_label_color", type_="unique")
        if "attributes_json" in {
            item["name"] for item in inspect(bind).get_columns("annotation_label")
        }:
            batch.drop_column("attributes_json")
