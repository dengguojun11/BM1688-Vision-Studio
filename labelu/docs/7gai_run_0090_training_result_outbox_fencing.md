# RUN-0090 训练结果 Outbox fencing 传递补齐

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第 8 章训练中心、第 14.6.3 节后台执行器与租约；训练结果一致性契约 `TRAIN-RESULT-FENCE-01`。
- 工作包/角色：WP2 训练与执行器；R04 后端主责，R03 架构、R06 模型算法、R09 测试协作。
- 本轮目标：补齐训练 Worker 结果事件由 Outbox 消费时的 fencing 检查传递，防止旧 Worker 结果在事件消费阶段修改训练作业状态、指标或远端状态。

## 变更范围

1. `handle_outbox_event()` 对 `training.worker.result.v1` 事件调用训练结果持久化函数，并把消费器传入的 `fencing_check` 原样传递。
2. `process_worker_outbox_batch()` 和结果事件消费路径继续使用同一个 owner/token 检查；旧 token 事件可以被消费并完成幂等处理，但不能写入训练作业。
3. 新增回归测试：fencing 检查失败时，训练作业保持原状态，`remote_status_json` 不产生结果、指标或 Worker token 的变更。

## 本地证据

```text
python -m pytest labelu/tests/internal/application/service/test_training_worker_persistence.py labelu/tests/internal/application/service/test_training_worker_runtime.py labelu/tests/internal/application/service/test_yolo_train_worker.py -q --disable-warnings --tb=short
29 passed, 112 warnings, 17.76s

python -m py_compile labelu/internal/application/service/auto_training_reconciliation.py labelu/internal/application/service/training_worker_runtime.py labelu/internal/application/service/training_worker_persistence.py labelu/internal/common/training_worker.py labelu/scripts/training_worker.py
通过

git diff --check -- labelu/internal/application/service/auto_training_reconciliation.py labelu/tests/internal/application/service/test_auto_training_reconciliation.py
通过（仅保留既有 LF/CRLF 转换提示）

python -m pytest labelu/tests/internal/application/service/test_auto_training_reconciliation.py labelu/tests/internal/application/service/test_auto_training_evaluator.py labelu/tests/internal/application/service/test_auto_training_scheduler_policy.py -q --disable-warnings --tb=short
28 passed, 112 warnings, 6.55s
```

## 真实性边界

- 这是结果事件消费阶段的 fencing 传递修复，不代表 YOLO/车牌训练的状态、进度、权重路径、评估结果和制品写入已经全部完成 owner/token 条件化。
- 独立训练 Worker 仍保持生产 fail-closed；当前不承接 YOLO/车牌 GPU 训练，Web 兼容路径不能据此宣称已被安全替换。
- 本轮未连接生产 MySQL、真实 GPU、AICM、S3、TPU-MLIR 或 BM1688，未执行 systemd 重启、双进程接管、真实训练或生产写入。
- 本轮只达到本地 L1；未提交、未推送、未部署。生产放行仍以真实迁移、执行器隔离、外部链路和 BM1688 真机证据为准。

## 后续验收

1. 在同版本 MySQL 脱敏生产快照上验证旧 token 结果事件、租约过期接管和结果幂等。
2. 完成 YOLO/车牌训练全链路状态与制品写入 fencing，再进行独立 Worker 灰度。
3. 补齐 systemd 重启、GPU 释放、真实训练结果、AICM/S3、TPU-MLIR 和 BM1688 设备证据。
