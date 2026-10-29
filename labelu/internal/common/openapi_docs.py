from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

API_DOCS_ASSET_DIR = Path(__file__).resolve().parent / "static" / "api_docs"

FLEXIBLE_WORKFLOW_TAG = (
    "柔性工作流",
    "专家回流任务、模型阈值、模型优化和第三方回调闭环。",
)

CHINESE_TAGS: dict[str, tuple[str, str]] = {
    "users": ("用户与认证", "用户注册、登录、身份信息和账号管理。"),
    "tasks": ("任务管理", "标注任务的创建、配置、查询、更新和协作管理。"),
    "attachments": ("文件与附件", "任务附件的上传、下载、查询和删除。"),
    "samples": ("样本与标注", "任务样本、人工标注、检索、统计和导出。"),
    "pre_annotations": ("预标注", "样本预标注数据的导入、查询和管理。"),
    "datasources": ("数据源", "数据源配置、文件发现和数据接入。"),
    "annotation-taxonomy": ("标签体系", "系统标签、标签预设、数据集分类和标注契约。"),
    "yolo_train": ("模型训练与评估", "YOLO 训练、评估、导出、边端验证和质量策略。"),
    "training": ("统一训练", "按算法契约、冻结数据集和标准 Worker 协议创建与查询训练作业。"),
    "yolo-eval": ("评估提示词", "模型评估提示词模板和相关配置。"),
    "ai-labeling": ("AI 标注配置", "AI 辅助标注服务、模型和参数配置。"),
    "模型接入配置": ("模型接入配置", "可配置的远程视觉、检测和全局标签模型服务。"),
    "board_agent": ("板端代理", "板端代理任务下发、状态和结果回传。"),
    "edge_agents": ("边端 Agent", "7GAI 边端 Agent 注册、心跳、能力上报和 OTA 生命周期。"),
    "edge_device_jobs": ("边缘设备任务", "边缘设备作业创建、执行进度和结果管理。"),
    "tagtool_preannotate": ("分类预标注", "TagTool 分类模型和任务预标注。"),
    "event_review": ("事件复核", "外部事件数据接入、复核和结果回传。"),
    "experiment_dataset_import": ("实验数据集导入", "实验数据集、样本和标注结果导入。"),
    "strategy_task_integration": FLEXIBLE_WORKFLOW_TAG,
    "strategy_tasks": (
        "进化中心",
        "统一策略任务、专家回流、阈值实验和模型能力优化接口。",
    ),
    "evolution": (
        "进化中心",
        "第三方回流数据包和累计导入状态查询。",
    ),
    "异常任务与第三方调用": (
        "异常任务与第三方调用",
        "查询和重试异常任务及第三方调用记录。",
    ),
    "strategy_integration_s3_config": (
        "策略 S3 配置",
        "策略系统 S3 对象存储连接配置。",
    ),
    "strategy_evolution": FLEXIBLE_WORKFLOW_TAG,
    "local_dev": ("本地开发", "仅用于本地开发和联调的辅助接口。"),
    "auto_training": ("自动训练", "自动训练计划、轮次、评审、评估和质量审批。"),
    "third_party_calls": FLEXIBLE_WORKFLOW_TAG,
    "published_models": (
        "模型目录管理",
        "已发布模型、兼容标识、下载开关和模型包管理。",
    ),
    "published_models_public": ("公开模型服务", "供外部项目查询和下载已开放模型。"),
    "projects": ("项目与权限", "项目边界、成员和项目级访问权限管理。"),
    "organizations": ("组织管理", "组织层级、成员和组织级访问边界管理。"),
    "scenes": ("场景管理", "业务场景定义、配置和项目场景关联管理。"),
    "datasets": ("数据中心", "数据集及数据集版本管理。"),
    "algorithm_groups": ("算法分组", "算法能力分组和筛选。"),
    "algorithms": ("算法注册", "算法适配器、能力契约和芯片支持。"),
    "algorithm-registry": ("算法注册", "算法定义、能力分组、运行契约和芯片支持。"),
    "annotation_review": ("标注审核", "样本审核队列、批量审核、驳回原因和仲裁记录。"),
    "active_learning": ("主动学习", "难例聚类、候选批次、审核接收和训练数据回流。"),
    "data-governance": ("数据治理", "媒体扫描、隔离、生命周期和治理状态管理。"),
    "providers": ("Provider目录", "预标注 Provider 的版本、能力和输入输出契约。"),
    "provider_jobs": ("Provider作业", "Provider 执行作业、预测集合、审核和正式投影。"),
    "ingestion": ("数据接入", "数据导入作业、媒体资产、去重和接入状态管理。"),
    "project_ownership": ("项目归属审计", "历史资源归属分析、隔离和确定性回填证据。"),
    "rule-center": ("规则中心", "规则模板、不可变版本、设备绑定和验签结果管理。"),
    "model_versions": ("模型仓库", "模型版本、产物、质量证据和发布门禁。"),
    "deployment_releases": ("部署中心", "边端发布、设备目标、期望状态和运行观测。"),
    "workspace": ("工作台", "项目、数据集、模型、部署和训练运行摘要。"),
    "工作台": ("工作台", "任务、训练、模型、回流和系统运行摘要。"),
    "edge_feedback": ("进化中心", "边端推理事件、难例回流和审核候选。"),
    # Keep the legacy strategy/callback module grouping stable for existing
    # consumers; the new canonical workflow API gets its own explicit tag.
    "workflows": ("工作流编排", "专家回流、阈值实验、模型优化和第三方回调的统一状态机。"),
    "__system__": ("系统页面与资源", "系统扩展页面、静态资源和基础入口。"),
}

