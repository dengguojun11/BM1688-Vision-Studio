# RUN-0153 全局回流工作流与进化中心页面去项目化

日期：2026-09-19

等级：本地 L1；未提交、未推送、未部署。

## 目标

让第三方 S3/边端难例进入“进化中心”后，能够在不选择项目的情况下完成：聚类、生成批次、接受批次、创建数据集版本和建立专家回流工作流。历史项目工作流继续可读，不恢复项目/业务空间菜单。

## 角色交付

| 角色 | 交付内容 | 验收 |
|---|---|---|
| R03 架构 | 统一 `scope_key` 作用域；工作流和数据集保留历史项目兼容列；外部通用工作流继续退役 | Alembic 单一 head 为 `z9f3a4b5c6d7` |
| R04 后端 | 工作流支持全局创建、查询和血缘校验；主动学习接受链使用受控内部入口；项目工作流仍按历史权限校验 | 新库全局聚类/批次/接受专项通过 |
| R08 前端 | 回流编排不再请求项目列表、不传项目参数；聚类、批次、数据集版本和工作流使用同一页面链路 | 进化页面专项 1 项通过，类型检查通过 |
| R09 测试 | 覆盖全局作用域、重复批次、数据集版本和工作流编号；保留旧项目测试失败原因 | 全局专项 2 项通过；旧测试因历史项目创建权限不再作为新链证据 |
| R10 运维 | 记录迁移顺序和生产门禁 | 同版本 MySQL、锁等待、备份恢复仍待执行 |

## 代码与迁移

- `WorkflowInstance.project_id` 改为历史兼容可空字段，新增 `scope_key` 和作用域索引。
- `CreateWorkflowCommand.project_id` 改为可选；响应返回 `scope_key`。
- 通用工作流 HTTP 写入口仍由一期退役门禁阻断；仅主动学习服务通过 `allow_evolution=True` 受控创建和推进 `expert_feedback` 工作流。
- `feedback_cluster`、`active_learning_batch` 使用 `scope_key`、模型版本和来源包建立稳定幂等边界。
- 新增正式迁移：
  - `z9d1e2f3a4b5_add_active_learning_global_scope.py`
  - `z9e2f3a4b5c6_add_global_workflow_scope.py`
  - `z9f3a4b5c6d7_make_dataset_project_compat_nullable.py`

## 前端行为

“进化中心 -> 回流编排”直接加载全局聚类、全局工作流和当前用户可见数据集；创建批次、接受批次和冻结版本均不要求项目编号。审核弹窗保持居中，排除项目选择依赖。

## 验证证据

- `python -m pytest labelu/tests/internal/adapter/routers/test_active_learning_global_scope.py -q`：`2 passed`（新 SQLite 库）。
- `pnpm exec vitest run src/pages/platform/pages/evolution.test.tsx --run`：`1 passed`。
- `pnpm run type-check`：通过。
- 目标 Python 模块 `compileall`：通过。
- `python -m alembic -c labelu/alembic_labelu/alembic.ini heads`：唯一 head `z9f3a4b5c6d7`。

## 未完成边界

未执行同版本生产 MySQL 迁移、历史数据冲突清理、20 并发批次/工作流幂等、真实 S3/AICM/BM1688 联调、生产部署和线上验证。旧主动学习测试仍创建项目，因当前测试用户不是系统管理员而返回 403；这属于旧项目夹具，不代表无项目新链路失败，后续应迁移为全局测试或使用管理人员夹具。
