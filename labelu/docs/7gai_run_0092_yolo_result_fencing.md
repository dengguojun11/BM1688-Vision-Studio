# RUN-0092 YOLO 训练结果写入 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第8章训练中心、第11章异步执行、第15.7节并发验收；执行契约 `CONCURRENCY-EXEC-02`。
- 工作包/角色：WP2训练与执行器；R04后端主责，R06模型算法、R03架构、R09测试协作。
- 本轮目标：将独立训练 Worker 的 owner/token 从领取层传递到 YOLO 子进程，并在数据集快照和最终训练结果写入前执行 owner/token/未过期校验。

## 变更内容

- `yolo_train_worker.py` 将执行租约 token 和 owner 传给默认 YOLO 运行器；兼容旧测试运行器，只有签名支持时才传入新增参数。
- `yolo_train_queue.py` 将 token/owner 传入 YOLO 子进程；没有 fencing 上下文时保持旧进程参数列表和旧 Web 路径不变。
- `yolo_train.py` 新增训练结果写入前的租约校验：数据集快照字段和最终 `weights_path`、`metrics_summary`、`COMPLETED` 写入必须匹配当前 owner/token 且租约未过期；失租抛出 `TrainExecutionFenceLost`，不写失败或完成状态。
- 车牌检测/识别训练仍保持现有路径，尚未接入这套内部结果写入 fencing；独立 Worker 继续 fail-closed，不切换生产消费路径。

## 本地证据

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings --tb=short
44 passed, 112 warnings

python -m pytest labelu/tests/internal/application/service/test_yolo_train_stability.py labelu/tests/internal/application/service/test_yolo_train_summary.py labelu/tests/internal/application/service/test_yolo_train_evaluation_dataset_evidence.py -q --disable-warnings --tb=short
31 passed, 114 warnings

python -m py_compile labelu/internal/application/service/yolo_train.py labelu/internal/application/service/yolo_train_queue.py labelu/internal/application/service/yolo_train_worker.py
通过
```

## 真实性边界

- 只证明本地 SQLite/模拟子进程下的 YOLO 结果写入边界；未连接生产 MySQL、真实 GPU、systemd、S3、AICM、TPU-MLIR 或 BM1688。
- 训练过程中的全部中间进度、日志文件和车牌多阶段制品仍未全部 token 化；不能据此打开独立训练 Worker 或宣称训练全链路完成。
- 尚未进行 MySQL 双进程接管、租约过期、重启恢复和 GPU 释放演练。
- 未提交、未推送、未部署，等待用户明确说“提交代码”。

## 下一依赖

1. 将同一 guard 复用到 YOLO 中间进度和评估结果写入。
2. 为车牌检测/识别训练建立等价的 owner/token 结果写入边界。
3. 在同版本 MySQL 脱敏生产快照上执行双进程接管和重启演练，再评估独立 Worker 灰度。