TOKEN_TRANSLATIONS = {
    "about": "系统信息",
    "access": "访问",
    "account": "账号",
    "action": "操作",
    "active": "当前",
    "agent": "代理",
    "ai": "AI",
    "alias": "别名",
    "aliases": "别名",
    "algorithm": "算法",
    "annotation": "标注",
    "annotations": "标注",
    "approval": "审批",
    "approvals": "审批",
    "asset": "素材",
    "assets": "素材",
    "attachment": "附件",
    "attachments": "附件",
    "attribute": "属性",
    "attributes": "属性",
    "auto": "自动",
    "available": "可用",
    "batch": "批量",
    "bound": "边界",
    "bounds": "边界",
    "board": "板端",
    "bm1688": "BM1688",
    "callback": "回调",
    "catalog": "目录",
    "category": "分类",
    "categories": "分类",
    "class": "类别",
    "classes": "类别",
    "client": "客户端",
    "collaborator": "协作者",
    "collaborators": "协作者",
    "code": "编码",
    "color": "颜色",
    "compare": "对比",
    "content": "内容",
    "config": "配置",
    "configuration": "配置",
    "consistent": "一致性",
    "contract": "契约",
    "count": "数量",
    "created": "创建",
    "dashboard": "仪表盘",
    "current": "当前",
    "data": "数据",
    "dataset": "数据集",
    "datasets": "数据集",
    "default": "默认",
    "detail": "详情",
    "display": "展示",
    "device": "设备",
    "devices": "设备",
    "download": "下载",
    "edge": "边缘",
    "eligible": "可训练",
    "enabled": "启用状态",
    "engine": "引擎",
    "environment": "环境",
    "eval": "评估",
    "evaluation": "评估",
    "event": "事件",
    "events": "事件",
    "evidence": "证据",
    "evolution": "进化",
    "experiment": "实验",
    "export": "导出",
    "exports": "导出",
    "external": "外部",
    "file": "文件",
    "files": "文件",
    "filter": "筛选",
    "global": "全局",
    "history": "历史",
    "image": "图片",
    "images": "图片",
    "import": "导入",
    "imported": "已导入",
    "info": "信息",
    "integration": "集成",
    "internal": "内部",
    "item": "条目",
    "items": "条目",
    "job": "作业",
    "jobs": "作业",
    "label": "标签",
    "labels": "标签",
    "latest": "最新",
    "local": "本地",
    "log": "日志",
    "logs": "日志",
    "map": "映射",
    "media": "媒体",
    "me": "当前用户信息",
    "meta": "元数据",
    "metadata": "元数据",
    "metric": "指标",
    "metrics": "指标",
    "model": "模型",
    "models": "模型",
    "name": "名称",
    "option": "选项",
    "options": "选项",
    "package": "模型包",
    "page": "页面",
    "policy": "策略",
    "policies": "策略",
    "pool": "数据池",
    "pre": "预",
    "preannotation": "预标注",
    "preannotations": "预标注",
    "preset": "预设",
    "presets": "预设",
    "profile": "配置档案",
    "progress": "进度",
    "prompt": "提示词",
    "prompts": "提示词",
    "public": "公开",
    "quality": "质量",
    "queue": "队列",
    "ready": "就绪",
    "record": "记录",
    "records": "记录",
    "report": "报告",
    "reports": "报告",
    "request": "请求",
    "requests": "请求",
    "result": "结果",
    "results": "结果",
    "review": "评审",
    "reviews": "评审",
    "role": "角色",
    "round": "轮次",
    "rounds": "轮次",
    "rule": "规则",
    "rules": "规则",
    "runtime": "运行时",
    "s3": "S3",
    "sample": "样本",
    "samples": "样本",
    "scope": "范围",
    "search": "检索",
    "session": "会话",
    "setting": "设置",
    "settings": "设置",
    "source": "来源",
    "stat": "统计",
    "stats": "统计",
    "status": "状态",
    "strategy": "策略",
    "summary": "摘要",
    "sync": "同步",
    "system": "系统",
    "tag": "标签",
    "tags": "标签",
    "task": "任务",
    "tasks": "任务",
    "taxonomy": "体系",
    "template": "模板",
    "third": "第三方",
    "threshold": "阈值",
    "token": "令牌",
    "train": "训练",
    "trained": "已训练",
    "training": "训练",
    "type": "类型",
    "upload": "上传",
    "url": "地址",
    "user": "用户",
    "users": "用户",
    "validate": "验证",
    "validation": "验证",
    "value": "值",
    "version": "版本",
    "video": "视频",
    "workflow": "流程",
    "yolo": "YOLO",
    "analysis": "分析",
    "assign": "分配",
    "by": "按",
    "check": "检查",
    "contracts": "契约",
    "copy": "复制",
    "from": "来源",
    "rect": "矩形框",
    "activate": "启用",
    "admin": "管理员",
    "all": "全部",
    "audit": "审计",
    "audits": "审计",
    "baseline": "基线",
    "bmodel": "BModel",
    "calls": "调用",
    "candidates": "候选项",
    "chain": "链路",
    "chains": "链路",
    "checks": "检查",
    "classification": "分类",
    "claim": "领取",
    "compiler": "编译器",
    "connection": "连接",
    "connections": "连接",
    "datasource": "数据源",
    "datasources": "数据源",
    "dev": "开发",
    "dimension": "维度",
    "dimensions": "维度",
    "disable": "停用",
    "environments": "环境",
    "extension": "扩展",
    "field": "字段",
    "gt": "标注真值",
    "hub": "中心",
    "hyperparam": "超参数",
    "hyperparams": "超参数",
    "id": "ID",
    "imports": "导入",
    "instruction": "指令",
    "iteration": "迭代",
    "labeling": "标注",
    "labelu": "LabelU",
    "llm": "大语言模型",
    "locateanything": "LocateAnything",
    "next": "下一步",
    "node": "节点",
    "nodes": "节点",
    "objects": "对象",
    "onnx": "ONNX",
    "perform": "执行",
    "party": "第三方",
    "patch": "局部更新",
    "payload": "请求数据",
    "plan": "计划",
    "plans": "计划",
    "platform": "平台",
    "pools": "数据池",
    "preannotate": "预标注",
    "pred": "预测",
    "profiles": "配置档案",
    "proxy": "代理",
    "publishable": "可发布",
    "published": "已发布",
    "recipe": "配方",
    "recipes": "配方",
    "reconciliation": "对账",
    "registry": "注册表",
    "remote": "远程",
    "scan": "扫描",
    "scopes": "范围",
    "sop": "SOP",
    "ssh": "SSH",
    "stage": "阶段",
    "tagtool": "TagTool",
    "templates": "模板",
    "test": "测试",
    "thresholds": "阈值",
    "to": "到",
    "val": "验证集",
    "versions": "版本",
    "visual": "可视化",
    "weights": "权重",
    "aicm": "AICM",
}

