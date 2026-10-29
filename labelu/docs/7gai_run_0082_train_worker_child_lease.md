# RUN-0082 训练 Worker 子进程租约闭环

## 1. 当前缺口

独立训练 Worker 已能领取 `YoloTrainJob`、持有执行租约并由
`ExecutionLeaseMonitor` 续租，但原训练子进程使用无超时等待。租约被其他 Worker
接管时，旧训练进程可能继续消耗 GPU 并写入训练目录。

## 2. 本轮最小闭环

- Worker 默认运行器将 `monitor.ensure_current` 作为租约检查传入 YOLO/车牌子进程等待函数。
- 训练子进程等待改为每 0.5 秒轮询；租约失效时终止并等待子进程退出。
- Worker 不再在失租后写入成功终态；失败写入仍经过当前 token 条件保护。
- 旧 Web 训练路径不传租约检查参数，保持现有兼容行为；独立 Worker 路径才启用失租终止。
- 自定义测试运行器保留旧参数签名兼容。

## 3. 并发与安全边界

子进程终止发生在父进程发现 fencing 丢失之后，不能替代数据库条件写入；训练内部历史状态、进度和制品写入尚未全部携带 token。因此 `dedicated_worker_ready()` 仍然强制 fail-closed，不能据此开启独立训练 Worker。

## 4. 本地证据

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_queue.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short
41 passed
```

新增测试覆盖租约回调传递和失租终止；未连接真实 GPU、MySQL、Ubuntu systemd 或生产队列。

## 5. 状态

达到本地 L1；完整训练内部 token 化、真实跨进程接管、生产迁移和部署仍未完成。未提交、未推送、未部署，等待用户明确授权。
