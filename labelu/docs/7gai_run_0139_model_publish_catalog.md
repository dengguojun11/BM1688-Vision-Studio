# RUN-0139 模型中心发布目录与 AICM 对齐审计

## 1. 本轮目标

按 R01 产品、R04 后端、R05 外部平台、R08 前端和 R09 测试职责，核对模型中心是否满足以下生产约束：

1. AICM 返回的 `model_set_id` 是本平台发布模型的唯一业务编号，平台目录中的 `public_model_id` 必须与它一致。
2. 发布成功后必须在 LabelU 本地保留一份可校验的模型包；外部 AICM 只作为发布对象和历史同步来源。
3. 管理人员关闭下载只能影响后续从 LabelU 获取模型包的请求，不能宣称召回已经下载到外部平台的文件，也不引入“回滚”按钮。
4. 第三方 S3 问题数据通过 `metadata.json` 中的 AICM 模型 ID 找到同一平台模型、导出记录、训练任务和标签契约，随后复用回流任务创建链路。

## 2. 本地代码核对结果

| 业务不变量 | 核对位置 | 结论 |
|---|---|---|
| AICM ID 对齐 | `published_model_catalog.register_published_model` | 发布成功以 AICM `model_set_id` 写入 `public_model_id`、`aicm_model_set_id`，并维护 AICM 标识别名 |
| 本地包保留 | `register_published_model`、`_persist_local_package` | 新发布写链使用 `package_required=True`，无有效 ZIP 时拒绝登记；包路径固定在 `published_model_packages/<AICM ID>/model.zip`，复制采用临时文件加原子替换 |
| ModelVersion 血缘 | `register_published_model` | 有对应训练任务时回写 `ModelVersion.published_catalog_id`、发布状态、发布时间和发布人 |
| 下载关闭并发 | `crud_published_model_catalog.compare_and_set_download_enabled`、`update_model` | 修改下载开关必须携带当前值，数据库条件更新失败返回 409，避免旧页面覆盖新状态 |
| 公共下载门禁 | `resolve_download_file` 和 `/api/v1/public/models` | 同时检查模型状态、公开状态、下载开关、本地包或 AICM 可补缓存状态；下载关闭返回冲突，不触发缓存 |
| 专家回流对齐 | `experiment_dataset_import._published_model_dataset_category` 与 `strategy_evolution._resolve_export_id_from_model_id` | 先按 AICM 模型 ID 解析发布目录/导出/训练/任务，再继承标签分类；找不到血缘时进入等待或异常，不猜测模型 |
| 发布权限 | `yolo_aicm_publish.start_publish_job` | 使用当前用户身份和任务访问权校验，任务创建者及有效协作者可以发布；AICM 环境配置、目录同步和下载开关仍由管理人员控制 |

本轮未发现需要新增重复模型实体、重复下载接口或“模型回滚”代码的实质缺口。

## 3. 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_published_model_catalog.py labelu/tests/internal/adapter/routers/test_published_model_catalog.py labelu/tests/e2e/test_yolo_model_catalog_frontend.py -q
22 passed, 2 skipped
```

测试覆盖本地模型包下载、AICM 不可用时的本地包兜底、公共目录过滤、管理端下载开关 CAS、过期页面 409、别名保护、桌面/窄屏模型目录和中文弹窗。

## 4. 生产只读证据

2026-09-19 对 `http://113.141.72.175:10000` 进行只读 HTTP 核验：

| 地址 | 结果 | 说明 |
|---|---:|---|
| `/openapi.json` | 200 | 返回真实 JSON |
| `/healthz`、`/readyz`、`/version` | 200 | 当前仍返回前端 HTML，不能作为后端健康证据 |
| `/api/v1/published-models` | 401 | 路由存在，未带登录凭据 |
| `/api/v1/datasets` | 404 | 生产仍缺规范数据集路由 |
| `/api/v1/model-versions` | 404 | 生产仍缺规范模型版本路由 |

上述只是入口和路由只读证据，不代表生产模型目录、AICM 对账、本地包下载、S3 回流或真实发布已经通过。

## 5. 验收等级与边界

- 本轮等级：**L1 本地审计通过**。
- 未完成：真实 AICM 发布与 ID 对账、生产本地包下载、第三方 S3 回流按模型 ID 建任务、真实 MySQL 并发、Ubuntu/systemd、生产数据库迁移和浏览器真机验收。
- 生产策略：发布模型出现问题时，管理人员关闭 LabelU 后续下载并发布替代版本；不提供模型回滚，不承诺召回外部已下载文件。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。