EXACT_FIELD_DESCRIPTIONS = {
    "id": "记录唯一 ID。",
    "task_id": "标注任务 ID。",
    "sample_id": "样本 ID。",
    "attachment_id": "附件 ID。",
    "user_id": "用户 ID。",
    "model_id": "模型 ID。",
    "export_id": "模型导出记录 ID。",
    "job_id": "作业 ID。",
    "created_at": "创建时间。",
    "updated_at": "最后更新时间。",
    "deleted_at": "软删除时间；为空表示未删除。",
    "status": "当前业务状态。",
    "name": "名称。",
    "description": "说明。",
    "username": "用户登录邮箱或账号。",
    "password": "用户密码。",
    "token": "访问令牌。",
    "page": "页码；具体接口会说明是否从 0 开始。",
    "size": "每页记录数。",
    "total": "符合条件的记录总数。",
    "data": "接口返回的业务数据。",
    "message": "接口提示信息。",
    "code": "业务响应编码。",
}

ACTION_TRANSLATIONS = {
    "activate": "启用",
    "add": "新增",
    "apply": "应用",
    "approve": "审批",
    "archive": "归档",
    "assign": "分配",
    "cancel": "取消",
    "check": "检查",
    "clear": "清理",
    "clone": "复制",
    "compare": "对比",
    "complete": "完成",
    "copy": "复制",
    "create": "创建",
    "delete": "删除",
    "download": "下载",
    "disable": "停用",
    "evaluate": "评估",
    "export": "导出",
    "generate": "生成",
    "get": "获取",
    "import": "导入",
    "invalidate": "失效",
    "iterate": "迭代",
    "list": "查询",
    "login": "登录",
    "logout": "退出登录",
    "pause": "暂停",
    "patch": "更新",
    "perform": "执行",
    "post": "执行",
    "preview": "预览",
    "probe": "探测",
    "publish": "发布",
    "put": "保存",
    "promote": "晋级",
    "recommend": "推荐",
    "request": "发起",
    "receive": "接收",
    "recompile": "重新编译",
    "reconcile": "对账",
    "refine": "优化",
    "repair": "修复",
    "replace": "替换",
    "refresh": "刷新",
    "remove": "删除",
    "reset": "重置",
    "restart": "重启",
    "resolve": "解析",
    "resume": "恢复",
    "retry": "重试",
    "run": "执行",
    "save": "保存",
    "search": "检索",
    "set": "设置",
    "signup": "注册",
    "start": "启动",
    "stop": "停止",
    "submit": "提交",
    "sync": "同步",
    "test": "测试",
    "trigger": "触发",
    "update": "更新",
    "upload": "上传",
    "upsert": "新增或更新",
    "validate": "验证",
    "verify": "校验",
}

