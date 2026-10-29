"""Backfill task label contracts from legacy rectTool configurations.

Revision ID: z8a9b0c1d2e3
Revises: y7z8a9b0c1d2
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "z8a9b0c1d2e3"
down_revision = "y7z8a9b0c1d2"
branch_labels = None
depends_on = None


def _signature(value) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _label_attributes(row) -> list:
    try:
        value = json.loads(row.get("attributes_json") or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = []
    return value if isinstance(value, list) else []


def _legacy_rect_attributes(config: object) -> list[dict]:
    try:
        payload = json.loads(config or "{}") if isinstance(config, str) else config
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    result: list[dict] = []
    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("tool") != "rectTool":
            continue
        attributes = ((tool.get("config") or {}).get("attributes") or [])
        if not isinstance(attributes, list):
            continue
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            value = str(attribute.get("value") or "").strip()
            key = str(attribute.get("key") or "").strip()
            if value or key:
                result.append(attribute)
        break
    return result


def _backfill_task_contracts(bind) -> int:
    tables = set(inspect(bind).get_table_names())
    required = {"task", "annotation_label", "task_annotation_contract", "task_annotation_label"}
    if not required.issubset(tables):
        return 0

    metadata = sa.MetaData()
    task = sa.Table("task", metadata, autoload_with=bind)
    labels = sa.Table("annotation_label", metadata, autoload_with=bind)
    contracts = sa.Table("task_annotation_contract", metadata, autoload_with=bind)
    contract_labels = sa.Table("task_annotation_label", metadata, autoload_with=bind)

    label_rows = bind.execute(
        sa.select(
            labels.c.id,
            labels.c.label_key,
            labels.c.label_value,
            labels.c.normalized_label_value,
            labels.c.color,
            labels.c.annotation_kind,
            labels.c.version,
            labels.c.attributes_json,
        ).where(labels.c.deleted_at.is_(None))
    ).mappings().all()
    by_value = {str(row["normalized_label_value"]): row for row in label_rows}
    by_key: dict[str, list] = {}
    for row in label_rows:
        by_key.setdefault(str(row["label_key"]).strip(), []).append(row)

    sample = sa.Table("task_sample", metadata, autoload_with=bind) if "task_sample" in tables else None
    now = datetime.now()
    created = 0
    task_rows = bind.execute(
        sa.select(task).where(task.c.deleted_at.is_(None))
    ).mappings().all()
    for task_row in task_rows:
        task_id = int(task_row["id"])
        if task_row.get("current_annotation_contract_id") is not None:
            continue
        if bind.execute(
            sa.select(contracts.c.id).where(contracts.c.task_id == task_id).limit(1)
        ).first():
            continue

        attributes = _legacy_rect_attributes(task_row.get("config"))
        resolved = []
        seen_ids: set[int] = set()
        for attribute in attributes:
            value = str(attribute.get("value") or "").strip()
            key = str(attribute.get("key") or "").strip()
            row = by_value.get(value.lower())
            if row is None and key and len(by_key.get(key, [])) == 1:
                row = by_key[key][0]
            if row is None or int(row["id"]) in seen_ids:
                continue
            seen_ids.add(int(row["id"]))
            resolved.append(row)
        if not resolved:
            continue

        ids = [int(row["id"]) for row in resolved]
        values = [str(row["label_value"]) for row in resolved]
        snapshot = [
            {
                "label_id": int(row["id"]),
                "label_key": str(row["label_key"]),
                "label_value": str(row["label_value"]),
                "color": str(row["color"]),
                "annotation_kind": str(row["annotation_kind"] or "bbox"),
                "attributes": _label_attributes(row),
                "version": int(row["version"] or 1),
            }
            for row in resolved
        ]
        has_annotation = False
        if sample is not None:
            sample_state = bind.execute(
                sa.select(sample.c.id)
                .where(
                    sample.c.task_id == task_id,
                    sample.c.deleted_at.is_(None),
                    sample.c.state != "NEW",
                )
                .limit(1)
            ).first()
            has_annotation = sample_state is not None
        contract_result = bind.execute(
            contracts.insert().values(
                task_id=task_id,
                revision=1,
                annotation_kind="bbox",
                label_ids_json=json.dumps(ids),
                labels_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                label_set_signature=_signature(sorted(set(ids))),
                class_order_signature=_signature(values),
                status="review_required" if has_annotation else "draft",
                source_preset_id=None,
                parent_contract_id=None,
                created_by=task_row.get("created_by"),
                created_at=now,
            )
        )
        contract_id = int(contract_result.inserted_primary_key[0])
        bind.execute(
            contract_labels.insert(),
            [
                {
                    "task_id": task_id,
                    "contract_id": contract_id,
                    "label_id": label_id,
                    "sort_order": order,
                    "status": "active",
                    "created_at": now,
                }
                for order, label_id in enumerate(ids)
            ],
        )
        bind.execute(
            task.update()
            .where(task.c.id == task_id)
            .values(current_annotation_contract_id=contract_id)
        )
        created += 1
    return created


def upgrade() -> None:
    _backfill_task_contracts(op.get_bind())


def downgrade() -> None:
    # Contract rows are retained on downgrade so a repeated upgrade does not
    # destroy user-created revisions or historical annotation references.
    return
