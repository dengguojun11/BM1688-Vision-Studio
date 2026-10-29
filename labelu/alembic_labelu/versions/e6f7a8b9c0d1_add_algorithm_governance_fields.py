"""Add explicit governance fields to the algorithm registry.

The fields are nullable during the rolling migration.  Existing rows are
backfilled from the reserved ``metadata_json`` keys when present; the API
continues to use that JSON as a compatibility fallback for partially upgraded
databases.
"""

import json

from alembic import op
import sqlalchemy as sa


revision = "f7a8b9c0d1e2"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("algorithm_kind", sa.String(length=24)),
    ("runtime_adapter", sa.String(length=128)),
    ("rule_schema_json", sa.Text()),
    ("supported_formats_json", sa.Text()),
    ("min_samples_json", sa.Text()),
    ("quality_gate_json", sa.Text()),
    ("risk_level", sa.String(length=24)),
    ("annotation_contract_version", sa.String(length=64)),
)


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def _columns(bind):
    return {item["name"] for item in sa.inspect(bind).get_columns("algorithm_definition")}


def upgrade() -> None:
    bind = op.get_bind()
    if "algorithm_definition" not in _tables(bind):
        return
    columns = _columns(bind)
    for name, column_type in _COLUMNS:
        if name not in columns:
            op.add_column("algorithm_definition", sa.Column(name, column_type, nullable=True))
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("algorithm_definition")}
    for index_name, column_name in (
        ("ix_algorithm_definition_algorithm_kind", "algorithm_kind"),
        ("ix_algorithm_definition_risk_level", "risk_level"),
    ):
        if index_name not in indexes:
            op.create_index(index_name, "algorithm_definition", [column_name])

    # Preserve operator edits: only fill a new column when it is still NULL.
    # Invalid metadata is ignored and will be served through normal defaults.
    rows = bind.execute(
        sa.text(
            "SELECT id, metadata_json FROM algorithm_definition"
        )
    ).mappings()
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        values = {
            "algorithm_kind": metadata.get("algorithm_kind") or "model",
            "runtime_adapter": metadata.get("runtime_adapter"),
            "rule_schema_json": _json_value(metadata.get("rule_schema")),
            "supported_formats_json": _json_value(metadata.get("supported_formats") or []),
            "min_samples_json": _json_value(metadata.get("min_samples")),
            "quality_gate_json": _json_value(metadata.get("quality_gate")),
            "risk_level": metadata.get("risk_level") or "standard",
            "annotation_contract_version": metadata.get("annotation_contract_version") or "v1",
        }
        for name, value in values.items():
            if value is None:
                continue
            bind.execute(
                sa.text(
                    f"UPDATE algorithm_definition SET {name} = :value "
                    f"WHERE id = :id AND {name} IS NULL"
                ),
                {"value": value, "id": row["id"]},
            )


def _json_value(value):
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def downgrade() -> None:
    bind = op.get_bind()
    if "algorithm_definition" not in _tables(bind):
        return
    indexes = {item["name"] for item in sa.inspect(bind).get_indexes("algorithm_definition")}
    for index_name in (
        "ix_algorithm_definition_risk_level",
        "ix_algorithm_definition_algorithm_kind",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="algorithm_definition")
    columns = _columns(bind)
    for name, _column_type in reversed(_COLUMNS):
        if name in columns:
            op.drop_column("algorithm_definition", name)
