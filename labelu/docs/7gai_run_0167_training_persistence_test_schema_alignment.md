# RUN-0167 训练持久化专项测试库结构对齐

日期：2026-09-20  
责任角色：R03 架构、R04 后端、R06 模型算法、R09 测试  
工作包：WP2/WP5

## 问题

训练 Worker 持久化专项有两个用例在写入 `Dataset` 时失败，错误为
`table dataset has no column named project_id`。原因是持久化 pytest 数据库曾由
项目去化迁移演练重建为无 `project_id` 的表，而当前 ORM 仍保留可空的历史兼容列。

## 处理

- 在 `labelu/tests/conftest.py` 的测试启动兼容层中，检测 `dataset.project_id` 缺失时补建
  可空 `INTEGER` 列。
- 该列只作用于测试数据库，未修改生产 ORM、正式 Alembic 迁移或业务接口；一期新写入仍不使用
  项目维度。
- 保留 `dataset_version` 既有兼容列处理，确保冻结版本和模型血缘专项可以在同一测试库重复运行。

## 验证

```text
python -m pytest -q labelu/tests/internal/application/service/test_training_worker_persistence.py
12 passed

python -m pytest -q \
  labelu/tests/internal/application/service/test_training_worker_runtime.py \
  labelu/tests/internal/application/service/test_training_worker_persistence.py \
  labelu/tests/internal/application/service/test_yolo_train_phase_generation.py \
  labelu/tests/internal/application/service/test_yolo_train_frozen_manifest.py \
  labelu/tests/internal/application/service/test_bm1688_worker_runtime.py \
  labelu/tests/internal/application/service/test_edge_validation_worker.py \
  labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py \
  labelu/tests/internal/application/service/test_provider_worker.py \
  labelu/tests/internal/application/service/test_legacy_s3_adapter.py
68 passed, 112 warnings in 252.18s（修复后）
```

修复前的两个失败均为测试数据库列缺失；修复后训练持久化专项 12 项和混合 Worker 套件
68 项全部通过。警告主要来自当前依赖的 Pydantic/SQLAlchemy 弃用提示，不是本轮新增失败。

## 边界

- 本轮没有连接生产数据库，没有执行正式迁移、提交、推送或部署。
- 测试库兼容列不代表生产已完成第 01 段迁移；生产仍需同版本 MySQL 脱敏快照、结构/行数对账、
  锁等待和备份恢复证据。
