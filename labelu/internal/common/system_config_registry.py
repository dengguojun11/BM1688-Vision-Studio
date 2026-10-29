from __future__ import annotations

from typing import Any

GLOBAL_SCOPE_KEY = "__global__"
DEFAULT_ENV_NAME = "default"


SYSTEM_CONFIG_REGISTRY: dict[str, dict[str, Any]] = {
    "auto_training": {
        "group_name_zh": "自动迭代训练技术开关",
        "group_comment": "自动训练各阶段独立开关；默认关闭，不能绕过业务门禁",
        "scope_type": "global",
        "sort_order": 5,
        "items": [
            {
                "item_key": key,
                "item_name_zh": name,
                "item_comment": comment,
                "value_type": "boolean",
                "default_value": False,
                "source_mode": "db_primary",
                "sort_order": index * 10,
            }
            for index, (key, name, comment) in enumerate(
                (
                    ("schema_ready", "数据结构就绪", "自动训练表结构和迁移已验收"),
                    ("observe_only", "仅观察模式", "只计算和展示，不执行下游任务"),
                    ("outbox_enabled", "Outbox消费", "允许消费自动训练异步事件"),
                    ("pool_enabled", "训练池更新", "允许更新自动训练样本池"),
                    ("scheduler_enabled", "轮次调度", "允许自动创建冻结轮次"),
                    ("train_enabled", "自动训练", "允许派发自动训练作业"),
                    (
                        "worker_command_enabled",
                        "标准 Worker 命令",
                        "允许非 YOLO 适配器通过 training.worker.command.v1 派发",
                    ),
                    ("bm1688_enabled", "自动BM1688", "允许质量达标后自动编译"),
                    (
                        "edge_validation_enabled",
                        "自动边端验证",
                        "允许编译通过后自动验证",
                    ),
                ),
                start=1,
            )
        ],
    },
    "ai_labeling_runtime": {
        "group_name_zh": "AI标注运行时配置",
        "group_comment": "AI标注运行时提示词、启用供应商和大模型档案配置",
        "scope_type": "global",
        "sort_order": 10,
        "items": [
            {
                "item_key": "instruction_suffix",
                "item_name_zh": "附加提示词",
                "item_comment": "AI标注时追加到系统提示词末尾的补充说明",
                "value_type": "string",
                "default_value": "",
                "source_mode": "db_primary",
                "sort_order": 10,
            },
            {
                "item_key": "enabled_providers",
                "item_name_zh": "启用供应商列表",
                "item_comment": "允许在页面上启用的 AI 供应商标识列表",
                "value_type": "json",
                "default_value": ["volcengine"],
                "source_mode": "db_primary",
                "sort_order": 20,
            },
            {
                "item_key": "llm_profiles",
                "item_name_zh": "大模型档案列表",
                "item_comment": "大模型接入档案；敏感密钥仅存环境变量名和掩码提示",
                "value_type": "json",
                "default_value": [],
                "source_mode": "db_overlay",
                "is_secret": True,
                "scope_desc": "全局",
                "sort_order": 30,
            },
            {
                "item_key": "annotation_chain_profile_ids",
                "item_name_zh": "标注链路档案顺序",
                "item_comment": "执行 AI 标注链路时使用的大模型档案 ID 顺序",
                "value_type": "json",
                "default_value": [],
                "source_mode": "db_primary",
                "sort_order": 40,
            },
        ],
    },
    "model_access": {
        "group_name_zh": "模型接入配置",
        "group_comment": "统一维护视觉模型服务地址、模型标识和调用参数；YOLO 检测框与 JSON/JSONL 文件导入不使用此配置",
        "scope_type": "global",
        "sort_order": 12,
        "items": [
            {
                "item_key": "connections_store",
                "item_name_zh": "模型服务列表",
                "item_comment": "已启用且连通性检查通过的配置才会进入预标注运行时",
                "value_type": "json",
                "default_value": {"version": 1, "connections": []},
                "source_mode": "db_primary",
                "is_secret": True,
                "sort_order": 10,
            }
        ],
    },
    "yolo_eval_prompts": {
        "group_name_zh": "YOLO评估提示词配置",
        "group_comment": "YOLO 训练评估和下一轮迭代建议使用的提示词模板",
        "scope_type": "global",
        "sort_order": 20,
        "items": [
            {
                "item_key": "full_analysis",
                "item_name_zh": "完整评估提示词",
                "item_comment": "用于生成完整训练分析报告的提示词模板",
                "value_type": "text",
                "default_value": "",
                "source_mode": "db_primary",
                "sort_order": 10,
            },
            {
                "item_key": "next_iteration",
                "item_name_zh": "下一轮迭代提示词",
                "item_comment": "用于生成下一轮训练建议的提示词模板",
                "value_type": "text",
                "default_value": "",
                "source_mode": "db_primary",
                "sort_order": 20,
            },
        ],
    },
    "edge_device_registry": {
        "group_name_zh": "边端设备注册表",
        "group_comment": "边端 SSH 多设备仓库与当前启用设备配置",
        "scope_type": "global",
        "sort_order": 35,
        "items": [
            {
                "item_key": "device_store",
                "item_name_zh": "边端设备仓库对象",
                "item_comment": "边端设备列表与当前启用设备；密码使用 Fernet 加密后存入 MySQL",
                "value_type": "json",
                "default_value": {"version": 1, "active_device_id": "", "devices": []},
                "source_mode": "db_primary",
                "is_secret": True,
                "sort_order": 10,
            }
        ],
    },
    "edge_agent_control_plane": {
        "group_name_zh": "边端 Agent 控制面",
        "group_comment": "页面配置 SSH 后自动启用的边端 Agent 控制面；Token 仅保存 Fernet 密文",
        "scope_type": "global",
        "sort_order": 36,
        "items": [
            {
                "item_key": "config",
                "item_name_zh": "控制面配置",
                "item_comment": "开关、回连地址和加密 Token；环境变量显式配置优先于此处",
                "value_type": "json",
                "default_value": {
                    "version": 1,
                    "enabled": False,
                    "primary_enabled": False,
                    "auto_bootstrap_enabled": True,
                    "public_base_url": "",
                    "token_encrypted": "",
                    "task_token_secret_encrypted": "",
                },
                "source_mode": "db_overlay",
                "is_secret": True,
                "scope_desc": "全局",
                "sort_order": 10,
            }
        ],
    },
    "remote_train_nodes": {
        "group_name_zh": "远程训练节点配置",
        "group_comment": "远程 YOLO 训练节点列表及其连接参数",
        "scope_type": "global",
        "sort_order": 40,
        "items": [
            {
                "item_key": "nodes",
                "item_name_zh": "远程训练节点列表",
                "item_comment": "远程训练节点清单；节点密码和私钥路径仅存环境变量名和掩码提示",
                "value_type": "json",
                "default_value": [],
                "source_mode": "db_overlay",
                "is_secret": True,
                "sort_order": 10,
            }
        ],
    },
    "bm1688_compiler_config": {
        "group_name_zh": "BM1688编译配置",
        "group_comment": "BM1688 TPU-MLIR 编译和量化相关配置",
        "scope_type": "global",
        "sort_order": 50,
        "items": [
            {
                "item_key": "config",
                "item_name_zh": "BM1688编译配置对象",
                "item_comment": "BM1688 编译页配置项整体对象",
                "value_type": "json",
                "default_value": {},
                "source_mode": "db_primary",
                "sort_order": 10,
            }
        ],
    },
    "yolo_quality_policy_store": {
        "group_name_zh": "YOLO质量策略配置",
        "group_comment": "YOLO 训练质量策略和激活映射配置",
        "scope_type": "global",
        "sort_order": 60,
        "items": [
            {
                "item_key": "store",
                "item_name_zh": "质量策略仓对象",
                "item_comment": "质量策略仓整体配置对象",
                "value_type": "json",
                "default_value": {},
                "source_mode": "db_primary",
                "sort_order": 10,
            }
        ],
    },
    "aicm_publish_config": {
        "group_name_zh": "AICM发布配置",
        "group_comment": "AICM 模型发布多环境配置和当前激活环境配置",
        "scope_type": "global",
        "sort_order": 70,
        "items": [
            {
                "item_key": "environments_store",
                "item_name_zh": "发布环境仓对象",
                "item_comment": "AICM 发布环境列表和当前激活环境；密码使用 Fernet 加密后存入 MySQL",
                "value_type": "json",
                "default_value": {
                    "version": 1,
                    "active_environment_id": "",
                    "environments": [],
                },
                "source_mode": "db_primary",
                "is_secret": True,
                "sort_order": 10,
            }
        ],
    },
    "strategy_integration_s3": {
        "group_name_zh": "专家回流 S3 地址配置",
        "group_comment": "专家回流数据集导入和策略任务联动使用的 S3/MinIO 服务地址列表",
        "scope_type": "global",
        "sort_order": 75,
        "items": [
            {
                "item_key": "connections_store",
                "item_name_zh": "S3 服务地址列表",
                "item_comment": "仅维护服务名称、完整地址和备注；当前启用的地址用于专家回流任务",
                "value_type": "json",
                "default_value": {
                    "version": 1,
                    "active_connection_id": "",
                    "connections": [],
                },
                "source_mode": "db_overlay",
                "sort_order": 10,
            }
        ],
    },
}


def list_group_keys() -> list[str]:
    return list(SYSTEM_CONFIG_REGISTRY.keys())


def get_group_definition(group_key: str) -> dict[str, Any]:
    return SYSTEM_CONFIG_REGISTRY[group_key]
