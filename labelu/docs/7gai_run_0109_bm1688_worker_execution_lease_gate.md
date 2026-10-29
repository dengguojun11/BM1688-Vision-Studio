# RUN-0109 独立 BM1688 Worker 执行租约领取门禁

## 1. 本轮目标

RUN-0108 已覆盖 API/调度服务的编译派发和恢复领取，但独立 `bm1688_worker` 仍只按 queue lease 扫描和领取。有效编译执行器存在时，旧 Worker 可能先拿到 queue lease，再在真正编译入口被拒绝，造成无效竞争和误导性的 Worker 统计。本轮把 execution lease 条件下沉到独立 Worker 自身的候选和领取 SQL。

## 2. 实现内容

- `bm1688_worker_runtime._candidate_ids()` 只扫描没有有效 execution lease 的自动 BM1688 导出任务。
- `bm1688_worker_runtime._claim_export()` 在单条条件更新中同时检查 queue lease 和 execution lease，接管窗口内不能领取有效执行者的任务。
- 保留真正 `_run_bm1688_export_sync()` 的 `claim_execution_lease()`；Worker 队列条件是前置减少竞争，不能替代执行期 fencing。
- 双进程隔离演练表增加执行租约字段，新增有效执行租约下零候选、零领取、零编译副作用测试；不新增菜单、用户角色、项目维度、API 或生产表结构。

## 3. 验收证据

独立 Worker 专项：

```text
python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py -q --disable-warnings --tb=short
```

结果：`8 passed`。

合并执行/派发/恢复回归：

```text
python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/application/service/test_auto_training_dispatch.py labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py labelu/tests/internal/common/test_execution_fencing.py labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_stability.py labelu/tests/internal/application/service/test_yolo_train_queue.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short
```

结果：`153 passed`，零失败；`py_compile` 和 `git diff --check` 通过。

## 4. 并发、性能与失败边界

- 候选扫描和领取仍是轻量 SQL 条件，不创建第二套任务状态机；真正编译副作用前仍由执行租约监控器续租和 fencing。
- 有效执行租约时 Worker 不计入候选、不领取 queue lease，也不启动编译；执行租约过期后可按 queue lease 规则重新接管。
- 本地双进程演练使用 SQLite；尚未证明同版本 MySQL 索引命中、锁等待、20 并发、systemd 重启、GPU 释放、真实 TPU-MLIR、BM1688、S3、AICM 或生产容量。
- 正式迁移列未完成时，独立 BM1688 Worker 保持失败关闭，不能把本地 Worker 通过写成生产 Worker 已放行。

## 5. 角色验收

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R04 后端 | 独立 BM1688 Worker 候选与领取的 execution lease 条件 | 本地完成 |
| R03 架构 | 复核 Worker queue lease 与编译 execution lease 分层 | 本地静态确认 |
| R06 模型算法 | 确认 Worker 领取门禁不改变 PT/ONNX/BModel 制品 | 本地静态确认；真实工具链未验收 |
| R07 边端 | 确认有效编译者存在时不重复占用 BM1688 编译资源 | 本地专项通过；真实设备未验收 |
| R09 测试 | 双进程唯一领取、过期队列接管、有效执行租约拒绝 | `153 passed` 合并通过 |
| R10 运维 | 复核迁移、索引、systemd 和设备资源边界 | 本轮未执行生产环境 |

## 6. 交付状态

本轮为本地 L1，未提交、未推送、未部署。下一依赖为正式迁移后的 MySQL 双进程/20 并发锁等待、BM1688 Worker systemd 重启、真实 TPU-MLIR 和 BM1688 真机证据。
