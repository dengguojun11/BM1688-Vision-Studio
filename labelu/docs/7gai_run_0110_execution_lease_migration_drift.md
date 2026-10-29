# RUN-0110 执行租约正式迁移链漂移审计

## 1. 范围与结论

- 日期：2026-09-18，Asia/Shanghai。
- 工作包：WP0 基线与迁移、WP2 训练/编译/边端执行器、WP5 测试验收。
- 角色：R03 架构与迁移主责，R04 后端协作，R09 测试验收；R10 仅负责后续受控数据库演练。
- 范围：`yolo_train_job`、`yolo_bm1688_export`、`yolo_edge_validation_job` 的 ORM 租约字段、索引、正式 Alembic 唯一 head，以及 BM1688 `worker_action` 字段归属。
- 安全边界：只读源码审计和隔离 SQLite 候选演练；不连接生产、不执行正式迁移、不修改生产数据、不提交代码。

**结论：正式 Alembic 图虽然当前只有唯一 head `z3a4b5c6d7e8`，但三个模型的执行租约结构尚未进入正式唯一迁移链。** 当前结构来自 ORM/空库 bootstrap 和 `docs/7gai_phase_one_migration_candidates/gai_p1_08_add_dispatch_leases.py`，候选文件不属于正式 `alembic_labelu/versions`。

## 2. 事实矩阵

| 对象 | 当前 ORM | 正式 `versions` | 一期候选 `gai_p1_08` | 结论 |
|---|---|---|---|---|
| 三表 `queue_lease_owner`、`queue_lease_expires_at` | 三表都有 | 只有 `k9l0m1n2o3p4` 文本上给 `yolo_train_job` 加字段，未覆盖三表完整结构 | 三表都有 | 正式链不完整 |
| 三表 `execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token` | 三表都有 | 正式迁移源码没有字段来源 | 三表都有 | 正式链缺失 |
| 三表租约索引和 Worker 扫描复合索引 | ORM只有字段上的单列索引；没有候选复合扫描索引 | 正式迁移源码没有候选索引名 | 每表5个索引，含扫描复合索引 | 正式链缺失 |
| `yolo_bm1688_export.worker_action` | 有字段和 `ix_yolo_bm1688_export_worker_action` | 正式迁移源码没有字段/索引来源 | `gai_p1_08` 添加字段和索引 | 正式链缺失 |
| 正式 Alembic head | 唯一 head `z3a4b5c6d7e8` | 唯一 | 不适用 | 图唯一不等于租约已迁移 |

正式迁移中能检出的租约字段来源只有：

- `queue_lease_owner`、`queue_lease_expires_at`：`k9l0m1n2o3p4_link_auto_training_existing_pipeline.py`，只针对 `yolo_train_job` 的历史自动训练连接字段。
- `execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token`：无正式来源。
- 候选索引和 `worker_action` 索引：无正式来源。

## 3. 新增审计能力

- `labelu/scripts/audit_execution_lease_migration.py`：只读读取正式迁移源码、Alembic heads、当前 ORM 元数据和候选路径，输出字段/索引来源及缺失矩阵；漂移时退出码为 `1`，不会执行数据库操作。
- `labelu/tests/alembic/test_execution_lease_migration_audit.py`：锁定唯一 head、三表 ORM 五字段、正式链缺失执行租约、候选不属于正式 versions，以及 `worker_action` 字段/索引仍只有 ORM/候选归属。

## 4. 隔离验证证据

- `python -m pytest labelu/tests/alembic/test_execution_lease_migration_audit.py labelu/tests/alembic/test_dispatch_lease_migration_candidate.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py -q --disable-warnings`：`11 passed`。
- 静态审计报告：正式 head 为 `z3a4b5c6d7e8`，唯一 head 为真；三表各自缺少正式执行租约字段，候选租约索引和 `worker_action` 字段/索引均未出现在正式 `versions`。
- 候选 SQLite 迁移仍可重复升级/降级，现有候选专项和一期预演通过；该结果仅证明候选 DDL 结构可演练，不证明正式生产迁移已完成。
- `python -m py_compile labelu/scripts/audit_execution_lease_migration.py`：通过；新增测试和脚本按 Black 格式化。

## 5. 空库 bootstrap 的误判风险

`labelu/alembic_labelu/env.py` 对真正空库直接按当前 `Base.metadata` 建表并 stamp 当前 head。因为当前 ORM 已包含租约字段和 `worker_action`，空库测试会看到这些列，但这不是历史生产库从 `h7i8j9k0l1m2` 升级到 head 的证据，也不能证明正式 migration `upgrade()` 会补齐字段、索引和复合扫描索引。生产脱敏快照副本仍必须按正式替换迁移逐表验证。

## 6. 后续处理与放行条件

1. R03/R04 将 `gai_p1_08` 的租约字段、15 个租约/扫描索引以及 `worker_action` 字段/索引纳入正式唯一替换链；不得直接把候选文件复制成第二个 head。
2. R09 在同版本空 MySQL 和生产脱敏快照副本上验证：升级/重复升级/降级、既有数据非空、索引列顺序、字段默认值、回滚/恢复和唯一 head。
3. R10 提供受控数据库和备份恢复证据；未完成前不执行生产 DDL，不开放独立训练、编译、边端 Worker 的生产消费。
4. 合入正式链后，更新本审计测试的“缺失”期望为“已覆盖”，保留审计脚本防止后续字段漂移。

## 7. 交付状态

- 本轮等级：本地 L1，正式迁移漂移已证实并显式阻断。
- 提交：未提交，等待用户明确“提交代码”。
- 推送/部署：未推送、未部署、未连接生产。
