# RUN-0146 旧策略投影与正式任务血缘回写验收

## 1. 轮次信息

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R09 测试主责；R05 外部平台协作。
- 工作包：WP4 第三方 S3 回流闭环。
- 需求编号：`S3-LEGACY-BRIDGE-03`、`S3-TASK-LINEAGE-01`。
- 交付级别：本地 L1；未提交、未推送、未部署。

## 2. 本轮产出

为旧 `/api/v1/integrations/strategy-tasks` 异步兼容链补充完成态验收：

- Worker 完成导入后，旧 `strategy_task_integration` 只回写 `import_status`、首个 `labelu_task_id`、样本数和 metadata 摘要，满足历史页面/回调查询。
- Worker 将正式任务的 `Task.source_integration_id` 补齐到旧策略记录，便于历史任务反查。
- 新 `ExperimentDatasetImport` 仍是导入状态、完整任务 ID、样本计数和错误信息的唯一真源；旧表不参与图片、样本或预标注发现和创建。
- 没有新增项目/业务空间、设备发布、OTA 或模型回滚。

## 3. 验收证据

联合执行第三方导入路由、旧策略异步入口、旧 metadata 兼容转换和 Worker 租约专项：

```text
40 passed, 113 warnings
```

新增测试：

`labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py::test_worker_completion_updates_legacy_projection_and_task_lineage`

覆盖旧投影完成态、首个正式任务 ID、导入样本数、metadata 摘要和正式任务 `source_integration_id`。

## 4. 真实环境边界

本轮没有连接或修改生产。真实 S3 包、AICM 模型 ID、旧第三方回调重试、Ubuntu/systemd Worker、MySQL 锁等待和生产历史数据仍未验收。HTTP 201/PENDING 仍只代表入队，不代表已完成导入。

## 5. 下一依赖

R05 提供脱敏真实包和旧调用方确认；R09/R10 在同版本 MySQL 上验证双 Worker、租约过期接管、旧 fencing token 拒绝和 20 并发；完成前生产回流 Worker 保持失败关闭。用户明确“提交代码”前不执行 commit、push 或部署。

