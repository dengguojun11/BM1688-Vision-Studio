# RUN-0094 test-val 评估结果乐观并发 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和契约：主方案第 8、11、15.7 节；评估结果一致性契约 `EVAL-VERSION-FENCE-01`。
- 工作包和角色：WP2 训练与执行器；R04 后端主责，R06 模型算法、R03 架构、R09 测试协作。
- 本轮目标：防止两个 test-val 评估或训练状态变更并发完成时，旧评估结果覆盖当前训练作业的最新 `metrics_summary`。

## 实际变更

- `crud_yolo_train_job.py` 新增条件更新函数：只有作业仍为 `COMPLETED`，且 `metrics_summary` 等于评估开始时读取的摘要，才允许写入；不需要新增数据库字段或迁移。
- `yolo_train.py` 的 test-val 结果加入唯一评估操作编号，并改用上述条件更新；条件失败只记录“旧评估结果已丢弃”，保留数据库当前结果。
- 新增专项测试，覆盖同一源摘要只能成功写一次，以及作业已重新进入训练状态时拒绝旧评估结果。

## 本地证据

```text
LABELU_TEST_DB_NAME=run0094-fresh python -m pytest labelu/tests/internal/application/service/test_yolo_test_val_fencing.py labelu/tests/internal/application/service/test_yolo_train_summary.py labelu/tests/internal/application/service/test_yolo_train_stability.py -q --disable-warnings --tb=short
27 passed, 114 warnings

LABELU_TEST_DB_NAME=run0094-full-fresh python -m pytest labelu/tests/internal/application/service/test_yolo_quality_evidence_auto_recovery.py labelu/tests/internal/application/service/test_yolo_train_evaluation_dataset_evidence.py labelu/tests/internal/application/service/test_yolo_train_analysis.py labelu/tests/internal/adapter/routers/test_yolo_train_analysis.py -q --disable-warnings --tb=short
27 passed, 113 warnings

python -m py_compile labelu/internal/adapter/persistence/crud_yolo_train_job.py labelu/internal/application/service/yolo_train.py labelu/tests/internal/application/service/test_yolo_test_val_fencing.py
通过
```

## 并发和真实性边界

- 这是应用层乐观版本条件，不是 MySQL 行锁、分布式锁或独立评估 Worker；SQLite 专项不能证明生产锁等待和高并发吞吐。
- 训练 epoch 阶段文件、远程训练轮询状态和真实执行器租约仍未全部纳入同一执行世代；本轮只收口 test-val 的数据库汇总写入。
- 未连接生产 MySQL、真实 GPU、TPU-MLIR、BM1688、S3、AICM 或生产训练队列；未提交、未推送、未部署。

## 下一轮依赖

1. 在同版本 MySQL 脱敏快照上验证条件更新执行计划、锁等待和双评估竞争。
2. 为 epoch/阶段文件增加操作世代标识，避免旧进程回写旧阶段文件。
3. 将远程训练状态和多阶段制品导入纳入 owner/token 条件写入。

本轮结论：test-val 数据库结果已达到本地 L1 并发写入边界；不能据此宣称评估执行器或生产训练链路已完成。
