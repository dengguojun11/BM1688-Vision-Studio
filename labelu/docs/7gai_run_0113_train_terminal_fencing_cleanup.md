# RUN-0113 训练子进程终态与阶段文件 fencing 收口

## 1. 本轮目标

继续完成独立 YOLO/车牌训练 Worker 的安全切换前置条件。重点修复训练子进程异常退出和暂停收尾时 owner/token 丢失的问题，避免旧 Worker 在租约接管窗口写入新的 `phase.json` 或错误终态。

本轮只做本地代码和隔离测试，不连接生产，不打开独立训练 Worker，不执行真实 GPU、TPU-MLIR 或 BM1688 训练。

## 2. 交付内容

- `yolo_train._mark_spawn_child_failed()` 的失败阶段文件写入现在继续传递 `execution_owner` 和 `execution_fencing_token`。
- 训练最终评估发现停止/取消时，`_finalize_yolo_train_job_sync()` 调用 `_mark_job_cancelled()` 时保留当前执行租约上下文。
- 旧 Web/无租约路径仍保持历史兼容；只有独立 Worker 带 owner/token 时启用严格 fencing。
- 车牌训练已有的组件状态、PT/ONNX 制品世代校验不改动，继续复用 RUN-0101 的边界。

## 3. 为什么必须修

训练状态数据库写入已经按 owner/token 做条件保护，但阶段文件是页面实时展示和恢复判断的依据。如果失败阶段写入不带 token，旧进程可能在新 Worker 接管后覆盖页面阶段；如果暂停收尾不带 token，独立 Worker 的有效租约又会被旧兼容路径拒绝或产生不一致状态。本轮把这两个写入点统一到同一执行世代。

## 4. 本地验证

```text
python -m pytest \
  labelu/tests/internal/application/service/test_yolo_train_stability.py \
  labelu/tests/internal/application/service/test_yolo_train_worker.py \
  labelu/tests/internal/application/service/test_plate_recognition_train.py \
  -q --disable-warnings --tb=short
```

结果：`58 passed, 115 warnings`，耗时约 118 秒。覆盖：

- 旧 token 失败收尾不写阶段、不改终态；
- 新 token 的失败阶段写入带 owner/token；
- 训练 Worker 领取、租约和 20 进程本地竞争；
- YOLO 生命周期、暂停/完成/异常收尾；
- 车牌检测/识别组件状态、制品和恢复契约。

附加检查：

- `python -m py_compile labelu/internal/application/service/yolo_train.py labelu/internal/application/service/plate_recognition_train.py labelu/internal/application/service/yolo_train_worker.py` 通过；
- `git diff --check` 通过。

## 5. 验收结论

- 本地：L1 通过。
- 预生产/生产：未验收。
- 独立训练 Worker：继续 `fail-closed`。
- 菜单、按钮、用户角色、项目维度、API、数据库字段和模型制品格式：本轮均未新增。

## 6. 仍需的真实证据

1. 同版本 MySQL 双进程领取、锁等待、旧 token 拒绝和重启接管。
2. Ubuntu/systemd 重启后的 GPU 进程终止、GPU 槽位释放和共享目录清理。
3. 真实 GPU 训练节点和车牌组合训练节点。
4. 训练成功后的真实 PT、ONNX、三种 BModel、BM1688 真机和 AICM 链路。

## 7. 交付状态

本轮未提交、未推送、未部署，等待用户明确“提交代码”。
