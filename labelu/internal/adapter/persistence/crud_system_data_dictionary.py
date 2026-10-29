from typing import List, Optional

from sqlalchemy.orm import Session

from labelu.internal.domain.models.system_data_dictionary import SystemDataDictionary


def create(
    db: Session,
    *,
    object_type: str,
    schema_name: str = "",
    table_name: str = "",
    column_name: str = "",
    object_key: str = "",
    name_zh: str,
    comment_zh: str,
    business_definition: Optional[str] = None,
    data_type: Optional[str] = None,
    enum_values_json: Optional[str] = None,
    validation_rule_json: Optional[str] = None,
    scope_desc: Optional[str] = None,
    is_sensitive: bool = False,
    source_type: Optional[str] = None,
    status: str = "active",
) -> SystemDataDictionary:
    row = SystemDataDictionary(
        object_type=object_type,
        schema_name=schema_name,
        table_name=table_name,
        column_name=column_name,
        object_key=object_key,
        name_zh=name_zh,
        comment_zh=comment_zh,
        business_definition=business_definition,
        data_type=data_type,
        enum_values_json=enum_values_json,
        validation_rule_json=validation_rule_json,
        scope_desc=scope_desc,
        is_sensitive=is_sensitive,
        source_type=source_type,
        status=status,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, dictionary_id: int) -> Optional[SystemDataDictionary]:
    return (
        db.query(SystemDataDictionary)
        .filter(SystemDataDictionary.id == dictionary_id)
        .first()
    )


def get_by_object(
    db: Session,
    *,
    object_type: str,
    schema_name: str = "",
    table_name: str = "",
    column_name: str = "",
    object_key: str = "",
) -> Optional[SystemDataDictionary]:
    return (
        db.query(SystemDataDictionary)
        .filter(
            SystemDataDictionary.object_type == object_type,
            SystemDataDictionary.schema_name == schema_name,
            SystemDataDictionary.table_name == table_name,
            SystemDataDictionary.column_name == column_name,
            SystemDataDictionary.object_key == object_key,
        )
        .first()
    )


def list_by_table(db: Session, table_name: str) -> List[SystemDataDictionary]:
    return (
        db.query(SystemDataDictionary)
        .filter(SystemDataDictionary.table_name == table_name)
        .order_by(SystemDataDictionary.column_name.asc(), SystemDataDictionary.id.asc())
        .all()
    )


def update(db: Session, row: SystemDataDictionary, **kwargs) -> SystemDataDictionary:
    for key, value in kwargs.items():
        setattr(row, key, value)
    db.flush()
    db.refresh(row)
    return row
