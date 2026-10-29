# RUN-0105 旧训练队列路径执行租约门禁

## 1. 本轮目标

独立训练 Worker 持有 `PROCESSING` 作业时，旧 Web 队列仍可能因 GPU 资源不足、队列调度或僵尸恢复逻辑把作业改回 `PENDING`。这会造成重复领取、错误重启或新旧执行器竞争。本轮把有效执行租约作为旧队列路径的优先条件。

## 2. 实现内容

- 新增 `_has_active_execution_lease()`，统一识别 `execution_lease_owner` 和未过期时间。
- 资源不足回队 `_mark_job_queued_for_resources()` 在旧路径修改状态前检查有效执行租约；事务内再次检查，避免检查后接管窗口。
- `reconcile_stale_processing_jobs()` 在回退僵尸作业前跳过有效执行租约；失败回写和回队回写事务内再次确认。
- 仅限制旧无上下文队列/恢复写入，不改变独立 Worker 的领取、续租和释放逻辑；不新增菜单、用户角色、项目维度、API、数据库字段或模型制品。

## 3. 验收

队列专项：

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings --tb=short
```

结果：`38 passed`。

合并训练执行专项：

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_stability.py labelu/tests/internal/application/service/test_yolo_train_queue.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short
```

结果：`72 passed`，零失败。

## 4. 真实性与并发边界

- 证据为本地 Windows/SQLite 和模拟进程，仅达到 L1。
- 尚未证明同版本 MySQL 锁等待、双进程接管、20 并发、systemd 重启、GPU 释放、真实训练、TPU-MLIR、BM1688、S3、AICM 或生产容量。
- 真实数据库迁移和执行器切换前，独立 YOLO/车牌 Worker 继续 fail-closed；旧 Web 路径仍是生产兼容路径。

## 5. 角色验收

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R04 后端 | 资源回队、僵尸恢复的租约跳过和事务内复核 | 本地完成 |
| R03 架构 | 确认 queue lease 与 execution lease 不混用 | 本地静态确认 |
| R06 模型算法/R07 边端 | 确认不会因旧队列回写误触发训练制品或 BM1688 编译 | 未连接真实工具链，保留前置项 |
| R09 测试 | 队列、接管、恢复和兼容回归 | `72 passed` 合并通过 |
| R10 运维 | 复核生产租约、重启和数据库边界 | 本轮未执行生产环境 |

## 6. 交付状态

本轮为本地 L1，未提交、未推送、未部署。下一依赖为同版本 MySQL 双进程锁等待、systemd 重启接管和真实 GPU 观察。
