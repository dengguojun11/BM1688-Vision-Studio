"""Repair the canonical geometry contract for the active plate preset.

Revision ID: zm9n0o1p2q3
Revises: zl8m9n0o1p2
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "zm9n0o1p2q3"
down_revision = "zl8m9n0o1p2"
branch_labels = None
depends_on = None


_SLUG = "plateDetection"
_PROFILE = "plate_detection_v1"
_PROFILE_CONFIG = {
    "profile": _PROFILE,
    "rect_label": "doubleEP",
    "polygon_label": "platePoint",
    "attributes": {},
}
_LABEL_SPECS = (
    {
        "label_key": "车牌框",
        "label_value": "doubleEP",
        "annotation_kind": "bbox",
        "color": "#ff6600",
        "sort_order": 0,
    },
    {
        "label_key": "车牌四角点",
        "label_value": "platePoint",
        "annotation_kind": "polygon",
        "color": "#00aaee",
        "sort_order": 1,
    },
)


def _tables(bind) -> set[str]:
    return set(inspect(bind).get_table_names())


def _filtered_values(table: sa.Table, values: dict) -> dict:
    """Keep a repair usable on databases with older optional columns."""

    return {key: value for key, value in values.items() if key in table.c}


def _normalized(value: object) -> str:
    return str(value or "").strip().lower()


def _find_preset(bind, table: sa.Table):
    expression = sa.func.lower(sa.func.trim(table.c.slug)) == _SLUG.lower()
    rows = (
        bind.execute(sa.select(table).where(expression).order_by(table.c.id))
        .mappings()
        .all()
    )
    if not rows:
        return None
    # A malformed database can contain both an active and a tombstoned row.
    # Prefer the active one while never reviving a tombstone.
    return next((row for row in rows if row.get("deleted_at") is None), rows[0])


def _find_label(bind, table: sa.Table, label_value: str):
    normalized = _normalized(label_value)
    normalized_column = sa.func.lower(sa.func.trim(table.c.normalized_label_value))
    row = (
        bind.execute(
            sa.select(table).where(normalized_column == normalized).order_by(table.c.id)
        )
        .mappings()
        .first()
    )
    if row is not None:
        return row

    # A few pre-contract databases populated label_value but left the
    # normalized column stale.  Resolve those rows before inserting a second
    # canonical label.
    value_column = sa.func.lower(sa.func.trim(table.c.label_value))
    return (
        bind.execute(
            sa.select(table).where(value_column == normalized).order_by(table.c.id)
        )
        .mappings()
        .first()
    )


def _used_colors(bind, table: sa.Table, *, exclude_id: int | None = None) -> set[str]:
    query = sa.select(table.c.id, table.c.color).where(table.c.color.is_not(None))
    if exclude_id is not None:
        query = query.where(table.c.id != int(exclude_id))
    return {
        _normalized(row.color)
        for row in bind.execute(query).all()
        if _normalized(row.color)
    }


def _available_color(
    bind,
    table: sa.Table,
    desired: str,
    *,
    label_id: int | None = None,
    seed: str,
) -> str:
    """Choose a color that cannot violate the global unique color constraint."""

    used = _used_colors(bind, table, exclude_id=label_id)
    candidates = [
        desired,
        "#00aaef",
        "#00aaec",
        "#ff6601",
        "#ff6602",
    ]
    for candidate in candidates:
        normalized = _normalized(candidate)
        if normalized and normalized not in used:
            return candidate

    attempt = 0
    while True:
        digest = hashlib.sha256(
            f"labelu-plate-detection-color:{seed}:{attempt}".encode("utf-8")
        ).hexdigest()
        candidate = f"#{digest[:6]}"
        if candidate not in used:
            return candidate
        attempt += 1


def _ensure_label(bind, table: sa.Table, spec: dict, now: datetime):
    row = _find_label(bind, table, spec["label_value"])
    if row is None:
        color = _available_color(
            bind,
            table,
            spec["color"],
            seed=spec["label_value"],
        )
        values = _filtered_values(
            table,
            {
                "label_key": spec["label_key"],
                "label_value": spec["label_value"],
                "normalized_label_value": _normalized(spec["label_value"]),
                "annotation_kind": spec["annotation_kind"],
                "color": color,
                "attributes_json": "[]",
                "status": "active",
                "version": 1,
                "sort_order": spec["sort_order"],
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
            },
        )
        result = bind.execute(table.insert().values(**values))
        label_id = int(result.inserted_primary_key[0])
        return (
            bind.execute(sa.select(table).where(table.c.id == label_id))
            .mappings()
            .one()
        )

    label_id = int(row["id"])
    current_color = str(row.get("color") or "").strip()
    color = current_color
    if not current_color or _normalized(current_color) in _used_colors(
        bind, table, exclude_id=label_id
    ):
        color = _available_color(
            bind,
            table,
            spec["color"],
            label_id=label_id,
            seed=f"{spec['label_value']}:{label_id}",
        )

    values = _filtered_values(
        table,
        {
            "label_key": row.get("label_key") or spec["label_key"],
            "label_value": row.get("label_value") or spec["label_value"],
            "normalized_label_value": _normalized(spec["label_value"]),
            "annotation_kind": spec["annotation_kind"],
            "color": color,
            "status": row.get("status") or "active",
            "updated_at": now,
        },
    )
    bind.execute(table.update().where(table.c.id == label_id).values(**values))
    return bind.execute(sa.select(table).where(table.c.id == label_id)).mappings().one()


def _find_item(bind, table: sa.Table, preset_id: int, label_value: str):
    expression = sa.func.lower(sa.func.trim(table.c.label_value)) == _normalized(
        label_value
    )
    return (
        bind.execute(
            sa.select(table)
            .where(table.c.preset_id == int(preset_id), expression)
            .order_by(table.c.id)
        )
        .mappings()
        .first()
    )


def _sync_item(
    bind,
    table: sa.Table,
    *,
    preset_id: int,
    spec: dict,
    label_color: str,
    now: datetime,
) -> None:
    row = _find_item(bind, table, preset_id, spec["label_value"])
    values = {
        "label_key": spec["label_key"],
        "label_value": spec["label_value"],
        "color": label_color,
        "sort_order": spec["sort_order"],
    }
    if row is None:
        values["preset_id"] = int(preset_id)
        values["created_at"] = now
        bind.execute(table.insert().values(**_filtered_values(table, values)))
        return
    bind.execute(
        table.update()
        .where(table.c.id == int(row["id"]))
        .values(**_filtered_values(table, values))
    )


def _sync_relation(
    bind,
    table: sa.Table,
    *,
    preset_id: int,
    label_id: int,
    sort_order: int,
    now: datetime,
) -> None:
    row = (
        bind.execute(
            sa.select(table)
            .where(
                table.c.preset_id == int(preset_id), table.c.label_id == int(label_id)
            )
            .order_by(table.c.id)
        )
        .mappings()
        .first()
    )
    if row is None:
        bind.execute(
            table.insert().values(
                **_filtered_values(
                    table,
                    {
                        "preset_id": int(preset_id),
                        "label_id": int(label_id),
                        "sort_order": sort_order,
                        "created_at": now,
                    },
                )
            )
        )
        return
    bind.execute(
        table.update()
        .where(table.c.id == int(row["id"]))
        .values(**_filtered_values(table, {"sort_order": sort_order}))
    )


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    required = {"label_preset", "annotation_label"}
    if not required.issubset(tables):
        return

    metadata = sa.MetaData()
    preset = sa.Table("label_preset", metadata, autoload_with=bind)
    labels = sa.Table("annotation_label", metadata, autoload_with=bind)
    preset_row = _find_preset(bind, preset)
    if preset_row is not None and preset_row.get("deleted_at") is not None:
        # A user explicitly retired this preset.  The migration must not
        # resurrect it or recreate a duplicate under the same slug.
        return

    now = datetime.now()
    profile_json = json.dumps(
        _PROFILE_CONFIG, ensure_ascii=False, separators=(",", ":")
    )
    if preset_row is None:
        values = _filtered_values(
            preset,
            {
                "slug": _SLUG,
                "name": "电动车牌检测与四角定位",
                "description": "车牌框 + 四角点，仅训练车牌定位，不识别号码、颜色或类型",
                "training_profile": _PROFILE,
                "training_profile_config": profile_json,
                "sort_order": 1,
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
            },
        )
        result = bind.execute(preset.insert().values(**values))
        preset_id = int(result.inserted_primary_key[0])
    else:
        preset_id = int(preset_row["id"])
        bind.execute(
            preset.update()
            .where(preset.c.id == preset_id)
            .values(
                **_filtered_values(
                    preset,
                    {
                        "name": "电动车牌检测与四角定位",
                        "description": "车牌框 + 四角点，仅训练车牌定位，不识别号码、颜色或类型",
                        "training_profile": _PROFILE,
                        "training_profile_config": profile_json,
                        "updated_at": now,
                    },
                )
            )
        )

    # The canonical relation table and legacy item table were introduced by
    # earlier revisions.  Keep the label repair useful on a partially created
    # local database while retaining the normal full-schema path.
    item = (
        sa.Table("label_preset_item", metadata, autoload_with=bind)
        if "label_preset_item" in tables
        else None
    )
    relation = (
        sa.Table("label_preset_label", metadata, autoload_with=bind)
        if "label_preset_label" in tables
        else None
    )
    for spec in _LABEL_SPECS:
        label = _ensure_label(bind, labels, spec, now)
        label_id = int(label["id"])
        if item is not None:
            _sync_item(
                bind,
                item,
                preset_id=preset_id,
                spec=spec,
                label_color=str(label.get("color") or spec["color"]),
                now=now,
            )
        if relation is not None:
            _sync_relation(
                bind,
                relation,
                preset_id=preset_id,
                label_id=label_id,
                sort_order=spec["sort_order"],
                now=now,
            )


def downgrade() -> None:
    """Data repair has no safe inverse; keep repaired contracts intact."""

    return