METHOD_ACTIONS = {
    "get": "获取",
    "post": "执行",
    "put": "完整更新",
    "patch": "更新",
    "delete": "删除",
}

RESPONSE_DESCRIPTIONS = {
    "200": "请求成功。",
    "201": "创建成功。",
    "202": "请求已接收，正在异步处理。",
    "204": "请求成功，无响应正文。",
    "400": "请求参数或业务条件不合法。",
    "401": "未提供有效的 Bearer 访问令牌。",
    "403": "当前用户没有执行该操作的权限。",
    "404": "请求的资源不存在。",
    "409": "当前资源状态与请求操作冲突。",
    "422": "请求参数校验失败。",
    "500": "服务器内部错误。",
}

DOCS_DESCRIPTION = """
# 7GAI 中文 API 接口文档

本平台统一使用 7GAI Evolution Platform 品牌，并保留历史兼容 API 路径和客户端能力。

本文档由当前运行版本的 FastAPI 路由自动生成，覆盖所有已注册到 OpenAPI 的 HTTP 接口，并按业务模块分组。

## 在线调用

1. 调用“用户与认证”模块的登录接口取得访问令牌。
2. 点击页面右上角“Authorize/授权”，只填写登录返回的令牌原文；Swagger 会自动添加 `Bearer` 前缀。
3. 展开目标接口，点击“Try it out/试一试”后填写参数并执行。

## 通用约定

- 业务接口默认前缀为 `/api/v1`；少数第三方回调和内部接入接口使用独立根路径。
- 普通 JSON 响应通常使用 `code`、`message`、`data`，分页响应另含 `meta_data`。
- 时间字段采用 ISO 8601 格式；文件下载接口直接返回二进制响应。
- 每个接口仍会执行真实的身份认证、权限校验和业务校验，文档页面不会绕过安全控制。

## WebSocket

实时标注协作地址为 `/ws/task/{task_id}/{sample_id}`。WebSocket 不属于 OpenAPI 可执行操作，连接时仍需携带有效身份信息。
"""


