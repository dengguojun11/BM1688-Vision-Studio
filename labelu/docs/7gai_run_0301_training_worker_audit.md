# RUN-0301 训练 Worker 独立执行与生产边界审计

## 1. 本轮目标

按角色团队的训练执行工作包，复核 YOLO/车牌训练 Worker 是否具备独立进程执行、输入持久化、执行租约与 fencing、重启接管、失败关闭和并发限制；只做本地代码与隔离测试，不连接生产、不提交、不推送、不部署。

## 2. 代码结论

### 2.1 YOLO/车牌专用 Worker 已形成可运行的本地闭环

入口为 `labelu/scripts/yolo_train_worker.py`，核心实现为 `yolo_train_worker.run_worker_once()`：

1. API 在独立 Worker 开关满足前置条件时只把作业留在 `PENDING`，不回落到 Web 进程线程执行。
2. Worker 使用 `queue_lease_owner/queue_lease_expires_at` 条件更新领取作业，租约过期后可被新 Worker 接管。
3. Worker 领取后再取得 `execution_lease_owner/execution_lease_expires_at/execution_fencing_token`，实际训练状态、运行进程号、阶段文件和终态写入都必须匹配 owner/token 且未过期。
4. Worker 通过 `ExecutionLeaseMonitor` 定期续租；续租失败会终止训练子进程，旧 Worker 不得写入失败、完成或进度状态。
5. GPU/CPU 资源由既有资源档案和 `YOLO_TRAIN_DEDICATED_WORKER_BATCH_LIMIT` 控制；训练作业按数据库状态和 GPU 槽位限制并发。
6. Worker、父训练子进程、YOLO/车牌训练入口复用同一份已持久化的 `YoloTrainJob` 输入：权重引用、训练轮数、图片尺寸、超参、来源任务、训练配方和冻结数据清单。Worker 重启不依赖 API 进程内存队列。

### 2.2 两条 Worker 链路必须分开描述

`training.worker.command.v1` Outbox 是通用算法/容器训练适配链，当前用于自动训练和非 YOLO 容器适配器；YOLO/车牌真实训练由 `yolo_train_worker` 消费 `YoloTrainJob` 持久化队列并启动现有 YOLO/车牌子进程。两者都使用租约和结果 fencing，但不是同一个消费者，不能在菜单、部署或验收文档中宣称“一个 Worker 同时覆盖两条链”。

### 2.3 统一训练入口复核结论

二次核对 `labelu/internal/adapter/routers/training.py` 后确认：统一训练入口
在 `adapter_key == "yolo_detection"` 时直接委托现有
`yolo_train_service.create_train_job`，不会把 YOLO 作业写入通用 Outbox。
通用 `training.worker.command.v1` 只覆盖非 YOLO 的容器型算法适配器。因此本轮
不存在“YOLO 训练入队后无人执行”的已确认缺陷；保留的要求是两类 Worker 的
状态、部署开关和验收证据必须分开记录。

## 3. 本地验证

执行命令：

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_queue.py -q
```

结果：`54 passed`。覆盖：

- 同一作业多进程领取只有一个成功；
- 过期队列租约接管；
- 释放操作按 owner 隔离；
- 独立 Worker 默认关闭及数据库字段未就绪时失败关闭；
- 运行器透传 owner/token、租约检查和运行进程号；
- 旧 token、有效租约下的无上下文写入和旧回收状态写入被拒绝；
- 20 个独立进程竞争同一训练作业只有一个获得队列租约。

通用 Outbox 链路另行执行：

```text
python -m pytest labelu/tests/internal/application/service/test_training_worker_runtime.py labelu/tests/internal/application/service/test_training_worker_persistence.py -q --disable-warnings --tb=short
```

结果：`25 passed`。覆盖容器命令白名单、命令重试、Outbox 租约心跳、结果幂等、模型血缘和旧 fencing 结果拒绝。

另行确认：`python -m compileall -q labelu/internal` 和 `git diff --check` 应在父工作包收口时统一执行；本轮不修改共享工作树中其他角色的未提交文件。

## 4. 尚未满足的生产开启条件

以下证据缺失前，生产 `YOLO_TRAIN_DEDICATED_WORKER_ENABLED` 必须保持关闭：

- 同版本 MySQL/InnoDB 双 Worker 锁等待、过期接管和 fencing 竞争；SQLite 多进程测试不能替代该证据；
- Ubuntu/systemd 实际启动、停止、重启接管、训练子进程终止和 GPU 释放；
- 真实 GPU、YOLO11/车牌训练、显存阈值、多卡和长任务续租；
- 训练作业的真实冻结数据、PT 产物、后续 ONNX/BModel 编译、BM1688 真机验证；
- 生产配置、日志、告警和两类账号页面验收。

## 5. 后续实施边界

1. 生产迁移时先执行数据库结构与索引只读检查，再以单 Worker、小批量开启；
2. Worker 未就绪时训练作业可以创建并保持 `PENDING`，但不能由 Web 线程兜底，防止双消费者重复训练；
3. 外部发布后的模型不做自动回滚；发现问题时只禁止本平台后续下载，并发布替代版本；
4. 本轮未进行任何生产写入，未提交、未推送、未部署。
