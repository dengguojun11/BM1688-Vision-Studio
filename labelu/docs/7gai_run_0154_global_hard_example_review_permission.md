# RUN-0154 全局难例审核权限与旧工作流测试迁移

日期：2026-09-19  
验收等级：本地 L1；未提交、未推送、未部署。

## 1. 本轮目标

按当前生产权限和一期去项目化规则收口两处不一致：

1. 第三方 S3/边端问题图片进入全局“数据回流”后，系统自动接收、去重、聚类和生成批次；全局难例的接受/排除由管理人员执行，标注人员只能查看自己可见的数据。
2. 删除旧的“项目策略工作流”测试假设，不恢复 `project_id`、项目工作流或默认项目；改为验证当前统一集成记录和平台回调幂等。

## 2. 角色与页面行为

| 页面区域 | 标注人员 | 管理人员 |
|---|---|---|
| 数据回流 -> 难例回流队列 | 查看本人创建、本人任务权限或本人模型血缘可见的全局难例；可打开居中弹窗查看 | 查看全部全局难例；可接受或排除，并填写排除理由 |
| 难例详情弹窗 | 下拉框、理由输入框和提交动作只读；主按钮显示“关闭” | 可选择“接受，纳入训练候选”或“排除，不进入训练”；提交前校验在线状态和排除理由 |
| 数据回流 -> 回流编排 | 不显示全局重建聚类、生成批次、接受批次和冻结动作 | 可重建聚类、生成批次、接受批次、校验并冻结数据集版本 |

后端权限仍是最终门禁。前端隐藏/只读只用于避免标注人员点击后得到 403，不能代替接口鉴权。

## 3. 代码变更

### 3.1 前端权限收口

- 文件：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/evolution.tsx`
- 复用既有 `useTaskSuperuser`，不增加登录角色或新的权限接口。
- 全局难例详情弹窗根据 `isTaskSuperuser` 展示：
  - 管理人员：`提交审核`；
  - 标注人员：`关闭`，并显示“全局难例由管理人员审核，当前账号仅可查看”。
- 标注人员不能修改审核决策和理由，也不会发送 `POST /v1/edge-feedback/hard-examples/{id}/review`。
- 弹窗继续使用居中 `Modal`，没有恢复抽屉。

### 3.2 旧测试迁移

- 文件：`labelu/tests/internal/application/service/test_strategy_workflow_bridge.py`
- 移除已经退役的 `project_id` 构造和不存在的 `_ensure_strategy_workflow` 断言。
- 新测试验证：
  - 平台回调落入 `StrategyTaskIntegration`；
  - 同一 `callback_event_id` 重复回调只保留一条回执；
  - 无项目回调不会创建 `WorkflowInstance`；
  - 历史无项目记录仍可保留回执。

## 4. 本地验证证据

```text
python -m pytest labelu/tests/internal/application/service/test_strategy_workflow_bridge.py labelu/tests/internal/application/service/test_strategy_evolution_no_project_scope.py -q --disable-warnings --tb=short
7 passed

联合主动学习与迁移专项复跑：12 passed（包含上述 7 项）

pnpm exec vitest run src/pages/platform/pages/evolution.test.tsx --run
3 passed

pnpm run type-check
通过
```

前端测试输出包含 Ant Design/jsdom 的既有 `computedStyle`、React 兼容性警告，但测试退出码为 0；这不是业务失败。

## 5. 并发与性能影响

- 本轮只增加一次既有 `/v1/users/me` 权限查询的复用，不增加回流、聚类、批次或训练请求。
- 审核提交仍由后端数据库唯一约束、状态条件和事务门禁负责；前端权限判断不会改变并发语义。
- 管理人员并发审核同一难例时，后端状态冲突仍返回 409，页面已有刷新/冲突提示；本轮没有把前端禁用误当作并发控制。
- 回流自动链路仍使用 `scope_key=global`、事件/指纹唯一键、批次幂等键和 Worker 租约；同版本 MySQL 锁等待、20 并发和失租接管尚未执行。

## 6. 未完成与放行边界

- `labelu/tests/internal/adapter/routers/test_active_learning.py` 仍有 9 个旧项目控制面测试直接创建项目；当前接口按设计返回 403。它们属于待迁移的历史测试，不计入全局主动学习新链路证据，也不能通过恢复项目写入口来“修复”。
- 未执行同版本 MySQL 在线迁移、锁等待、20 并发审核/批次幂等和历史重复数据清理。
- 未连接真实第三方 S3/MinIO、AICM、TPU-MLIR 或 BM1688；没有把本地测试当成生产回流或边端验收。
- 未提交、未推送、未部署；生产 Worker 和真实外部副作用开关保持原有失败关闭状态。

## 7. 关联文档

- `7gai_run_0152_active_learning_global_scope.md`
- `7gai_run_0153_global_workflow_and_evolution_ui.md`
- `7gai_role_delivery_plan.md`
- `7gai_delivery_ledger.md`

## 8. 生产只读探针（2026-09-19）

本轮未登录、未写库、未调用外部模型平台，只读取 `http://113.141.72.175:10000`：

| 地址 | 结果 | 结论 |
|---|---|---|
| `/healthz`、`/readyz`、`/version` | HTTP 200，但返回前端 HTML | 仍不能作为后端健康/版本 JSON 证据 |
| `/openapi.json` | HTTP 200，返回 JSON，标题为“LabelU 中文 API 接口文档” | OpenAPI 真源可读 |
| `/api-docs/openapi.json` | HTTP 200，但返回前端 HTML | Nginx/API 分流问题仍未修复 |
| `/training` | HTTP 200，返回前端 HTML | 页面入口可达，不代表训练链路通过 |
| `/api/v1/published-models` | HTTP 401 | 未登录接口鉴权正常，本轮未使用账号写入 |

该探针与前序生产差距记录一致：线上仍未证明本轮前端权限改动、MySQL 迁移、S3/AICM 对账、真实 BM1688 和 Worker 重启接管。没有用户明确“提交代码”前，不进行提交、部署或线上写验证。
