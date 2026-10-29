# RUN-0143 旧策略 S3 入口异步契约与本地迁移结构收口

## 1. 本轮范围

- 责任角色：R03 架构、R04 后端、R09 测试；R05 外部平台和 R10 运维协作。
- 工作包：WP4 第三方 S3 回流闭环，兼容入口测试与本地结构基线。
- 需求编号：`S3-LEGACY-BRIDGE-02`、`MIG-IMPORT-WORKER-TEST-01`。
- 前置核验：RUN-0142 已将旧接口转换为统一回流作业，本轮只处理测试契约、持久化结构兼容和证据记录，不改变生产接口路径。

## 2. 代码与数据影响

### 2.1 本地测试库结构

`labelu/tests/conftest.py` 对跳过 Alembic 的持久化 SQLite 增加了缺失列兼容：

- `experiment_dataset_import.source_type/source_system/source_external_task_id/source_external_package_id/source_integration_id`；
- `worker_lease_owner/worker_lease_expires_at/worker_fencing_token/attempt_count`；
- Worker 扫描复合索引 `(status, worker_lease_expires_at, id)`。

这只是本地测试数据库兼容，不等价于生产 MySQL 迁移。生产仍必须执行正式 head `z7a8b9c0d1e2` 并做结构、索引、行数和锁等待对账。

### 2.2 旧入口测试契约

`test_strategy_task_integration.py` 已从同步导入断言迁移为统一异步契约：

1. POST 保留 HTTP 201，但 `import_status=PENDING`；
2. 返回 `package_id` 和 `/internal/v1/experiment-datasets/{package_id}` 状态地址；
3. 请求完成时不创建 Task、DataSource、Attachment、Sample 或 PreAnnotation；
4. 重复通知只保留一个 `ExperimentDatasetImport`，相同外部 ID 载荷变化仍返回 409；
5. S3 暂时故障不会在 Web 请求内触发读取或创建；
6. 数据集分类不存在或已停用时返回 400，不允许静默接收入库。

## 3. 验证证据

```text
python -m pytest labelu/tests/internal/adapter/routers/test_strategy_task_integration.py -q --disable-warnings --tb=short
8 passed

python -m pytest labelu/tests/internal/application/service/test_legacy_s3_adapter.py labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q --disable-warnings --tb=short
12 passed

python -m compileall -q labelu/internal
通过

git diff --check
通过
```

本地证据说明：测试证明新旧入口的数据库对象和状态契约一致，但未证明真实 S3、AICM、独立 Worker、MySQL 并发或生产迁移。

## 4. 风险与遗留

- 生产当前只读探针曾显示数据库仍处于 `z3a4b5c6d7e8`，而代码目标 head 是 `z7a8b9c0d1e2`；未执行任何生产迁移。
- 生产 MySQL 必须补齐回流表 Worker 租约列和索引后，才能打开 `EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED`。
- 旧第三方调用方是否接受 `201 + PENDING + status_url` 仍需 R05 提供脱敏请求、响应处理和重试证据。
- 真实 metadata、AICM 模型 ID、Worker 重启接管和跨包内容去重仍未验收。

## 5. 下一依赖与恢复

- R03/R10：在同版本 MySQL 沙箱和生产脱敏副本执行正式 head 迁移演练、结构对账和锁等待测试。
- R05/R09：用真实脱敏 S3 包验证旧入口和新入口结果一致，并确认外部重试语义。
- 失败恢复：本轮只修改本地代码、测试和文档；不删除数据、不修改生产开关。若兼容层异常，可停用旧入口写入并保留新入口作业。
- 提交/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
