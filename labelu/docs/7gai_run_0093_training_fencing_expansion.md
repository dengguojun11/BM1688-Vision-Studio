# RUN-0093 训练 Worker 中间与车牌结果 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和契约：主方案第 8、11、15.7 节；执行契约 `CONCURRENCY-EXEC-02`。
- 工作包和角色：WP2 训练与执行器；R04 后端主责，R06 模型算法、R03 架构、R07 边端、R09 测试协作。
- 本轮目标：把独立训练 Worker 的 owner/token 继续贯通到 YOLO 启动/取消/失败写入和车牌检测、车牌识别的多阶段组件与最终状态写入，阻止失租 Worker 覆盖新 Worker 的结果。

## 实际变更

- `yolo_train.py`：执行上下文不完整时失败关闭；YOLO 写入 `PROCESSING`、`CANCELLED`、`FAILED` 和车牌兼容入口的收尾状态前校验 owner/token/租约未过期。
- `plate_recognition_train.py`：组件状态、组件 PT/ONNX 制品、数据集摘要、最终权重/指标/完成状态及失败状态全部复用同一 guard；写入事务内再次校验，旧 token 不得覆盖组件 JSON 或训练任务状态。
- `yolo_train_queue.py`：车牌训练子进程接收并传递 `execution_fencing_token`、`execution_owner`，父进程在租约失效时终止子进程并拒绝异常收尾写入。
- `test_yolo_train_queue.py`：同步车牌子进程新增兼容参数契约。

## 本地证据

```text
python -m py_compile labelu/internal/application/service/yolo_train.py labelu/internal/application/service/plate_recognition_train.py labelu/internal/application/service/yolo_train_queue.py
通过

python -m pytest labelu/tests/internal/application/service/test_plate_recognition_train.py labelu/tests/internal/application/service/test_yolo_train_queue.py labelu/tests/internal/application/service/test_yolo_train_spawn.py labelu/tests/internal/application/service/test_yolo_train_worker.py -q --disable-warnings --tb=short
75 passed, 115 warnings

LABELU_TEST_DB_NAME=run0093-fresh python -m pytest labelu/tests/internal/application/service/test_yolo_train_stability.py labelu/tests/internal/application/service/test_yolo_train_summary.py labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_plate_recognition_train.py labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings --tb=short
95 passed, 117 warnings
```

## 并发和真实性边界

- guard 是数据库写入前的 owner/token/租约条件检查；它不替代 MySQL 行锁、执行租约续期、GPU 回收或 systemd 重启恢复。
- YOLO 的 epoch 进度主要落在结果文件和阶段日志中，用户触发的 test-val 后处理也尚未建立独立操作版本号；旧进程回写文件或并发评估的覆盖风险仍需下一轮处理。
- 仅完成隔离 SQLite、模拟子进程和本地专项测试；没有连接生产 MySQL、真实 GPU、TPU-MLIR、BM1688、S3、AICM 或生产训练队列。
- 独立训练 Worker 继续 fail-closed，不能据此切换生产消费路径；没有提交、推送或部署。

## 下一轮依赖

1. 为 epoch 中间指标、阶段文件和 test-val 评估结果增加独立操作世代/版本校验。
2. 将远程训练状态轮询和多阶段传输写入接入相同的执行边界。
3. 在同版本 MySQL 脱敏生产快照上完成双进程接管、租约续期、失租终止、重启恢复和 GPU 释放演练。

本轮结论：训练结果覆盖面从 RUN-0092 的数据集/最终 YOLO 写入扩展到车牌组件和训练生命周期写入，但仍为本地 L1，不能写成训练全链路或生产可用已完成。