def _contains_chinese(value: Any) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(value or "")))


def _identifier_tokens(value: str) -> list[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
    return [item.lower() for item in re.split(r"[^A-Za-z0-9]+", text) if item]


def _translated_token(token: str) -> str:
    return TOKEN_TRANSLATIONS.get(
        token,
        ACTION_TRANSLATIONS.get(token, token.upper() if len(token) <= 3 else token),
    )


def _translated_label(value: str) -> str:
    tokens = _identifier_tokens(value)
    if not tokens:
        return str(value or "字段")
    translated = []
    for token in tokens:
        label = _translated_token(token)
        if not translated or translated[-1] != label:
            translated.append(label)
    return "".join(translated)


def _field_description(name: str, existing: Any = None) -> str:
    if _contains_chinese(existing):
        return str(existing)
    base = EXACT_FIELD_DESCRIPTIONS.get(name) or f"{_translated_label(name)}字段。"
    if existing:
        return f"{base} 原说明：{str(existing).strip()}"
    return base


def _path_resource_label(path: str) -> str:
    segments = [
        segment
        for segment in str(path).split("/")
        if segment
        and segment not in {"api", "v1"}
        and not (segment.startswith("{") and segment.endswith("}"))
    ]
    if not segments:
        return "接口"
    selected = segments[-2:] if len(segments) > 1 else segments
    labels = [_translated_label(segment) for segment in selected]
    return "".join(dict.fromkeys(labels))


def _operation_summary(original: str, method: str, path: str) -> str:
    tokens = _identifier_tokens(original)
    action_token = tokens[0] if tokens else ""
    if tokens == ["auto", "label"]:
        return "自动标注"
    if tokens and tokens[0] in ACTION_TRANSLATIONS:
        action = ACTION_TRANSLATIONS[tokens.pop(0)]
    elif (
        len(tokens) > 1
        and tokens[0] in {"auto", "batch"}
        and tokens[1] in ACTION_TRANSLATIONS
    ):
        prefix = "自动" if tokens.pop(0) == "auto" else "批量"
        action = f"{prefix}{ACTION_TRANSLATIONS[tokens.pop(0)]}"
    elif (
        len(tokens) > 2
        and tokens[:2] == ["board", "agent"]
        and tokens[2] in ACTION_TRANSLATIONS
    ):
        action = ACTION_TRANSLATIONS[tokens.pop(2)]
    else:
        action = METHOD_ACTIONS.get(method.lower(), "调用")
    if tokens == ["me"]:
        return f"{action}当前用户信息"
    if not tokens and action_token in {"signup", "login", "logout"}:
        return {
            "signup": "用户注册",
            "login": "用户登录",
            "logout": "用户退出登录",
        }[action_token]
    if action == "复制" and tokens == ["by", "label"]:
        return "按标签复制样本"
    if tokens == ["all"]:
        tokens = []
    if len(tokens) > 1 and tokens[-1] == "request":
        tokens.pop()
    tokens = [token for token in tokens if token not in {"by", "id"}]
    generic_resource = not tokens
    if generic_resource:
        resource = _path_resource_label(path)
        if action == "获取" and not resource.endswith(("状态", "摘要", "详情")):
            resource += "详情"
    else:
        resource = _translated_label("_".join(tokens))
    if resource.endswith(action) and len(resource) > len(action):
        resource = resource[: -len(action)]
    if not resource:
        resource = "接口"
    suffix = "列表" if action == "查询" and not resource.endswith("列表") else ""
    return f"{action}{resource}{suffix}"


def _operation_description(
    *, summary: str, method: str, path: str, operation: dict[str, Any]
) -> str:
    existing = str(operation.get("description") or "").strip()
    authentication = (
        "需要 Bearer 身份认证。"
        if operation.get("security")
        else "无需 Bearer 身份认证。"
    )
    generated = (
        f"{summary}。\n\n"
        f"- 请求方式：`{method.upper()}`\n"
        f"- 接口路径：`{path}`\n"
        f"- 身份认证：{authentication}"
    )
    if existing and not _contains_chinese(existing):
        return f"{generated}\n\n原始说明：{existing}"
    return existing or generated


def _enrich_parameter(parameter: dict[str, Any]) -> None:
    name = str(parameter.get("name") or "parameter")
    existing = parameter.get("description")
    if not _contains_chinese(existing):
        location = {
            "path": "路径参数",
            "query": "查询参数",
            "header": "请求头",
            "cookie": "Cookie 参数",
        }.get(str(parameter.get("in") or ""), "请求参数")
        parameter["description"] = (
            f"{_field_description(name, existing)} 参数位置：{location}。"
        )
    schema = parameter.get("schema")
    if isinstance(schema, dict) and not _contains_chinese(schema.get("description")):
        schema["description"] = parameter["description"]


def _enrich_schema(schema_name: str, schema: dict[str, Any]) -> None:
    title = _translated_label(str(schema.get("title") or schema_name))
    schema["title"] = title
    if not _contains_chinese(schema.get("description")):
        schema_tokens = set(_identifier_tokens(schema_name))
        if schema_name.startswith("OkResp"):
            schema["description"] = "标准成功响应数据结构。"
        elif schema_tokens & {"command", "input", "payload"}:
            schema["description"] = "接口请求数据结构。"
        elif schema_tokens & {"response", "resp"}:
            schema["description"] = "接口响应数据结构。"
        elif schema_tokens & {"item", "row"}:
            schema["description"] = "列表条目数据结构。"
        elif schema_tokens & {"error"}:
            schema["description"] = "接口错误数据结构。"
        else:
            schema["description"] = "业务数据结构。"
    for property_name, property_schema in schema.get("properties", {}).items():
        if not isinstance(property_schema, dict):
            continue
        property_schema["title"] = _translated_label(
            str(property_schema.get("title") or property_name)
        )
        property_schema["description"] = _field_description(
            property_name, property_schema.get("description")
        )


def enrich_openapi_schema(schema: dict[str, Any]) -> dict[str, Any]:
    info = schema.setdefault("info", {})
    info["title"] = "7GAI 中文 API 接口文档"
    # Keep the legacy product name discoverable for existing integrations.
    info["x-legacy-product"] = "LabelU"
    info["description"] = DOCS_DESCRIPTION
    info["x-document-language"] = "zh-CN"

    used_tag_codes: list[str] = []
    for path, path_item in schema.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in METHOD_ACTIONS or not isinstance(operation, dict):
                continue
            original_tags = operation.get("tags") or ["__system__"]
            translated_tags = []
            for code in original_tags:
                code = str(code)
                if code not in used_tag_codes:
                    used_tag_codes.append(code)
                translated_tags.append(
                    CHINESE_TAGS.get(code, (f"其他接口（{code}）", "其他系统接口。"))[0]
                )
            operation["tags"] = translated_tags
            operation["x-labelu-module-codes"] = original_tags

            original_summary = str(
                operation.get("summary") or operation.get("operationId") or path
            )
            operation["x-labelu-original-summary"] = original_summary
            summary = _operation_summary(original_summary, method, path)
            operation["summary"] = summary
            operation["description"] = _operation_description(
                summary=summary,
                method=method,
                path=path,
                operation=operation,
            )
            for parameter in operation.get("parameters", []):
                if isinstance(parameter, dict):
                    _enrich_parameter(parameter)
            for status_code, response in operation.get("responses", {}).items():
                if not isinstance(response, dict):
                    continue
                existing = response.get("description")
                if not _contains_chinese(existing):
                    response["description"] = RESPONSE_DESCRIPTIONS.get(
                        str(status_code),
                        f"HTTP {status_code} 响应。",
                    )

    grouped_tags: dict[str, dict[str, Any]] = {}
    for code in used_tag_codes:
        tag_name, tag_description = CHINESE_TAGS.get(
            code,
            (f"其他接口（{code}）", "其他系统接口。"),
        )
        tag = grouped_tags.setdefault(
            tag_name,
            {
                "name": tag_name,
                "description": tag_description,
                "x-labelu-module-codes": [],
            },
        )
        tag["x-labelu-module-codes"].append(code)

    schema["tags"] = list(grouped_tags.values())

    components = schema.setdefault("components", {})
    for schema_name, component_schema in components.get("schemas", {}).items():
        if isinstance(component_schema, dict):
            _enrich_schema(schema_name, component_schema)
    bearer = components.get("securitySchemes", {}).get("HTTPBearer")
    if isinstance(bearer, dict):
        bearer["description"] = (
            "Bearer JWT 访问令牌。请只填写令牌原文，Swagger 会自动添加 `Bearer` 前缀；"
            "令牌可通过用户登录接口获取。"
        )
        bearer["bearerFormat"] = "JWT"

    schema["x-seven-gai-chinese-docs"] = True
    schema["x-labelu-chinese-docs"] = True
    schema["x-websocket-endpoints"] = [
        {
            "path": "/ws/task/{task_id}/{sample_id}",
            "description": "标注样本实时协作 WebSocket。",
        }
    ]
    return schema


def configure_chinese_openapi(app: FastAPI) -> None:
    original_openapi: Callable[[], dict[str, Any]] = app.openapi

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema and (
            app.openapi_schema.get("x-seven-gai-chinese-docs")
            or app.openapi_schema.get("x-labelu-chinese-docs")
        ):
            return app.openapi_schema
        app.openapi_schema = enrich_openapi_schema(original_openapi())
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def add_chinese_api_docs(app: FastAPI) -> None:
    async def docs_asset(asset_name: str) -> FileResponse:
        allowed_assets = {
            "redoc.standalone.js",
            "swagger-ui-bundle.js",
            "swagger-ui.css",
        }
        asset_path = API_DOCS_ASSET_DIR / asset_name
        if asset_name not in allowed_assets or not asset_path.is_file():
            raise HTTPException(status_code=404, detail="API 文档资源不存在")
        return FileResponse(asset_path)

    async def openapi_json() -> JSONResponse:
        return JSONResponse(app.openapi())

    async def swagger_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/api-docs/openapi.json",
            title="7GAI 中文 API 接口文档",
            swagger_js_url="/api-docs/assets/swagger-ui-bundle.js",
            swagger_css_url="/api-docs/assets/swagger-ui.css",
            swagger_favicon_url="/favicon.svg",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_ui_parameters={
                "deepLinking": True,
                "displayRequestDuration": True,
                "docExpansion": "none",
                "filter": True,
                "persistAuthorization": True,
                "showExtensions": True,
                "tryItOutEnabled": True,
            },
        )

    async def redoc_docs() -> HTMLResponse:
        return get_redoc_html(
            openapi_url="/api-docs/openapi.json",
            title="7GAI 中文 API 接口文档 - ReDoc",
            redoc_js_url="/api-docs/assets/redoc.standalone.js",
            redoc_favicon_url="/favicon.svg",
            with_google_fonts=False,
        )

    # /docs is the long-standing operator bookmark. Keep it as an alias of
    # the same localized document instead of exposing FastAPI's default page.
    app.add_api_route(
        "/docs",
        swagger_docs,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/redoc",
        redoc_docs,
        methods=["GET"],
        include_in_schema=False,
    )

    app.add_api_route(
        "/api-docs/assets/{asset_name}",
        docs_asset,
        methods=["GET"],
        include_in_schema=False,
    )

    app.add_api_route(
        "/api-docs/openapi.json",
        openapi_json,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/api-docs",
        swagger_docs,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        "/api-docs/redoc",
        redoc_docs,
        methods=["GET"],
        include_in_schema=False,
    )
