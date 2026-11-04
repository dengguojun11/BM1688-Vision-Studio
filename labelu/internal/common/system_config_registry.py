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
    "auto_iteration": {
        "group_name_zh": "自动迭代训练策略",
        "group_comment": (
            "训练未达标时按 AI 报告自动续训的判定口径与停止条件；"
            "管理员可在训练页可视化调整，保存后对下一轮判定立即生效"
        ),
        "scope_type": "global",
        "sort_order": 6,
        "items": [
            {
                "item_key": "enabled",
                "item_name_zh": "自动迭代开关",
                "item_comment": "关闭后只做单轮训练与报告，不自动续训",
                "value_type": "boolean",
                "default_value": False,
                "source_mode": "db_primary",
                "env_var_name": "YOLO_AUTO_ITERATION_ENABLED",
                "sort_order": 10,
            },
            {
                "item_key": "metric_basis",
                "item_name_zh": "判定指标口径",
                "item_comment": "val_recall=验证集召回率；overall_recall=自动评估加权召回；map50_95=沿用 mAP 口径",
                "value_type": "string",
                "default_value": "map50_95",
                "source_mode": "db_primary",
                "enum_options": ["val_recall", "overall_recall", "map50_95"],
                "sort_order": 20,
            },
            {
                "item_key": "pass_threshold",
                "item_name_zh": "达标阈值",
                "item_comment": "达到该阈值即判定通过：停止迭代并进入编译",
                "value_type": "number",
                "default_value": 0.5,
                "source_mode": "db_primary",
                "validation_rule": {"min": 0.3, "max": 0.99, "step": 0.01},
                "sort_order": 30,
            },
            {
                "item_key": "warn_threshold",
                "item_name_zh": "预警阈值",
                "item_comment": "仅用于报告与页面提示，不阻断流程",
                "value_type": "number",
                "default_value": 0.45,
                "source_mode": "db_primary",
                "validation_rule": {"min": 0.0, "max": 0.99, "step": 0.01},
                "sort_order": 40,
            },
            {
                "item_key": "max_rounds",
                "item_name_zh": "最大迭代次数",
                "item_comment": "同一迭代链训练总次数（含首轮）：4 = 首轮 + 3 次迭代",
                "value_type": "number",
                "default_value": 4,
                "source_mode": "db_primary",
                "env_var_name": "YOLO_AUTO_ITERATION_MAX_ROUNDS",
                "validation_rule": {"min": 1, "max": 20, "step": 1},
                "sort_order": 50,
            },
            {
                "item_key": "no_improvement_rounds",
                "item_name_zh": "无提升停止轮数",
                "item_comment": "连续 N 轮提升小于最小幅度即自动停止；0=关闭",
                "value_type": "number",
                "default_value": 2,
                "source_mode": "db_primary",
                "validation_rule": {"min": 0, "max": 10, "step": 1},
                "sort_order": 60,
            },
            {
                "item_key": "min_improvement_delta",
                "item_name_zh": "最小提升幅度",
                "item_comment": "低于该幅度视为训练噪声，不计为提升",
                "value_type": "number",
                "default_value": 0.02,
                "source_mode": "db_primary",
                "validation_rule": {"min": 0.0, "max": 0.5, "step": 0.005},
                "sort_order": 70,
            },
            {
                "item_key": "allow_conditional_pass",
                "item_name_zh": "条件达标算通过",
                "item_comment": "关闭后「条件达标」（样本偏少或指标处于预警区）也继续迭代",
                "value_type": "boolean",
                "default_value": True,
                "source_mode": "db_primary",
                "sort_order": 80,
            },
            {
                "item_key": "require_quality_for_compile",
                "item_name_zh": "达标才编译",
                "item_comment": "开启后只有判定达标的模型才自动创建 BM1688 编译",
                "value_type": "boolean",
                "default_value": True,
                "source_mode": "db_primary",
                "sort_order": 90,
            },
            {
                "item_key": "min_class_recall_enabled",
                "item_name_zh": "启用单类最低召回",
                "item_comment": "守护项：防止整体达标但某关键类召回为 0",
                "value_type": "boolean",
                "default_value": False,
                "source_mode": "db_primary",
                "sort_order": 100,
            },
            {
                "item_key": "min_class_recall",
                "item_name_zh": "单类最低召回",
                "item_comment": "任一类召回低于该值即判定不达标（需训练指标包含各类别指标）",
                "value_type": "number",
                "default_value": 0.3,
                "source_mode": "db_primary",
                "validation_rule": {"min": 0.0, "max": 0.99, "step": 0.01},
                "sort_order": 110,
            },
            {
                "item_key": "precision_floor_enabled",
                "item_name_zh": "启用 precision 下限",
                "item_comment": "守护项：防止靠多出框刷召回导致误检崩塌",
                "value_type": "boolean",
                "default_value": False,
                "source_mode": "db_primary",
                "sort_order": 120,
            },
            {
                "item_key": "precision_floor",
                "item_name_zh": "precision 下限",
                "item_comment": "验证集 precision 低于该值即判定不达标",
                "value_type": "number",
                "default_value": 0.7,
                "source_mode": "db_primary",
                "validation_rule": {"min": 0.0, "max": 0.99, "step": 0.01},
                "sort_order": 130,
            },
            {
                "item_key": "max_regression",
                "item_name_zh": "相对上一轮回退上限",
                "item_comment": "守护项：相对上一轮下降超过该值即判定不达标并提示",
                "value_type": "number",
                "default_value": 0.05,
                "source_mode": "db_primary",
                "validation_rule": {"min": 0.0, "max": 0.5, "step": 0.01},
                "sort_order": 140,
            },
            {
                "item_key": "eval_conf",
                "item_name_zh": "评估置信度工作点",
                "item_comment": "记录评测工作点供报告说明；当前判定指标仍来自训练/评估结果，不改变计算",
                "value_type": "number",
                "default_value": 0.25,
                "source_mode": "db_primary",
                "validation_rule": {"min": 0.01, "max": 0.99, "step": 0.01},
                "sort_order": 150,
            },
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
    "image_cleaning_policy": {
        "group_name_zh": "图片清洗配置",
        "group_comment": "任务编辑页直接上传图片时使用的视觉相似度清洗规则",
        "scope_type": "global",
        "sort_order": 80,
        "items": [
            {
                "item_key": "policy",
                "item_name_zh": "图片清洗策略",
                "item_comment": "简单、标准、严格三档相似度阈值及当前生效规则",
                "value_type": "json",
                "default_value": {
                    "active_preset": "standard",
                    "presets": {
                        "simple": {"threshold": 0.98},
                        "standard": {"threshold": 0.95},
                        "strict": {"threshold": 0.90},
                    },
                    "revision": 1,
                    "algorithm_version": "visual-v1",
                },
                "source_mode": "db_primary",
                "sort_order": 10,
            }
        ],
    },
    "training_admission": {
        "group_name_zh": "训练准入配置",
        "group_comment": "任务进入训练候选前要求的最低合格标注样本数",
        "scope_type": "global",
        "sort_order": 85,
        "items": [
            {
                "item_key": "config",
                "item_name_zh": "训练准入规则",
                "item_comment": "min_sample_count：每个任务至少需要的合格标注图片数",
                "value_type": "json",
                "default_value": {
                    "version": 1,
                    "min_sample_count": 50,
                },
                "source_mode": "db_primary",
                "sort_order": 10,
            }
        ],
    },
    "edge_validation_settings": {
        "group_name_zh": "边端验证设置",
        "group_comment": "边端验证取图和最低验证样本数配置",
        "scope_type": "global",
        "sort_order": 86,
        "items": [
            {
                "item_key": "config",
                "item_name_zh": "边端验证图片配置",
                "item_comment": "min_images：最低验证图片数；max_images：单次最多图片数",
                "value_type": "json",
                "default_value": {
                    "version": 1,
                    "min_images": 200,
                    "max_images": 500,
                },
                "source_mode": "db_primary",
                "sort_order": 10,
            }
        ],
    },
}


def list_group_keys() -> list[str]:
    return list(SYSTEM_CONFIG_REGISTRY.keys())


def get_group_definition(group_key: str) -> dict[str, Any]:
    return SYSTEM_CONFIG_REGISTRY[group_key]
