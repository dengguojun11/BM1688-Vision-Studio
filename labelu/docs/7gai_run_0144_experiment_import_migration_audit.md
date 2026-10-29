# RUN-0144 第三方回流表与正式迁移链只读审计

## 1. 审计范围

- 角色：R03 架构、R04 后端、R09 测试。
- 对象：正式 Alembic 迁移图、`ExperimentDatasetImport` ORM、`experiment_dataset_import` 建表及后续增量迁移、空库升级可运行性。
- 边界：只读检查本地代码和临时 SQLite；未连接生产、未执行生产 DDL、未修改正式迁移、未提交或部署。

## 2. 实际证据

### 2.1 正式迁移 head

通过 Alembic `ScriptDirectory.from_config("labelu/alembic_labelu/alembic.ini")` 实际解析：

```text
heads ['z7a8b9c0d1e2']
current z7a8b9c0d1e2
revisions 134
```

当前本地正式迁移图只有一个 head，第三方回流 Worker 租约迁移 `z7a8b9c0d1e2` 位于正式链上，父版本为 `z6b7c8d9e0f1`。这只证明本地迁移图，不代表生产数据库已升级到该版本。

### 2.2 ORM 与迁移后的表结构

在 `.tmp/run0144-empty.sqlite` 上使用独立 Alembic 配置执行全新数据库 `upgrade(head)`，随后用 SQLAlchemy Inspector 对比 ORM：

| 项目 | 结果 |
|---|---:|
| ORM 列数 | 37 |
| 升级后数据库列数 | 37 |
| ORM 有、数据库没有 | 0 |
| 数据库有、ORM 没有 | 0 |

关键字段均已覆盖：`package_id`、来源系统与外部任务字段、`dataset_category_id`、`aggregation_key`、`aggregation_group_id`、`metadata_json`、样本计数、`worker_lease_owner`、`worker_lease_expires_at`、`worker_fencing_token`、`attempt_count`、错误和完成时间字段。

### 2.3 索引与幂等

升级后的临时 SQLite 发现 18 个索引，包含唯一包幂等约束 `uq_experiment_dataset_import_package_id`、Worker 扫描复合索引 `idx_experiment_dataset_import_worker_scan(status, worker_lease_expires_at, id)`、状态软删除索引，以及来源、聚合、任务、创建/更新者、删除时间和两项 Worker 租约单列索引。

## 3. 测试证据

执行：

```text
python -m pytest labelu/tests/alembic/test_experiment_import_worker_migration.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py -q --disable-warnings --tb=short
```

结果：`5 passed`，113 warnings。另行执行全新 SQLite `upgrade(head)` 成功，并完成 ORM/Inspector 37 列和索引对账。

## 4. 结论与未完成项

1. 本地正式链、回流导入表 ORM、迁移字段和索引目前没有发现可立即修复的结构缺口。
2. 空库升级实际可运行，但 `env.py` 对完全空库采用当前 ORM 基线建表并直接 stamp head；这不是逐个执行 134 个历史迁移，不能替代生产快照迁移演练。
3. 尚未在生产同版本 MySQL 或脱敏生产快照上验证字符集、索引长度、外键顺序、元数据锁、在线 DDL、执行计划和双 Worker 并发领取。
4. 生产回流必须先确认实际 `alembic_version=z7a8b9c0d1e2`、表列/索引逐项一致，再开启 Worker；任何字段或版本漂移都保持失败关闭。

## 5. 放行建议

- R03/R09：保留空库升级、ORM 对账和唯一 head 检查为迁移门禁，但报告中明确“空库基线 stamp”与“生产历史升级”是两种不同证据。
- R10：准备同版本 MySQL 沙箱和脱敏生产副本，执行 `SHOW CREATE TABLE`、`SHOW INDEX`、`EXPLAIN FORMAT=JSON`、短锁等待和备份恢复演练。
- R04/R09：在真实 MySQL 完成双 Worker 领取、租约过期接管、旧 fencing token 拒绝和 20 并发锁等待测试前，保持回流 Worker 关闭。

## 6. 状态

- 本地：L1，只读审计与临时 SQLite 证据通过。
- 预生产：未执行。
- 生产：未执行，不能宣称迁移或回流 Worker 已上线。
- commit/push/deploy：未提交、未推送、未部署，等待用户明确“提交代码”。
