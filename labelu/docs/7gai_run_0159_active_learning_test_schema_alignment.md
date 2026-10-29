# RUN-0159：主动学习测试库结构与全局范围对齐

日期：2026-09-19（Asia/Shanghai）  
范围：持久化 SQLite 测试库、主动学习聚类/批次全局范围、结构兼容门禁

## 发现

联合回归发现持久化 `test.db` 是旧结构：`feedback_cluster.project_id` 和
`active_learning_batch.project_id` 仍为非空约束。虽然 ORM 和正式迁移已经把
项目列定义为历史兼容可空，旧测试库仍会在全局聚类写入时抛出
`NOT NULL constraint failed`。

## 处理

在 `labelu/tests/conftest.py` 增加测试库结构自检：如果两个主动学习表仍有旧的
非空项目约束，则仅重建测试库中的聚类、批次和批次项表，按当前 ORM 重新创建，
不修改生产数据库、不伪造项目编号。该逻辑只在测试数据库启动时执行，生产迁移
仍必须由正式 Alembic/MySQL 验收完成。

## 验收证据

```text
python -m pytest labelu/tests/internal/adapter/routers/test_active_learning.py labelu/tests/internal/adapter/routers/test_active_learning_global_scope.py labelu/tests/internal/adapter/routers/test_health.py -q --disable-warnings --tb=short
14 passed

python -m py_compile labelu/tests/conftest.py
通过

git diff --check
通过
```

## 风险与下一依赖

测试库重建不等于生产迁移通过。生产仍需在同版本 MySQL 和脱敏快照副本验证列
可空、索引、唯一约束、历史数据对账、锁等待和回滚备份；在这些证据完成前，主动
学习与自动训练只能保持本地 L1。

本轮未提交、未推送、未部署。
