from __future__ import annotations

import json
from typing import Any, Iterable

from sqlalchemy import MetaData
from sqlalchemy.sql.schema import Column, Table


BUSINESS_TABLE_PREFIX_EXCLUDES = ("system_", "alembic_")

TABLE_COMMENT_OVERRIDES: dict[str, str] = {
    "global_tag_dimension": "全局标签维度",
    "label_preset": "标注预设",
    "user": "用户",
    "data_source": "外部数据源",
    "global_tag_option": "全局标签选项",
    "label_preset_item": "标注预设项",
    "tagtool_classification_model": "分类模型配置",
    "task": "标注任务",
    "yolo_imported_model": "YOLO导入模型",
    "auto_label_job": "自动标注作业",
    "export_job": "导出作业",
    "locateanything_preannotate_job": "LocateAnything预标注作业",
    "sample_bounds_check_job": "样本框线检查作业",
    "strategy_task_integration": "策略任务接入记录",
    "tagtool_preannotate_job": "TagTool预标注作业",
    "task_attachment": "任务附件",
    "task_collaborator": "任务协作者",
    "yolo_preannotate_job": "YOLO预标注作业",
    "yolo_train_job": "YOLO训练作业",
    "edge_device_job": "边端设备",
    "task_pre_annotation": "样本预标注",
    "provider_registry": "Provider能力目录",
    "task_sample": "任务样本",
    "yolo_bm1688_export": "BM1688导出作业",
    "yolo_eval_report": "YOLO评估报告",
    "sample_edit_log": "样本编辑日志",
    "task_sample_updater": "样本更新人",
    "yolo_board_validation": "板端验证记录",
    "yolo_edge_validation_job": "边端验证作业",
}

TABLE_DEFINITION_OVERRIDES: dict[str, str] = {
    "task": "保存标注任务主数据、任务配置和审核摘要。",
    "task_sample": "保存任务样本当前标注结果、状态和统计信息。",
    "task_pre_annotation": "保存任务样本的预标注结果。",
    "provider_registry": "保存7GAI预标注Provider的版本、能力和契约元数据。",
    "strategy_task_integration": "保存外部策略任务导入 LabelU 的映射关系与导入状态。",
    "yolo_train_job": "保存 YOLO 训练作业的参数、状态和训练产物信息。",
    "yolo_edge_validation_job": "保存边端验证任务的执行输入、阈值与结果摘要。",
    "yolo_board_validation": "保存板端验证结果与报告。",
}

COLUMN_COMMENT_OVERRIDES: dict[tuple[str, str], str] = {
    ("task", "config"): "任务配置JSON文本，用于保存任务级标注界面与流程配置",
    ("task", "annotation_audit_json"): "标注审核摘要JSON文本，用于保存任务级审核统计与结论",
    ("task_sample", "data"): "样本标注结果JSON文本，用于保存当前样本的完整标注载荷",
    ("label_preset", "training_profile_config"): "复合训练配置JSON文本",
    ("task_pre_annotation", "data"): "样本预标注结果JSON文本，用于保存模型生成的原始标注载荷",
    ("strategy_task_integration", "external_task_id"): "外部任务ID",
    ("strategy_task_integration", "labeling_labels_json"): "标注标签定义JSON文本",
    ("strategy_task_integration", "target_json"): "目标对象定义JSON文本",
    ("strategy_task_integration", "experiment_json"): "实验上下文JSON文本",
    ("strategy_task_integration", "metadata_json"): "元数据JSON文本",
    ("export_job", "sample_ids"): "导出样本ID列表JSON文本",
    ("tagtool_classification_model", "label_map_json"): "分类标签映射JSON文本",
    ("yolo_train_job", "hyperparams_json"): "训练超参数JSON文本",
    ("yolo_train_job", "source_task_ids_json"): "来源任务ID列表JSON文本",
    ("yolo_train_job", "component_status_json"): "复合训练组件状态JSON文本",
    ("yolo_train_job", "component_artifacts_json"): "复合训练组件产物JSON文本",
    ("yolo_train_job", "remote_status_json"): "远程训练状态JSON文本",
    ("yolo_edge_validation_job", "sample_ids_json"): "抽样样本ID列表JSON文本",
    ("yolo_edge_validation_job", "thresholds_json"): "阈值配置JSON文本",
    ("yolo_edge_validation_job", "ai_thresholds_json"): "AI阈值建议JSON文本",
    ("yolo_edge_validation_job", "result_json"): "边端验证结果JSON文本",
    ("yolo_board_validation", "report_json"): "板端验证报告JSON文本",
}

