# RUN-0345 自动训练轮次阶段唯一性（2026-09-21，本地）

## 责任角色与工作包

- 责任角色：R03 模型算法架构师、R04 后端研发、R09 测试与迁移审计。
- 工作包：WP0 架构与数据契约、WP2 训练/编译/边端执行链、WP5 并发与交付门禁。
- 需求：同一自动训练轮次在训练任务、训练作业、BM1688 编译作业、边端验证作业中各最多有一条记录；并发重复创建必须由数据库最终兜底。

## 现状与风险

四个 ORM 模型都将 `auto_training_round_id` 声明为唯一，但历史正式迁移只增加了字段，没有数据库级唯一索引。仅依赖服务层 `.first()` 和 ORM 声明无法阻止两个并发请求同时插入相同轮次。

## 本轮处理

新增正式 Alembic 迁移 `zf2a3b4c5d6e`，父版本为 `ze1f2a3b4c5d`：

- 对 `training_task`、`yolo_train_job`、`yolo_bm1688_export`、`yolo_edge_validation_job` 逐表检查字段和历史重复值；
- 发现非空 `auto_training_round_id` 重复时明确阻断迁移，不自动猜测或删除数据；
- 无重复时创建四个唯一索引：
  - `uq_training_task_auto_training_round`
  - `uq_yolo_train_job_auto_training_round`
  - `uq_yolo_bm1688_export_auto_training_round`
  - `uq_yolo_edge_validation_job_auto_training_round`
- 允许多个 `NULL`，兼容尚未接入自动训练的普通记录；
- 重复执行幂等；降级不自动删除索引，生产恢复只允许使用迁移前备份。
- BM1688 编译和边端验证的物化服务在读取轮次时使用行锁；服务层先复用已有阶段记录，数据库唯一索引作为最终兜底。

## 影响范围

- 数据库：新增四个唯一索引，不回写历史业务数据。
- API/服务：没有改变接口字段和普通任务流程；并发重复写入由数据库约束返回冲突，调用方现有幂等/重试逻辑继续生效。
- 页面：无页面变更。
- 生产：未连接、未修改、未执行迁移。

## 本地验证证据

- `python -m pytest labelu/tests/alembic/test_auto_training_round_uniqueness_migration.py labelu/tests/alembic/test_auto_training_migrations.py labelu/tests/alembic/test_phase_one_migration_plan.py labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py labelu/tests/alembic/test_execution_lease_formal_closure.py labelu/tests/alembic/test_execution_lease_migration_audit.py labelu/tests/alembic/test_execution_lease_candidate_alignment.py -q`：`23 passed`。
- `python -m pytest labelu/tests/alembic -q`：完整迁移测试到 `100%`，无失败。
- `python -m pytest labelu/tests/internal/application/service/test_auto_training_bm1688_edge_flow.py labelu/tests/internal/application/service/test_auto_training_phase_one_gate.py -q`：`56 passed`。
- 新增测试覆盖：空库索引、重复升级、多个 `NULL`、重复轮次 fail-closed、降级保护和迁移父子链。

## 风险与遗留项

- 生产执行前必须在生产只读快照或受控 MySQL 沙箱完成重复数据预检；如存在重复轮次，需先由管理人员确定保留记录和数据修复方案。
- 当前仍未提交代码、未推送、未部署；等待用户明确说“提交代码”后再进入提交和生产验证流程。
