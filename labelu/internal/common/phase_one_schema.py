"""一期数据库建表范围。

一期不再创建已经取消的项目、组织、场景、设备发布和设备代理控制面。
这个模块是唯一的建表范围入口，Alembic 空库引导和应用初始化必须复用，
避免 ``env.py``、迁移脚本和应用启动各自维护一套不一致的排除名单。

历史生产库中的遗留表不会被此模块删除；它们仍由迁移和只读审计处理。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.sql.schema import Table


# 这些表属于已经取消的一期外部控制面。名称集中维护，新增模型默认不会
# 意外进入空库，后续扩展必须显式加入 ``PHASE_ONE_TABLE_ALLOWLIST``。
PHASE_ONE_RETIRED_TABLES = frozenset(
    {
        "organization",
        "scene",
        "project",
        "project_member",
        "deployment_release",
        "deployment_target",
        "deployment_observation",
        "deployment_transition_audit",
        "edge_agent",
        "edge_agent_release",
        "edge_agent_deployment",
        "edge_model_deployment",
        "edge_device_job",
        "model_optimization_job",
        "rule_device_signature_report",
    }
)

# 空库只允许创建下列一期业务表。显式白名单保证未来新增ORM模型不会
# 绕过方案评审直接进入空库。列表按名称排序，便于迁移审计和代码评审。
PHASE_ONE_TABLE_ALLOWLIST = frozenset(
    {
        "active_learning_batch",
        "active_learning_batch_item",
        "algorithm_definition",
        "algorithm_group",
        "annotation_label",
        "annotation_label_alias",
        "annotation_prompt_binding",
        "annotation_review",
        "auto_label_job",
        "auto_training_audit_event",
        "auto_training_evaluation_run",
        "auto_training_outbox",
        "auto_training_plan",
        "auto_training_plan_version",
        "auto_training_round",
        "auto_training_round_sample",
        "auto_training_sample_pool",
        "auto_training_task_scope",
        "bm1688_calibration_manifest",
        "data_source",
        "dataset",
        "dataset_category",
        "dataset_label_contract",
        "dataset_label_contract_item",
        "dataset_version",
        "edge_hard_example",
        "experiment_dataset_group",
        "experiment_dataset_import",
        "export_job",
        "feedback_cluster",
        "global_tag_dimension",
        "global_tag_option",
        "inference_event",
        "ingestion_job",
        "label_preset",
        "label_preset_item",
        "label_preset_label",
        "locateanything_preannotate_job",
        "media_asset",
        "media_upload_session",
        "model_artifact",
        "model_series",
        "model_variant",
        "model_quality_evidence",
        "model_version",
        "prediction_set",
        "prediction_set_projection",
        "prediction_set_review_audit",
        "provider_job",
        "provider_registry",
        "published_model_catalog",
        "published_model_identifier_alias",
        "quality_policy_approval",
        "rule_binding",
        "rule_binding_audit",
        "rule_template",
        "rule_version",
        "rule_version_audit",
        "runtime_publish_template",
        "sample_annotation_label_index",
        "sample_annotation_scope",
        "sample_asset_fingerprint",
        "sample_bounds_check_job",
        "sample_duplicate_group",
        "sample_edit_log",
        "sample_human_review",
        "scheduler_lease",
        "strategy_platform_callback_receipt",
        "strategy_task_integration",
        "system_config_group",
        "system_config_history",
        "system_config_item",
        "system_config_value",
        "system_data_dictionary",
        "tagtool_classification_model",
        "tagtool_preannotate_job",
        "task",
        "task_annotation_contract",
        "task_annotation_label",
        "task_attachment",
        "task_collaborator",
        "task_pre_annotation",
        "task_sample",
        "task_sample_updater",
        "taxonomy_audit_log",
        "third_party_call_audit",
        "third_party_request_receipt",
        "training_data_grant",
        "training_data_withdrawal",
        "training_evaluation_dataset",
        "training_evaluation_sample",
        "training_recipe",
        "training_task",
        "user",
        "workflow_event",
        "workflow_instance",
        "workflow_outbox",
        "workflow_step",
        "yolo_bm1688_export",
        "yolo_board_validation",
        "yolo_consistent_pool_registry",
        "yolo_edge_validation_job",
        "yolo_eval_report",
        "yolo_imported_model",
        "yolo_preannotate_job",
        "yolo_train_job",
    }
)


def phase_one_table_names(metadata: MetaData) -> frozenset[str]:
    """返回一期允许建表的名称集合。

    只返回显式白名单与当前已注册ORM模型的交集。白名单中尚未注册的表
    不会导致异常；未知的新ORM表也不会因为模块导入而自动进入空库。
    """

    return frozenset(
        name
        for name in metadata.tables
        if name in PHASE_ONE_TABLE_ALLOWLIST
    )


def phase_one_tables(metadata: MetaData) -> tuple[Table, ...]:
    """按稳定名称顺序返回一期 ``create_all`` 使用的表对象。"""

    names = phase_one_table_names(metadata)
    return tuple(metadata.tables[name] for name in sorted(names))


def phase_one_foreign_key_violations(
    metadata: MetaData,
    table_names: Iterable[str] | None = None,
) -> dict[str, tuple[str, ...]]:
    """检查一期表是否引用了已退休/未纳入范围的表。

    返回值按表名分组，便于迁移审计和测试输出；不在这里静默删除外键。
    一旦一期模型完成去项目化，结果必须为空。应用启动和迁移引导可在严格
    模式下调用 ``assert_phase_one_foreign_key_closure`` 立即失败。
    """

    allowed = frozenset(table_names or phase_one_table_names(metadata))
    violations: dict[str, tuple[str, ...]] = {}
    for table in metadata.tables.values():
        if table.name not in allowed:
            continue
        targets = sorted(
            {
                fk.target_fullname.split(".")[0]
                for column in table.columns
                for fk in column.foreign_keys
                if fk.target_fullname.split(".")[0] not in allowed
            }
        )
        if targets:
            violations[table.name] = tuple(targets)
    return violations


def assert_phase_one_foreign_key_closure(metadata: MetaData) -> None:
    """在严格入口校验一期表外键闭包。"""

    violations = phase_one_foreign_key_violations(metadata)
    if violations:
        details = "; ".join(
            f"{table}->{','.join(targets)}"
            for table, targets in sorted(violations.items())
        )
        raise RuntimeError(f"一期建表范围存在悬空外键：{details}")


def create_phase_one_tables(bind: Any, metadata: MetaData) -> None:
    """只创建一期表。

    闭包检查在模型去项目化完成后由迁移验收开启；迁移过渡期间先保留
    诊断信息，避免对已有生产兼容字段做静默删除或重写。
    """

    metadata.create_all(bind=bind, tables=phase_one_tables(metadata))