NAME_OVERRIDES: dict[tuple[str, str], str] = {
    ("task", "config"): "任务配置JSON",
    ("task", "annotation_audit_json"): "标注审核摘要JSON",
    ("task_sample", "data"): "样本标注结果JSON",
    ("label_preset", "training_profile_config"): "复合训练配置JSON",
    ("task_pre_annotation", "data"): "样本预标注结果JSON",
    ("strategy_task_integration", "labeling_labels_json"): "标注标签定义JSON",
    ("strategy_task_integration", "target_json"): "目标对象定义JSON",
    ("strategy_task_integration", "experiment_json"): "实验上下文JSON",
    ("strategy_task_integration", "metadata_json"): "元数据JSON",
    ("export_job", "sample_ids"): "导出样本ID列表JSON",
    ("tagtool_classification_model", "label_map_json"): "分类标签映射JSON",
    ("yolo_train_job", "hyperparams_json"): "训练超参数JSON",
    ("yolo_train_job", "source_task_ids_json"): "来源任务ID列表JSON",
    ("yolo_train_job", "component_status_json"): "复合训练组件状态JSON",
    ("yolo_train_job", "component_artifacts_json"): "复合训练组件产物JSON",
    ("yolo_train_job", "remote_status_json"): "远程训练状态JSON",
    ("yolo_edge_validation_job", "sample_ids_json"): "抽样样本ID列表JSON",
    ("yolo_edge_validation_job", "thresholds_json"): "阈值配置JSON",
    ("yolo_edge_validation_job", "ai_thresholds_json"): "AI阈值建议JSON",
    ("yolo_edge_validation_job", "result_json"): "边端验证结果JSON",
    ("yolo_board_validation", "report_json"): "板端验证报告JSON",
}

GLOBAL_NAME_OVERRIDES: dict[str, str] = {
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "deleted_at": "删除时间",
    "created_by": "创建人ID",
    "updated_by": "更新人ID",
}

GLOBAL_COMMENT_OVERRIDES: dict[str, str] = {
    "created_at": "创建时间",
    "updated_at": "更新时间",
    "deleted_at": "删除时间",
    "created_by": "创建人ID",
    "updated_by": "更新人ID",
}

TOKEN_ZH: dict[str, str] = {
    "ai": "AI",
    "algorithm": "算法",
    "annotation": "标注",
    "attachment": "附件",
    "audit": "审核",
    "auto": "自动",
    "base": "基础",
    "board": "板端",
    "bm1688": "BM1688",
    "bounds": "边界",
    "bucket": "桶",
    "category": "类别",
    "chip": "芯片",
    "classification": "分类",
    "collaborator": "协作者",
    "comment": "说明",
    "completed": "完成",
    "config": "配置",
    "connect": "连接",
    "count": "数量",
    "created": "创建",
    "creator": "创建人",
    "data": "数据",
    "dataset": "数据集",
    "datasource": "数据源",
    "default": "默认",
    "deleted": "删除",
    "description": "描述",
    "device": "设备",
    "dimension": "维度",
    "docker": "Docker",
    "edge": "边端",
    "edit": "编辑",
    "enabled": "启用",
    "endpoint": "地址",
    "epochs": "训练轮次",
    "error": "错误",
    "eval": "评估",
    "experiment": "实验",
    "export": "导出",
    "external": "外部",
    "file": "文件",
    "filename": "文件名",
    "full": "全量",
    "global": "全局",
    "host": "主机",
    "hyperparams": "超参数",
    "id": "ID",
    "image": "图像",
    "import": "导入",
    "imported": "已导入",
    "imgsz": "图像尺寸",
    "inner": "内部",
    "instruction": "指令",
    "integration": "集成",
    "job": "作业",
    "json": "JSON",
    "key": "键",
    "label": "标签",
    "labeling": "标注",
    "last": "最后",
    "locateanything": "LocateAnything",
    "map": "映射",
    "media": "媒体",
    "metadata": "元数据",
    "metrics": "指标",
    "mode": "模式",
    "model": "模型",
    "name": "名称",
    "next": "下一轮",
    "node": "节点",
    "onnx": "ONNX",
    "option": "选项",
    "owner": "创建人",
    "password": "密码",
    "path": "路径",
    "port": "端口",
    "pre": "预",
    "prefix": "前缀",
    "preset": "预设",
    "provider": "提供方",
    "quality": "质量",
    "region": "区域",
    "remote": "远程",
    "report": "报告",
    "required": "必需",
    "result": "结果",
    "run": "运行",
    "sample": "样本",
    "schema": "结构版本",
    "scope": "作用域",
    "secret": "密钥",
    "source": "来源",
    "sophon": "Sophon",
    "ssh": "SSH",
    "started": "开始",
    "status": "状态",
    "strategy": "策略",
    "summary": "摘要",
    "tag": "标签",
    "tagtool": "TagTool",
    "target": "目标",
    "task": "任务",
    "thresholds": "阈值",
    "timeout": "超时时间",
    "tips": "提示",
    "token": "令牌",
    "train": "训练",
    "type": "类型",
    "unlabeled": "未标注",
    "updated": "更新",
    "updater": "更新人",
    "uri": "URI",
    "url": "地址",
    "use": "使用",
    "user": "用户",
    "validation": "验证",
    "verify": "校验",
    "version": "版本",
    "weights": "权重",
    "yolo": "YOLO",
}

