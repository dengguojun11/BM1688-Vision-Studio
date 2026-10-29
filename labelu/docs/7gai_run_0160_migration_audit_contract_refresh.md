# RUN-0160 正式迁移 head 与项目外键归属审计契约刷新

日期：2026-09-19  
责任角色：R03 架构、R09 测试；R10 运维协作  
工作包：WP0 / U0B 迁移后缀重建离线闸门

## 本轮目标

修正迁移审计契约与当前代码图的漂移。只读取本地迁移文件和 ORM 元数据，不执行
Alembic DDL、不连接生产、不修改正式 migration 文件。

## 发现与处理

- 当前正式 Alembic 图的唯一 head 是 `z9f3a4b5c6d7`，不是旧审计常量
  `z9b0c1d2e3f4`。
- 生产基线 `h7i8j9k0l1m2` 之后实际有 46 个修订；补登记 `z9c0d1e2f3a4` 至
  `z9f3a4b5c6d7` 四个全局范围/兼容修订，并分别归入第 05、03、01 段。
- 当前 ORM 仍有 22 张表保留 `project` 外键。`dataset`、`dataset_version` 不能
  被审计误报为已经去项目化，现明确归属第 01 段，等待八段替代链移除外键。
- 审计测试的迁移总数、后缀映射数量和首段断言同步到上述事实；没有通过修改测试
  隐藏结构缺口。

## 本地修改

- `labelu/internal/common/phase_one_migration_rebuild_audit.py`
  - `CURRENT_LOCAL_HEAD` 更新为 `z9f3a4b5c6d7`；
  - 补齐四个后缀修订的对象级替代映射。
- `labelu/internal/common/phase_one_migration_plan.py`
  - 第 01 段显式拥有 `dataset`、`dataset_version` 的项目外键移除工作。
- `labelu/tests/alembic/test_phase_one_migration_plan.py`
  - 断言唯一 head、迁移总数 139、项目外键表 22；
  - 首段测试改为验证“已归属、尚未移除”的真实状态。
- `labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py`
  - 断言 46 个后缀映射、34 个替代、12 个排除。

## 验证证据

```text
python -m pytest labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py labelu/tests/alembic/test_phase_one_migration_plan.py -q --disable-warnings --tb=short
7 passed

python -m pytest labelu/tests/alembic/test_phase_one_candidate_skeleton.py labelu/tests/alembic/test_phase_one_candidate_chain_rehearsal.py labelu/tests/alembic/test_dispatch_lease_migration_candidate.py -q --disable-warnings --tb=short
12 passed

python -m alembic -c labelu/alembic_labelu/alembic.ini heads
z9f3a4b5c6d7 (head)
```

## 生产与上线边界

本轮为本地 L1。没有执行数据库迁移、MySQL 锁等待、生产快照升级、备份恢复、提交、
推送或部署。正式八段替代迁移仍只能在 `.tmp/phase-one-rebuild/versions` 隔离目录
演练，生产基线之后的旧后缀不能直接上线。下一步仍需同版本 MySQL 空库、脱敏生产快照、
表/列/索引/外键对账和恢复演练；在这些证据完成前，U0B 不得提升到生产放行级别。

