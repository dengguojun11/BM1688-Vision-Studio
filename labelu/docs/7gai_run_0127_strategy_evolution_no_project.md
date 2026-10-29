# RUN-0127 策略演化与专家回流新写链去项目化

## 1. 目标与边界

本轮只处理策略演化和第三方专家回流入口的项目维度残留。系统登录用户仍只有标注人员、管理人员；回流数据进入统一任务与标注主链，按任务可见性和创建者权限处理，不建立项目、业务空间或工作流项目归属。

历史数据库中的 `project_id` 列、旧迁移和历史读取兼容不在本轮删除范围。它们不得进入新的请求、权限判断、响应字段、幂等键或新增业务对象。策略回调继续使用 `StrategyTaskIntegration` 的平台回执表和原有 HTTP 回调重试字段。

## 2. 代码路径审计与改动

| 路径 | 原问题 | 本轮处理 |
|---|---|---|
| `application/command/strategy_evolution.py` | `CreateStrategyEvolutionTaskCommand` 暴露 `project_id`，新请求可能携带项目归属 | 删除字段；显式携带 `project_id` 的新请求返回校验错误；OpenAPI 请求模型不再展示该字段 |
| `application/service/strategy_evolution.py::accept_task` | 更新/创建回流集成时写 `project_id`，并调用项目工作流投影 | 删除项目字段写入；删除 `_ensure_strategy_workflow` 调用；策略任务仍按 `external_task_id` 幂等 |
| `application/service/strategy_evolution.py::handle_platform_callback` | 收到平台回调时追加 `WorkflowInstance/WorkflowOutbox` 项目对象 | 删除回调工作流投影；保留平台回调回执去重、状态单向推进和结果持久化 |
| `application/service/strategy_evolution.py::_send_outbound_callback` | 出站回调桥接 `flexible_callback`，依赖项目工作流 | 删除桥接；继续使用原有 HTTP、签名、重试和回调状态字段 |
| `application/command/strategy_task_integration.py` | 专家回流任务创建命令需确认不引入项目字段 | 保持无 `project_id`，任务由 `source_system/source_external_task_id/source_integration_id` 建立来源血缘 |
| `domain/models/strategy_task_integration.py`、响应模型和路由 | 新对象/响应不能产生项目字段 | 模型和响应保持无项目属性；路由不接收项目查询参数，内部鉴权仍只保护服务调用 |

## 3. 业务结果

第三方问题图片仍由生产策略 S3 配置自动导入、摘要去重、创建或复用 LabelU 任务，再由预标注、人工确认、DatasetVersion 冻结和训练链继续处理。策略平台回调只更新该回流集成的状态和回执，不创建另一套项目工作流。历史行即使存在旧列，也不会在重试或回调时被补写。

## 4. 并发与失败处理

- `external_task_id` 唯一键和行锁仍是回流入口幂等真源；同一任务重试只更新同一集成记录。
- 平台回调事件使用 `strategy_platform_callback_receipt` 去重；终态不允许回退。
- 回调发送失败继续写 `outbound_callback_status/attempts/next_retry_at/last_error`，不写项目工作流 outbox。
- 显式携带 `project_id` 的新请求直接 422，避免旧调用方误以为项目归属仍受支持。
- 本轮没有删除历史列和旧迁移，避免对生产历史数据进行破坏性 DDL；后续正式迁移需单独提供只读快照、备份和回滚评审。

## 5. 验证证据

- `python -m pytest labelu/tests/internal/application/service/test_strategy_evolution_no_project_scope.py labelu/tests/internal/adapter/routers/test_strategy_task_integration.py -q`：`13 passed`。
- `python -m pytest labelu/tests/internal/adapter/routers/test_strategy_evolution.py -q`：`35 passed`。
- `python -m compileall -q labelu/internal/application/command/strategy_evolution.py labelu/internal/application/service/strategy_evolution.py`：通过。
- `git diff --check`：通过（工作区其他历史改动仍存在，仅检查无空白错误）。

上述均为本地 SQLite/隔离测试证据；未连接生产、未执行生产数据库写入、未提交、未推送、未部署。

## 6. 遗留与下一依赖

生产 OpenAPI 仍需在部署前核验策略演化请求 schema、回流接口和历史兼容路由；生产 MySQL 的历史 `project_id` 列是否可最终清理需另行做脱敏快照和迁移评审。本轮不宣称第三方 S3、AICM、真实 Worker 或生产环境已完成。