HIGH_VALUE_JSON_COLUMNS: dict[tuple[str, str], dict[str, Any]] = {
    ("task", "config"): {
        "name_zh": "任务配置JSON治理",
        "comment_zh": "任务级配置 JSON 文本，建议优先迁移到 MySQL JSON。",
        "business_definition": "影响标注页面、任务流程和审核行为，属于任务主配置。",
        "scope_desc": "任务级",
        "target_storage": "mysql_json",
        "priority": "P0",
        "phase": 4,
    },
    ("task", "annotation_audit_json"): {
        "name_zh": "标注审核摘要JSON治理",
        "comment_zh": "任务审核摘要 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "用于保存审核统计、风险标记和审核结论。",
        "scope_desc": "任务级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("task_sample", "data"): {
        "name_zh": "样本标注结果JSON治理",
        "comment_zh": "样本标注结果 JSON 文本，建议拆到子表而不是继续堆在单列。",
        "business_definition": "载荷体量大、结构多变，是后续样本级结构化治理的重点。",
        "scope_desc": "样本级",
        "target_storage": "split_child_table",
        "priority": "P0",
        "phase": 4,
    },
    ("task_pre_annotation", "data"): {
        "name_zh": "样本预标注结果JSON治理",
        "comment_zh": "预标注结果 JSON 文本，建议拆到子表或独立明细结构。",
        "business_definition": "与样本标注结果强相关，后续需要按类型拆分明细。",
        "scope_desc": "样本级",
        "target_storage": "split_child_table",
        "priority": "P0",
        "phase": 4,
    },
    ("export_job", "sample_ids"): {
        "name_zh": "导出样本ID列表JSON治理",
        "comment_zh": "导出样本 ID 列表 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "用于描述导出任务覆盖的样本集合。",
        "scope_desc": "导出任务级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("strategy_task_integration", "labeling_labels_json"): {
        "name_zh": "标注标签定义JSON治理",
        "comment_zh": "策略接入标签定义 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存外部标签定义，用于落任务和后续预标注映射。",
        "scope_desc": "策略接入级",
        "target_storage": "mysql_json",
        "priority": "P0",
        "phase": 4,
    },
    ("strategy_task_integration", "target_json"): {
        "name_zh": "目标对象定义JSON治理",
        "comment_zh": "策略接入目标对象 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存策略侧目标定义和数据集目标范围。",
        "scope_desc": "策略接入级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("strategy_task_integration", "experiment_json"): {
        "name_zh": "实验上下文JSON治理",
        "comment_zh": "策略接入实验上下文 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存策略任务来源实验信息和实验参数快照。",
        "scope_desc": "策略接入级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("strategy_task_integration", "metadata_json"): {
        "name_zh": "元数据JSON治理",
        "comment_zh": "策略接入元数据 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存外部元数据与导入过程上下文。",
        "scope_desc": "策略接入级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("tagtool_classification_model", "label_map_json"): {
        "name_zh": "分类标签映射JSON治理",
        "comment_zh": "分类模型标签映射 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存分类模型输出类别到业务标签的映射关系。",
        "scope_desc": "模型级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("yolo_train_job", "hyperparams_json"): {
        "name_zh": "训练超参数JSON治理",
        "comment_zh": "训练超参数 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存训练超参数快照，是训练可重放和审计的关键字段。",
        "scope_desc": "训练作业级",
        "target_storage": "mysql_json",
        "priority": "P0",
        "phase": 4,
    },
    ("yolo_train_job", "source_task_ids_json"): {
        "name_zh": "来源任务ID列表JSON治理",
        "comment_zh": "来源任务 ID 列表 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存合并训练时参与的数据源任务集合。",
        "scope_desc": "训练作业级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("yolo_train_job", "remote_status_json"): {
        "name_zh": "远程训练状态JSON治理",
        "comment_zh": "远程训练状态 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存远程训练上传、启动、同步和回收阶段状态。",
        "scope_desc": "训练作业级",
        "target_storage": "mysql_json",
        "priority": "P0",
        "phase": 4,
    },
    ("yolo_board_validation", "report_json"): {
        "name_zh": "板端验证报告JSON治理",
        "comment_zh": "板端验证报告 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存板端验证原始结果与报告内容。",
        "scope_desc": "验证作业级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("yolo_edge_validation_job", "sample_ids_json"): {
        "name_zh": "抽样样本ID列表JSON治理",
        "comment_zh": "边端验证样本 ID 列表 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存边端验证所覆盖的样本集合。",
        "scope_desc": "验证作业级",
        "target_storage": "mysql_json",
        "priority": "P1",
        "phase": 4,
    },
    ("yolo_edge_validation_job", "thresholds_json"): {
        "name_zh": "阈值配置JSON治理",
        "comment_zh": "边端验证阈值配置 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存人工配置的边端阈值。",
        "scope_desc": "验证作业级",
        "target_storage": "mysql_json",
        "priority": "P0",
        "phase": 4,
    },
    ("yolo_edge_validation_job", "ai_thresholds_json"): {
        "name_zh": "AI阈值建议JSON治理",
        "comment_zh": "AI 阈值建议 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存 AI 推荐的阈值候选与解释。",
        "scope_desc": "验证作业级",
        "target_storage": "mysql_json",
        "priority": "P0",
        "phase": 4,
    },
    ("yolo_edge_validation_job", "result_json"): {
        "name_zh": "边端验证结果JSON治理",
        "comment_zh": "边端验证结果 JSON 文本，建议迁移到 MySQL JSON。",
        "business_definition": "保存边端验证执行结果、对比摘要和分析明细。",
        "scope_desc": "验证作业级",
        "target_storage": "mysql_json",
        "priority": "P0",
        "phase": 4,
    },
}

