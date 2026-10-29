from __future__ import annotations

import json
from typing import Any

import labelu.internal.domain.models  # noqa: F401

from labelu.internal.adapter.persistence import crud_system_data_dictionary
from labelu.internal.common.db import Base, session_scope
from labelu.internal.common.schema_governance import (
    apply_schema_comment_defaults,
    build_data_dictionary_entries,
    build_high_value_json_governance_entries,
)
from labelu.internal.common.system_config_provider import is_governance_store_ready


def build_system_data_dictionary_records(*, include_governance: bool = False) -> list[dict[str, Any]]:
    apply_schema_comment_defaults(Base.metadata, include_governance=include_governance)
    return build_data_dictionary_entries(
        Base.metadata,
        include_governance=include_governance,
    ) + build_high_value_json_governance_entries(Base.metadata)


def bootstrap_system_data_dictionary(*, include_governance: bool = False) -> dict[str, Any]:
    if not is_governance_store_ready():
        return {"status": "store_not_ready", "created": 0, "updated": 0, "total": 0}

    created = 0
    updated = 0
    records = build_system_data_dictionary_records(include_governance=include_governance)
    with session_scope() as db:
        with db.begin():
            for record in records:
                row = crud_system_data_dictionary.get_by_object(
                    db,
                    object_type=record["object_type"],
                    schema_name=record["schema_name"],
                    table_name=record["table_name"],
                    column_name=record["column_name"],
                    object_key=record["object_key"],
                )
                payload = {
                    "name_zh": record["name_zh"],
                    "comment_zh": record["comment_zh"],
                    "business_definition": record["business_definition"],
                    "data_type": record["data_type"],
                    "enum_values_json": record["enum_values_json"],
                    "validation_rule_json": record["validation_rule_json"],
                    "scope_desc": record["scope_desc"],
                    "is_sensitive": record["is_sensitive"],
                    "source_type": record["source_type"],
                    "status": record["status"],
                }
                if row is None:
                    crud_system_data_dictionary.create(
                        db,
                        object_type=record["object_type"],
                        schema_name=record["schema_name"],
                        table_name=record["table_name"],
                        column_name=record["column_name"],
                        object_key=record["object_key"],
                        **payload,
                    )
                    created += 1
                else:
                    crud_system_data_dictionary.update(db, row, **payload)
                    updated += 1
    return {"status": "ok", "created": created, "updated": updated, "total": len(records)}


def main() -> None:
    result = bootstrap_system_data_dictionary()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
