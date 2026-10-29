"""seed the 31 product capabilities for the 7GAI catalog.

The original registry migration intentionally seeded only the compatibility
``yolo_detection`` primitive.  This data-only revision adds the frozen product
catalog while leaving operator edits and all five internal adapter entries
untouched.  The insert is guarded by ``algorithm_key`` so rerunning a partially
applied migration is harmless on SQLite and MySQL.
"""

from datetime import datetime
import json

from alembic import op
import sqlalchemy as sa

from labelu.internal.common.algorithm_registry import PRODUCT_ALGORITHM_DEFINITIONS


revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    groups = {
        row.group_key: row.id
        for row in bind.execute(sa.text("SELECT id, group_key FROM algorithm_group"))
    }
    existing = {
        row[0]
        for row in bind.execute(sa.text("SELECT algorithm_key FROM algorithm_definition"))
    }
    now = datetime.now()
    rows = []
    for spec in PRODUCT_ALGORITHM_DEFINITIONS:
        if spec.key in existing or spec.group_key not in groups:
            continue
        rows.append(
            {
                "algorithm_key": spec.key,
                "name": spec.name,
                "description": spec.description,
                "task_type": spec.task_type,
                "algorithm_group_id": groups[spec.group_key],
                "label_set_signature": None,
                "required_tools_json": json.dumps(spec.required_tools, ensure_ascii=False),
                "required_attributes_json": json.dumps(spec.required_attributes, ensure_ascii=False),
                "training_adapter": spec.training_adapter,
                "evaluation_adapter": spec.evaluation_adapter,
                "supported_chips_json": json.dumps(spec.supported_chips, ensure_ascii=False),
                "default_recipe": spec.default_recipe,
                "input_contract_json": json.dumps(spec.input_contract, ensure_ascii=False),
                "output_contract_json": json.dumps(spec.output_contract, ensure_ascii=False),
                "status": "ACTIVE",
                "metadata_json": json.dumps(
                    {
                        "algorithm_kind": spec.algorithm_kind,
                        "runtime_adapter": spec.runtime_adapter,
                        "rule_schema": spec.rule_schema,
                        "catalog_kind": spec.catalog_kind,
                    },
                    ensure_ascii=False,
                ),
                "created_at": now,
                "updated_at": now,
            }
        )
    if rows:
        bind.execute(sa.text(
            "INSERT INTO algorithm_definition ("
            "algorithm_key,name,description,task_type,algorithm_group_id,"
            "label_set_signature,required_tools_json,required_attributes_json,"
            "training_adapter,evaluation_adapter,supported_chips_json,default_recipe,"
            "input_contract_json,output_contract_json,status,metadata_json,created_at,updated_at) "
            "VALUES (:algorithm_key,:name,:description,:task_type,:algorithm_group_id,"
            ":label_set_signature,:required_tools_json,:required_attributes_json,"
            ":training_adapter,:evaluation_adapter,:supported_chips_json,:default_recipe,"
            ":input_contract_json,:output_contract_json,:status,:metadata_json,:created_at,:updated_at)"
        ), rows)


def downgrade() -> None:
    bind = op.get_bind()
    keys = [spec.key for spec in PRODUCT_ALGORITHM_DEFINITIONS]
    if not keys:
        return
    bind.execute(
        sa.text("DELETE FROM algorithm_definition WHERE algorithm_key IN (" + ",".join(f":k{i}" for i in range(len(keys))) + ")"),
        {f"k{i}": key for i, key in enumerate(keys)},
    )