SENSITIVE_TOKENS = ("password", "secret", "token", "access_key", "private_key", "api_key")


def _metadata_or_default(metadata: MetaData | None = None) -> MetaData:
    if metadata is not None:
        return metadata
    from labelu.internal.common.db import Base

    return Base.metadata


def iter_governed_tables(
    metadata: MetaData | None = None,
    *,
    include_governance: bool = False,
) -> Iterable[Table]:
    md = _metadata_or_default(metadata)
    for table in md.sorted_tables:
        if not include_governance and table.name.startswith(BUSINESS_TABLE_PREFIX_EXCLUDES):
            continue
        yield table


def _phrase_for_tokens(tokens: list[str]) -> str:
    parts: list[str] = []
    for token in tokens:
        mapped = TOKEN_ZH.get(token, token.upper() if token.isupper() else token)
        if mapped == "ID" and parts:
            parts[-1] = f"{parts[-1]}ID"
            continue
        if mapped == "JSON" and parts:
            parts[-1] = f"{parts[-1]}JSON"
            continue
        parts.append(mapped)
    return "".join(parts)


def _default_name_zh(column_name: str) -> str:
    if column_name == "id":
        return "主键ID"
    if column_name.endswith("_id"):
        return f"{_phrase_for_tokens(column_name[:-3].split('_'))}ID"
    if column_name.endswith("_json"):
        return f"{_phrase_for_tokens(column_name[:-5].split('_'))}JSON"
    if column_name.endswith("_at"):
        return f"{_phrase_for_tokens(column_name[:-3].split('_'))}时间"
    if column_name.endswith("_count"):
        return f"{_phrase_for_tokens(column_name[:-6].split('_'))}数量"
    if column_name.startswith("is_"):
        return f"是否{_phrase_for_tokens(column_name[3:].split('_'))}"
    if column_name.startswith("use_"):
        return f"是否使用{_phrase_for_tokens(column_name[4:].split('_'))}"
    return _phrase_for_tokens(column_name.split("_"))


def get_table_comment(table: Table) -> str:
    return str(table.comment or TABLE_COMMENT_OVERRIDES.get(table.name) or _phrase_for_tokens(table.name.split("_"))).strip()


def get_column_name_zh(table: Table, column: Column[Any]) -> str:
    return str(
        NAME_OVERRIDES.get((table.name, column.name))
        or GLOBAL_NAME_OVERRIDES.get(column.name)
        or _default_name_zh(column.name)
    ).strip()


