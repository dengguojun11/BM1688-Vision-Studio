# RUN-0086 独立训练 Worker fencing 最小切片

## 1. 分析结论

独立 Worker 的父进程领取、执行租约续期、`PROCESSING/FAILED` 条件写入和终态清理
已经使用 execution owner/token。训练内部的 YOLO、车牌状态、进度、结果和制品写入
仍散落在旧 Web 训练函数中，若一次性改造会同时影响普通训练、暂停/继续、断点恢复、
评估和模型目录生成，不能在本轮扩大范围。

子进程启动后，旧代码只写 `phase.json`，数据库中的 `execution_runtime_pid` 仍为空，
导致 Worker 无法把当前受租约保护的运行进程暴露给运维和接管逻辑。

## 2. 本轮最小实现

- 独立 Worker 默认 runner 增加 `runtime_update` 回调，由当前
  `ExecutionLeaseMonitor.conditional_update` 按 owner/token 条件写入
  `execution_runtime_pid`。
- Worker 中 Python 标准库 `inspect` 与 SQLAlchemy `inspect` 使用显式别名，避免回调签名检查在真实启动时被错误覆盖。
- YOLO 和车牌训练子进程启动后先执行该条件写入，再记录运行阶段。
- 条件写入失败时立即终止已启动的子进程并等待退出，禁止无租约训练继续运行。
- 旧 Web 训练路径不传 `runtime_update`，保持原有行为。
- 自定义测试 runner 通过参数签名兼容，不影响隔离演练。

## 3. 未迁移写入与 fail-closed 边界

以下写入仍在旧训练函数中，尚未接入 execution token：训练状态切换、进度日志、
`weights_path`、训练评估结果、车牌多阶段制品路径和模型目录登记。由于这些写入
不能仅靠父 Worker 的 token 防止旧子进程覆盖，`dedicated_worker_ready()` 继续返回
不可用，独立 Worker 不会切走 Web 消费路径。

## 4. 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_queue.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short
```

结果：`45 passed`。覆盖租约回调传递、运行 PID 条件写入、条件写入失败终止子进程、
租约失效终止训练进程和原有 Worker 领取并发契约。

## 5. 环境与交付边界

- 未连接生产数据库、GPU 节点、BM1688、S3、AICM 或真实训练环境。
- 未提交、未推送、未部署；等待用户明确授权。
- 下一步必须逐类迁移训练状态/结果/制品写入到 token 条件更新，并完成真实 MySQL、
  双进程接管、断电、重复制品和 GPU 释放验证后，才能打开独立 Worker 开关。
