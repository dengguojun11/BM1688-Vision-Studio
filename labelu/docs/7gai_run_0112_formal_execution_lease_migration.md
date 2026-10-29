# RUN-0112 执行租约正式迁移链落地

## 1. 本轮目标

把训练、BM1688 编译、边端验证和独立 Worker 实际依赖的数据库字段、索引和动作路由纳入正式唯一 Alembic 链，消除 RUN-0110 发现的“ORM 有、正式历史库升级没有”的结构漂移。

本轮只做本地代码和隔离数据库验证，不连接生产、不执行生产 DDL、不启动真实 TPU-MLIR 或 BM1688 Worker。

## 2. 交付内容

- 新增正式后继迁移 `z4a5b6c7d8e9`，父版本为 `z3a4b5c6d7e8`，不创建第二个 head。
- `yolo_train_job`、`yolo_bm1688_export`、`yolo_edge_validation_job` 均补齐：
  - `queue_lease_owner`、`queue_lease_expires_at`；
  - `execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token`；
  - Worker 扫描复合索引和租约查询索引。
- `yolo_bm1688_export` 补齐 `worker_action` 字段和索引，空值继续由 Worker 按 `export` 兼容处理。
- `yolo_train_job` 补齐训练 Worker 已使用的 `assigned_gpu_json`、`execution_runtime_pid`。
- ORM 同步声明训练 Worker 和边端派发复合索引，避免空库 bootstrap 与历史库升级后的查询计划不一致。
- 审计脚本和测试改为验证正式链已覆盖，而不是继续把候选迁移当作正式来源。

## 3. 为什么不在迁移中回填历史动作

不能在生产迁移窗口执行：

```sql
UPDATE yolo_bm1688_export SET worker_action = 'export' WHERE worker_action IS NULL;
```

这会对历史 BM1688 表产生全表写锁/大量重写，和生产在线迁移的短锁窗口目标冲突。当前 Worker 已明确把空动作解释为 `export`，所以新增列保持空值是兼容且安全的；新建任务由应用写入 `export`，历史任务无需一次性回填。后续如需统计归一化，另建可限批、可暂停、可观测的后台作业，不能混在结构迁移中。

## 4. 降级和恢复策略

本迁移的 `downgrade()` 失败关闭，不自动删除列和索引。原因是历史库可能已经通过热修或人工方式存在部分同名字段，迁移代码无法安全判断字段归属；自动删列可能破坏租约、运行 PID 或已发布模型关联数据。

生产恢复方式是：保留迁移前备份，验证失败时按备份恢复数据库，再恢复对应应用版本。这里的数据库恢复与模型发布回滚不同：已发布到 AICM 或已被其他平台下载的模型不能召回，只能由管理人员禁止本平台继续下载，并保留审计记录。

## 5. 本地验证

```text
python -m pytest \
  labelu/tests/alembic/test_execution_lease_formal_migration.py \
  labelu/tests/alembic/test_execution_lease_migration_audit.py \
  labelu/tests/alembic/test_dispatch_lease_migration_candidate.py \
  labelu/tests/alembic/test_phase_one_migration_rehearsal.py \
  labelu/tests/alembic/test_phase_one_migration_plan.py \
  labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py \
  -q --disable-warnings --tb=short
```

验收要求：正式 head 唯一为 `z4a5b6c7d8e9`；重复升级幂等；非空 SQLite 结构完整；空动作不需要回填；自动降级被明确拒绝。SQLite 结果只证明结构和幂等，不能替代 MySQL 锁等待、执行计划或生产快照证据。

## 6. 生产放行条件

1. R10 提供同版本本机 `labelu_migration_sandbox*` MySQL 和脱敏生产快照副本。
2. R09/R03 验证 `SHOW CREATE TABLE`、`SHOW INDEX`、字段默认值、复合索引列顺序、非空历史数据和重复升级。
3. 通过短 `lock_wait_timeout`、双进程/20 并发领取、旧 token 写入拒绝、备份恢复和磁盘增长演练。
4. 迁移前确认 Web、训练 Worker、BM1688 Worker、边端验证 Worker 版本兼容；正式迁移失败时停止切换，不执行自动降级。
5. 上述证据完成前，独立 Worker 保持 `fail-closed`，本轮等级为本地 L1。

## 7. 角色交付

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R03 架构与迁移 | 唯一父链、字段/索引契约、前向-only恢复边界 | 本地完成；MySQL/生产快照待验收 |
| R04 后端 | 确认 Worker 空动作兼容和新任务默认值 | 本地静态完成 |
| R09 测试 | 正式 head、重复升级、非空结构、漂移审计 | 专项测试通过 |
| R10 运维 | MySQL 沙箱、备份恢复、锁等待和在线迁移 | 本轮未执行 |

## 8. 交付状态

本轮为本地 L1，未提交、未推送、未部署。等待用户明确“提交代码”后，才能进入提交、部署和生产验证流程。
