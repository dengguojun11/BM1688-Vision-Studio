# 7GAI 独立 Worker 切分与迁移方案

## 1. 当前生产代码入口审计

| 能力 | 当前入口 | 当前执行方式 | 独立 Worker 结论 |
| --- | --- | --- | --- |
| 训练 | `yolo_train._submit_yolo_train_job` -> `yolo_train_queue.request_train_start` -> `training.worker.command.v1` Outbox | API 只创建训练命令；训练 Worker 通过 Outbox 记录租约、心跳和结果 fencing，最终启动本地训练子进程或远程训练请求 | 以 Outbox Worker 为目标主路径；保留 API 入队兼容，不再把训练表扫描描述为当前事实 |
| BM1688 编译 | `auto_training_dispatch.dispatch_bm1688_export` -> `_run_bm1688_export_sync` | 调度器通过 `threading.Thread` 启动；自动派发使用 `queue_lease_*`，执行函数已接入 execution fencing | 可立即切出 BM1688 Worker；Worker 领取并提交 queue lease 后再执行 |
| 边端验证 | `dispatch_edge_validation_job` -> `_run_manual_job_sync` | 调度器先在当前会话准备样本包，再用线程启动 SSH/板端执行 | 不能直接照搬 BM1688；必须先拆成“样本包准备事务”和“执行事务”，再切 Worker |
| 自动恢复 | `yolo_bm1688_export_auto_resume.py`、`yolo_edge_validation_auto_resume.py` | Web 进程内 watchdog 扫描并启动线程 | 切换期间保留为兼容恢复入口，不能和独立 Worker 同时重复执行 |

本次审计没有连接生产数据库、TPU-MLIR、BM1688 或边端设备。结论来自本地代码路径，不代表线上已完成切换。

## 2. 最小切分边界

### 2.1 API 进程保留的职责

API 只负责创建任务、写入 `PENDING/QUEUED`、保存请求参数和发送可重试事件，不直接运行训练、编译或 SSH。现有 API 不能立即删除线程入口，迁移期通过配置开关关闭自动线程派发，手工作业仍按兼容路径运行。

### 2.2 Worker 进程职责

每个 Worker 只处理一种执行能力，并使用独立数据库会话：

1. 训练 Worker：消费 `training.worker.command.v1` Outbox，原子领取命令记录并维护记录租约/心跳，提交事务后调用现有训练子进程入口；训练结果仍由现有状态机回写。训练表上的历史 `PENDING` 扫描只作为迁移兼容或恢复边界，不能作为新主链路描述。
2. BM1688 Worker：扫描自动 BM1688 导出，先提交派发租约，再调用 `_run_bm1688_export_sync(export_id)`；执行函数负责 execution fencing、续租和条件终态写入。
3. 边端 Worker：第一阶段只消费已经完成样本包的边端任务；样本包准备必须在独立事务完成并提交后，才允许 Worker 领取和调用 `_run_manual_job_sync(job_id)`。

### 2.3 不可混用的租约

- `queue_lease_*`：调度器把记录交给哪个 Worker，短租约，提交后才允许启动执行。
- `execution_lease_*` + `execution_fencing_token`：Worker 在编译、上传、SSH、结果导入期间的实际写入保护。
- `lease_expires_at`（边端设备字段）：设备分配租约，不能作为 Worker 进程租约。

## 3. Worker 命令协议

Worker 不新增业务表，先复用现有三张任务表。每次执行在日志中打印一条 JSON 命令：

```json
{
  "协议版本": "7gai.worker.v1",
  "命令编号": "bm1688-export-123",
  "能力": "bm1688_export",
  "记录编号": 123,
  "派发租约持有者": "bm1688-worker:主机:进程",
  "执行围栏令牌": 4,
  "尝试次数": 1
}
```

`命令编号 = 业务记录编号 + execution_signature`。同一命令重复投递时，只允许一个 Worker 成功领取；重复终态写入必须被 owner/token/未过期条件拒绝。Worker 进程退出时不主动把记录改成失败，交给过期租约恢复扫描处理。

## 4. 迁移顺序

1. 先部署独立 Worker，但只读扫描并输出候选记录，验证查询、索引和并发耗时。
2. 开启 BM1688 Worker 灰度；关闭 BM1688 Web 线程自动派发，保留手工按钮兼容入口。
3. 迁移训练 Worker；确认 GPU/远程节点资源队列的原有限流和停止标记不变。
4. 完成边端样本包事务拆分后再开启边端 Worker。
5. 最后关闭对应 watchdog 的执行启动功能，只保留恢复和状态同步。

## 5. systemd 最小运行约定

每种能力单独一个服务，服务只启动一个常驻进程；进程内部使用短轮询（建议 2 秒）和数据库条件领取，不使用 Web 端口。服务停止时不删除租约，让恢复扫描接管。

```text
labelu-train-worker.service
labelu-bm1688-worker.service
labelu-edge-worker.service
```

首期不要求引入 RabbitMQ/Kafka；数据库条件领取和现有 outbox 足以完成单机/少量 Worker 的迁移。达到 20 并发或跨机吞吐瓶颈后，再评估消息队列，不在本轮引入第二套状态机。

## 6. 验收标准

- API 请求不再因训练、TPU-MLIR、SSH 长任务占用 Web 线程。
- 双 Worker 同时扫描同一记录时只有一个 `queue_lease_owner` 成功，另一个不启动副作用。
- Worker 接管后旧 Worker 的续租、进度、制品和终态写入全部被 execution fencing 拒绝。
- 进程重启后，过期 queue lease 可被新 Worker 接管；未过期租约不能被抢占。
- 边端样本包未提交时，Worker 不得开始 SSH 上传或设备执行。
- 先完成本地 SQLite 双进程演练，再做空 MySQL 和生产快照副本验证，最后才允许预生产真实设备灰度。

## 7. BM1688 systemd 运维配置

部署脚本已预留 `labelu-bm1688-worker.service`，但生产默认关闭。只有正式迁移、数据库字段和预生产验证全部放行后，才可在运行环境配置：

```text
LABELU_ENABLE_BM1688_WORKER=1
LABELU_BM1688_WORKER_INTERVAL=2
LABELU_BM1688_WORKER_LEASE_SECONDS=300
LABELU_BM1688_WORKER_BATCH_LIMIT=1
```

部署脚本开启该服务时，会同时给 API 和 Worker 注入 `LABELU_BM1688_DEDICATED_WORKER_ENABLED=true`。API 自动派发器随即停止启动 Web 编译线程，独立 Worker 成为自动 BM1688 导出的唯一启动方。关闭 `LABELU_ENABLE_BM1688_WORKER` 后，部署脚本会停用并删除该 unit，API 恢复原有兼容派发路径。

unit 使用独立 TMPDIR、限定可写目录、`NoNewPrivileges`、`ProtectSystem` 和 `ProtectHome`。部署检查清单必须同时确认 Worker 入口存在、参数为正数、unit 已安装且服务处于运行状态。该配置不代表生产已经启用；本地阶段只验证 unit 生成契约，不执行 `systemctl`。
