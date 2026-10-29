# RUN-0137 BM1688/边端执行期租约续租与 fencing 收口

## 1. 本轮目标

按角色方案继续收口 BM1688 编译和边端验证的执行期租约边界。`queue_lease_*` 只负责把任务交给 Worker；`execution_lease_*`、fencing token 和条件写入负责保护实际编译、SSH、上传、结果回传和终态写入。本轮重点验证：

1. 续租只允许当前 owner + fencing token + 未过期租约；
2. 旧 Worker 被接管后不能继续写入；
3. 执行期租约失效后由恢复扫描回队，不能由旧 Worker 越权回写；
4. 执行租约字段未完成迁移时，独立 BM1688 Worker 不得领取或编译。

## 2. 实际发现与最小修复

### 2.1 边端心跳此前未受 fencing 保护

边端长阶段心跳线程原来直接写 `updated_at`。旧 Worker 失租后仍可能刷新时间戳，使恢复扫描把旧任务误判为“仍在运行”，延迟 `PROCESSING -> PENDING` 回队。

已修改 `labelu/internal/application/service/yolo_edge_validation.py`：

- `_touch_manual_job_heartbeat()` 接受可选 `ExecutionLeaseMonitor`；
- 有执行上下文时通过 `conditional_update()` 写心跳，条件包含记录 ID、owner、fencing token 和未过期租约；
- 心跳续租/条件写入遇到 `ExecutionFencingLost` 时停止触碰数据库，只记录跳过；
- 所有样本包、上传、推理、回传和 PT 对比长阶段复用带执行监控器的心跳；
- 没有执行上下文的历史 Web 兼容入口仍保留原心跳行为。

### 2.2 BM1688 独立 Worker 增加迁移就绪门禁

已修改 `labelu/internal/application/service/bm1688_worker_runtime.py`：

- 新增 `execution_fencing_ready()`，用 Worker 自己的数据库会话检查 ORM 字段和实际表列；
- 缺少队列交接字段或 `execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token` 任一字段时，本轮返回 `ready=false`；
- 未就绪时不查询候选、不领取队列租约、不启动 PT/ONNX/BModel 编译；
- `--once` 未就绪返回失败码，循环模式保持等待；
- 结果增加 `ready` 和中文 `reason`，便于部署探针记录原因。

本轮没有把 Web 旧人工编译路径强制切换到新门禁，也没有修改正式 Alembic 迁移。生产数据库尚未完成同版本 MySQL 结构验收时，独立 BM1688 Worker 必须保持关闭。

## 3. 条件写入和回队关系

执行期 fencing 的职责边界如下：

| 场景 | 允许的写入者 | 处理方式 |
|---|---|---|
| 调度派发 | 持有 `queue_lease_*` 的调度器/Worker | 只完成短时交接，不代表可以写编译结果 |
| 编译/边端执行中 | 持有有效 `execution_lease_*` 和 token 的 Worker | 进度、制品、结果、终态必须条件写入 |
| Worker 失租 | 旧 Worker | `ensure_current()` 或条件写入失败，停止后续副作用 |
| 失租回队 | 恢复扫描器/新 Worker | 观察到租约过期后将 BM1688 `PROCESSING` 回到 `PENDING`；边端通过调度器重新排队并释放设备占用 |
| 迁移字段缺失 | 任意独立 Worker | 失败关闭，不领取、不连接设备、不编译 |

旧 Worker 不直接把任务改成新 Worker 的终态；这样可以避免旧进程在租约接管后覆盖新进程的结果。回队是恢复扫描器的职责，而不是失租进程越权写入。

## 4. 本地专项验证

测试命令：

```text
python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short
```

结果：`26 passed`，零失败。

覆盖内容：

- BM1688 两进程队列领取唯一性、过期队列租约接管和有效执行租约拒绝；
- BM1688 执行租约字段缺失时 Worker `ready=false`、零候选、零副作用；
- 边端心跳使用 execution monitor 的条件写入；
- 边端租约字段不可用时任务回到 `PENDING` 并释放设备地址；
- 旧 token 条件写入拒绝、租约续期和接管逻辑（复用既有执行 fencing 专项）。

## 5. 并发、生产与迁移边界

- 本轮只完成本地 SQLite/多进程模拟和服务级专项，未连接生产数据库；
- 未执行同版本 MySQL 的锁等待、20 并发、长事务、进程重启和真实接管；
- 未连接真实训练节点、TPU-MLIR、BM1688 设备、S3 或 AICM；
- 未执行正式 Alembic DDL，未修改生产表；
- 生产已有旧路径和迁移漂移问题仍以 `RUN-0134`、`RUN-0135` 的阻断结论为准。

验收等级：本地 L1。生产不能据此开启独立 BM1688/边端 Worker，也不能把本地测试结果写成真机验证完成。

## 6. 变更文件与交付状态

- `labelu/internal/application/service/yolo_edge_validation.py`
- `labelu/internal/application/service/bm1688_worker_runtime.py`
- `labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py`
- `labelu/tests/internal/application/service/test_bm1688_worker_runtime.py`
- `labelu/docs/7gai_run_0137_execution_fencing.md`

本轮未提交、未推送、未部署，等待用户明确“提交代码”。
