# RUN-0138 正式迁移 head 与历史后缀映射收口

## 1. 本轮范围

本轮处理迁移审计回归，不执行数据库迁移，不连接生产，不修改正式迁移文件。目标是让审计契约反映当前真实正式链，避免把已存在的第三方 S3 Worker 租约迁移误判为“第二个 head”或“迁移缺失”。

## 2. 发现

当前正式 Alembic 链包含：

- `z4a5b6c7d8e9`：训练、BM1688、边端执行租约与 Worker 动作；
- `z5a6b7c8d9e0`：第三方接入去项目化；
- `z6b7c8d9e0f1`：Provider 作业去项目化；
- `z7a8b9c0d1e2`：第三方 S3 回流 Worker 的 `worker_lease_owner`、`worker_lease_expires_at`、`worker_fencing_token`、`attempt_count` 和扫描索引。

原审计常量仍停留在 `z4a5b6c7d8e9`，历史后缀计划也没有登记 `z5/z6/z7`，因此迁移专项出现 3 项失败。这是审计契约漂移，不是生产数据库已升级的证据。

## 3. 本轮修复

- `CURRENT_LOCAL_HEAD` 更新为 `z7a8b9c0d1e2`；
- 历史后缀计划新增 `z5a6b7c8d9e0`、`z6b7c8d9e0f1`、`z7a8b9c0d1e2` 的对象映射；
- 执行租约审计测试、一期迁移重建审计测试和隔离预演测试改为断言真实 head 与 41 个后缀修订；
- 没有修改正式 migration 的 `revision/down_revision/upgrade`，没有删除或回滚已有迁移。

## 4. 本地验证

```text
python -m pytest labelu/tests/alembic/test_execution_lease_migration_audit.py labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py labelu/tests/alembic/test_dispatch_lease_migration_candidate.py -q --disable-warnings --tb=short
15 passed
```

此前的 3 个迁移审计失败已消除；本轮未运行生产数据库 DDL。

## 5. 生产边界

- 这只能证明本地迁移图和审计测试一致，不能证明生产数据库已经升级到 `z7a8b9c0d1e2`；
- 仍需在同版本 MySQL 沙箱和脱敏生产快照执行空库升级、重复升级、表/列/索引对账、锁等待和备份恢复；
- 在上述证据完成前，训练、BM1688、边端和 S3 独立 Worker 开关继续关闭。

## 6. 交付状态

本轮未提交、未推送、未部署，等待用户明确“提交代码”。

