# RUN-0209 迁移审计事实模型刷新（2026-09-20）

## 责任角色

- R03 架构：核对正式 Alembic head、项目外键闭包和迁移分段归属。
- R04 后端：确认 ORM 与迁移审计口径一致，不改生产 DDL。
- R09 测试：刷新过期的迁移审计断言并执行隔离回归。

## 触发问题

全量本地回归发现迁移审计仍写死旧正式 head `z9b0c1d2e3f4`，并把已经完成
一期 ORM 去新外键的 `dataset`、`dataset_version` 计算为待拆除项目外键，导致
审计结果与当前正式迁移 head `z9f3a4b5c6d7` 不一致。

## 本轮处理

1. 将 `phase_one_migration_plan` 的第一段改为“历史兼容审计，不含活动项目外键
   拆除队列”；数据集和数据集版本仍保留可空 `project_id` 列，但不再由一期 ORM
   建立新 `project` 外键。
2. 迁移审计测试更新为当前正式 head `z9f3a4b5c6d7`、正式历史版本计数 `46`、
   当前实际项目外键表 `20`。
3. 不删除历史生产列、不修改正式 Alembic 迁移、不恢复任何项目菜单或写入口。

## 本地证据

```text
python -m pytest \
  labelu/tests/alembic/test_execution_lease_migration_audit.py \
  labelu/tests/alembic/test_phase_one_migration_plan.py \
  labelu/tests/alembic/test_phase_one_migration_rehearsal.py -q
9 passed
```

## 全量回归剩余项

本轮之前全量基线为 `2605 passed, 32 skipped, 42 failed`。本轮已消除其中
4 个迁移审计失败，剩余失败主要是：

- 已退役项目、规则中心和 `edge-device-jobs` 旧写链测试仍按历史成功状态断言；
- 第三方回流旧测试仍向已去项目 ORM 传入 `project_id`；
- 自动训练旧评估/持久化测试绕过一期只读开关。

这些不是本轮通过修改迁移断言可以解决的问题，下一批必须分别决定“迁移测试契约、
保留只读兼容、二期隔离套件”三种处理方式，不能恢复已取消的一期项目维度。

## 放行边界

- 本轮仅本地 L1，未连接生产 MySQL，未执行在线 DDL、备份恢复或部署。
- 正式生产外键和历史数据仍需在脱敏快照/独立 MySQL 沙箱中核对后，才可制定
  可执行的约束迁移方案。
- 未提交、未推送、未部署。