def get_column_comment(table: Table, column: Column[Any]) -> str:
    override = COLUMN_COMMENT_OVERRIDES.get((table.name, column.name))
    if override:
        return override
    global_override = GLOBAL_COMMENT_OVERRIDES.get(column.name)
    if global_override:
        return global_override
    explicit = str(column.comment or "").strip()
    if explicit:
        return explicit
    name_zh = get_column_name_zh(table, column)
    if name_zh.endswith("时间"):
        return f"{name_zh}"
    if name_zh.endswith("ID"):
        return f"{name_zh}"
    return f"{name_zh}字段"


def apply_schema_comment_defaults(
    metadata: MetaData | None = None,
    *,
    include_governance: bool = False,
) -> None:
    for table in iter_governed_tables(metadata, include_governance=include_governance):
        if not str(table.comment or "").strip():
            table.comment = get_table_comment(table)
        for column in table.columns:
            override = COLUMN_COMMENT_OVERRIDES.get((table.name, column.name))
            if override:
                column.comment = override
                continue
            global_override = GLOBAL_COMMENT_OVERRIDES.get(column.name)
            if global_override:
                column.comment = global_override
                continue
            if not str(column.comment or "").strip():
                column.comment = get_column_comment(table, column)


def _is_sensitive(column_name: str) -> bool:
    lowered = column_name.lower()
    return any(token in lowered for token in SENSITIVE_TOKENS)


def build_comment_sync_plan(
    metadata: MetaData | None = None,
    *,
    include_governance: bool = False,
) -> list[dict[str, Any]]:
    apply_schema_comment_defaults(metadata, include_governance=include_governance)
    plan: list[dict[str, Any]] = []
    for table in iter_governed_tables(metadata, include_governance=include_governance):
        plan.append(
            {
                "table_name": table.name,
                "table_comment": get_table_comment(table),
                "columns": [
                    {
                        "column_name": column.name,
                        "comment": get_column_comment(table, column),
                        "data_type": str(column.type),
                    }
                    for column in table.columns
                ],
            }
        )
    return plan


def build_data_dictionary_entries(
    metadata: MetaData | None = None,
    *,
    include_governance: bool = False,
) -> list[dict[str, Any]]:
    apply_schema_comment_defaults(metadata, include_governance=include_governance)
    rows: list[dict[str, Any]] = []
    for table in iter_governed_tables(metadata, include_governance=include_governance):
        table_comment = get_table_comment(table)
        rows.append(
            {
                "object_type": "table",
                "schema_name": "",
                "table_name": table.name,
                "column_name": "",
                "object_key": table.name,
                "name_zh": table_comment,
                "comment_zh": f"{table_comment}业务表",
                "business_definition": TABLE_DEFINITION_OVERRIDES.get(
                    table.name, f"保存{table_comment}相关业务数据。"
                ),
                "data_type": "table",
                "enum_values_json": None,
                "validation_rule_json": None,
                "scope_desc": "表级",
                "is_sensitive": False,
                "source_type": "orm_metadata",
                "status": "active",
            }
        )
        for column in table.columns:
            rows.append(
                {
                    "object_type": "column",
                    "schema_name": "",
                    "table_name": table.name,
                    "column_name": column.name,
                    "object_key": f"{table.name}.{column.name}",
                    "name_zh": get_column_name_zh(table, column),
                    "comment_zh": get_column_comment(table, column),
                    "business_definition": get_column_comment(table, column),
                    "data_type": str(column.type),
                    "enum_values_json": None,
                    "validation_rule_json": None,
                    "scope_desc": "字段级",
                    "is_sensitive": _is_sensitive(column.name),
                    "source_type": "orm_metadata",
                    "status": "active",
                }
            )
    return rows


def build_high_value_json_governance_entries(
    metadata: MetaData | None = None,
) -> list[dict[str, Any]]:
    md = _metadata_or_default(metadata)
    rows: list[dict[str, Any]] = []
    for (table_name, column_name), rule in HIGH_VALUE_JSON_COLUMNS.items():
        table = md.tables.get(table_name)
        if table is None or column_name not in table.c:
            continue
        column = table.c[column_name]
        rows.append(
            {
                "object_type": "json_column",
                "schema_name": "",
                "table_name": table_name,
                "column_name": column_name,
                "object_key": f"{table_name}.{column_name}",
                "name_zh": rule["name_zh"],
                "comment_zh": rule["comment_zh"],
                "business_definition": rule["business_definition"],
                "data_type": f"{column.type} (JSON text)",
                "enum_values_json": None,
                "validation_rule_json": json.dumps(
                    {
                        "target_storage": rule["target_storage"],
                        "priority": rule["priority"],
                        "phase": rule["phase"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "scope_desc": rule["scope_desc"],
                "is_sensitive": _is_sensitive(column_name),
                "source_type": "json_text_governance",
                "status": "planned",
            }
        )
    return rows
