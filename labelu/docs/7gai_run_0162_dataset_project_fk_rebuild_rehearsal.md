# RUN-0162 dataset/dataset_version 非空历史行外键移除隔离演练

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色：R03 架构、R09 测试；R10 运维协作；WP0/U0B 第01段。
- 目标：验证第01段在带有非空历史行的 SQLite 脱敏结构上移除 `dataset`、`dataset_version` 的 `project_id` 外键列时，能保留其他列、历史行和索引，并且重复执行不产生重复副作用。

## 变更边界

- 新增 `.tmp/phase-one-rebuild/supplemental/gai_p1_01_project_fk_rebuild.py`，是隔离补充演练，不属于正式 `labelu/alembic_labelu/versions`，也不加入八段候选链的 8 个修订计数。
- 不修改正式 Alembic DDL，不连接生产、不连接 MySQL、不改变 ORM，不删除历史项目表。
- `downgrade()` 明确失败关闭；生产故障处理必须使用升级前快照恢复到独立实例，不能自动补回 `project_id`。

## 本地证据

测试：

```text
python -m pytest labelu/tests/alembic/test_phase_one_dataset_project_fk_rebuild.py -q --disable-warnings --tb=short
```

结果：`2 passed`。补充候选不在八段 `versions` 目录；八段候选链/调度租约复跑 `12 passed`，离线审计仍通过。

验收要求：

1. 两张表各有 2 条非空历史行，升级后行数、非 `project_id` 列和值保持一致。
2. `project_id` 列和指向 `project` 的外键均不存在。
3. 同一隔离数据库重复执行安全跳过。
4. 候选文件路径不在正式 Alembic 目录，且降级拒绝自动回滚。

## 未完成

该演练不证明生产迁移可上线。正式放行仍必须提供同版本 MySQL、脱敏生产快照、表/列/索引/外键/行数对账、元数据锁和备份恢复证据；在证据完成前，U0B 与平台保持本地 L1。
