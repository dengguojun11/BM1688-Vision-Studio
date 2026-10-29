# RUN-0195 正式执行租约字段闭包审计与新增迁移阻断

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；工作包 WP2/WP5。
- 范围：只读核对当前正式 Alembic head、ORM 执行租约字段/索引和 `worker_action` 来源；不连接数据库，不修改正式迁移目录。

## 审计结论

1. 正式 Alembic 当前只有一个 head：`z9f3a4b5c6d7`。
2. 三张执行表的 ORM 租约字段闭包完整：`queue_lease_owner`、`queue_lease_expires_at`、`execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token` 均有正式迁移来源。
3. 训练表的 `assigned_gpu_json`、`execution_runtime_pid`，以及 BM1688 表的 `worker_action` 和对应索引也均由正式 `z4a5b6c7d8e9` 提供；正式迁移缺失字段、缺失索引、缺失辅助字段和缺失辅助索引均为空。
4. 因此当前没有需要新增的执行租约正式迁移。新增一个空迁移或重复加列迁移不会增加能力，只会增加 revision 漂移和第二个 head 风险。

## 阻断证据

- 候选 `gai_p1_08` 的字段/索引语义与 `z4...` 一致，但其 `down_revision=gai_p1_07` 不存在于正式 Alembic 图；它已经标记 `SUPERSEDED_BY=z4a5b6c7d8e9`，只保留用于历史对账，不能作为待执行迁移。
- 当前正式 head 和 ORM 闭包已满足静态结构要求，但真实生产是否已执行到该 head、历史表中是否存在字段/索引漂移，必须由同版本 MySQL 脱敏快照、`alembic_version` 对账、`SHOW CREATE TABLE`、`SHOW INDEX` 和备份恢复证据证明；本轮没有这些生产证据。
- 所以本轮不能给出可执行的新正式迁移草案，也不能因为静态闭包通过就放开生产 DDL；迁移发布闸门继续要求备份恢复、锁等待、20 并发领取和旧 fencing token 拒绝证据。

## 本地验证

- `python -m pytest labelu/tests/alembic/test_execution_lease_formal_closure.py labelu/tests/alembic/test_execution_lease_candidate_alignment.py labelu/tests/internal/common/test_migration_release_gate.py -q --disable-warnings --tb=short`：专项通过。
- 只读脚本未连接数据库；未移动或删除候选文件；未执行 upgrade/downgrade；未写生产。
- 当前验收等级：本地 L1。提交、推送、部署和生产迁移仍等待用户明确授权及真实 MySQL 证据。
