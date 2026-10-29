import json

import labelu.internal.domain.models  # noqa: F401
from labelu.internal.common.db import Base
import labelu.internal.common.schema_governance as gov
from labelu.internal.common.system_data_dictionary_bootstrap import (
    build_system_data_dictionary_records,
)


def test_apply_schema_comment_defaults_fills_business_comments():
    gov.apply_schema_comment_defaults(Base.metadata, include_governance=False)

    task = Base.metadata.tables["task"]
    strategy = Base.metadata.tables["strategy_task_integration"]

    assert task.comment == "标注任务"
    assert task.c.config.comment == "任务配置JSON文本，用于保存任务级标注界面与流程配置"
    assert task.c.updated_at.comment == "更新时间"
    assert strategy.comment == "策略任务接入记录"
    assert strategy.c.external_task_id.comment == "外部任务ID"


def test_build_data_dictionary_entries_covers_tables_and_columns():
    gov.apply_schema_comment_defaults(Base.metadata, include_governance=False)

    entries = gov.build_data_dictionary_entries(Base.metadata, include_governance=False)

    task_table = next(
        item
        for item in entries
        if item["object_type"] == "table" and item["table_name"] == "task"
    )
    task_config = next(
        item
        for item in entries
        if item["object_type"] == "column"
        and item["table_name"] == "task"
        and item["column_name"] == "config"
    )

    assert task_table["name_zh"] == "标注任务"
    assert "任务" in task_table["comment_zh"]
    assert task_config["name_zh"] == "任务配置JSON"
    assert task_config["comment_zh"] == "任务配置JSON文本，用于保存任务级标注界面与流程配置"
    assert task_config["data_type"] == "TEXT"


def test_build_high_value_json_governance_entries_marks_target_strategy():
    gov.apply_schema_comment_defaults(Base.metadata, include_governance=False)

    entries = gov.build_high_value_json_governance_entries(Base.metadata)
    by_key = {
        (item["table_name"], item["column_name"]): item
        for item in entries
    }

    task_config = by_key[("task", "config")]
    task_sample_data = by_key[("task_sample", "data")]
    edge_result = by_key[("yolo_edge_validation_job", "result_json")]

    task_config_rule = json.loads(task_config["validation_rule_json"])
    task_sample_rule = json.loads(task_sample_data["validation_rule_json"])
    edge_result_rule = json.loads(edge_result["validation_rule_json"])

    assert task_config["object_type"] == "json_column"
    assert task_config_rule["target_storage"] == "mysql_json"
    assert task_config_rule["priority"] == "P0"
    assert task_sample_rule["target_storage"] == "split_child_table"
    assert edge_result_rule["phase"] == 4


def test_build_system_data_dictionary_records_contains_json_governance_rows():
    records = build_system_data_dictionary_records(include_governance=False)

    object_types = {item["object_type"] for item in records}
    task_table = next(
        item for item in records if item["object_type"] == "table" and item["table_name"] == "task"
    )
    task_config_json = next(
        item
        for item in records
        if item["object_type"] == "json_column"
        and item["table_name"] == "task"
        and item["column_name"] == "config"
    )

    assert {"table", "column", "json_column"}.issubset(object_types)
    assert task_table["name_zh"] == "标注任务"
    assert json.loads(task_config_json["validation_rule_json"])["target_storage"] == "mysql_json"
