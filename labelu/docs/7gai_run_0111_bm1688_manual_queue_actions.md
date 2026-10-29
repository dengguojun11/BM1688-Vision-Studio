# RUN-0111 BM1688 手工导出与重新编译统一进入独立 Worker

## 1. 本轮目标

RUN-0109 已经让独立 BM1688 Worker 能够领取自动训练导出，但生产页面上的“编译 bmodel”和“重新编译”仍会在 Web 进程内启动线程。这样在标注、查询、下载和编译并发时，Web 进程仍可能被编译器占用，也无法把手工任务和自动任务纳入同一套队列容量与执行租约。

本轮将两种手工动作复用到现有 `yolo_bm1688_export` 任务表和 Worker，不新增菜单、人员角色、项目/业务空间或第二套任务状态机。

## 2. 实现内容

- `YoloBm1688Export.worker_action`：
  - `export`：PT -> ONNX -> FP32/FP16/INT8 BModel；
  - `recompile`：已有 ONNX -> FP32/FP16/INT8 BModel；
  - 历史空值按 `export` 兼容处理。
- 手工导出接口创建 `PENDING + worker_action=export`；开启独立 Worker 时 Web 只入队并立即返回任务记录，不再在 Web 进程启动编译线程。
- 手工重新编译接口先把现有记录置为 `PENDING + worker_action=recompile`；开启独立 Worker 时由 Worker 领取并调用已有重新编译函数。
- Worker 候选扫描和原子领取复用现有 queue lease / execution lease / fencing，不改变真正编译函数的执行期门禁。
- 手工重新编译现在也在进入工具链前领取 execution lease；阶段写入、制品清单和失败终态使用 owner/token 条件更新，失租或旧 token 不得覆盖新执行者。
- 未开启独立 Worker 时保留原 Web 线程兼容路径，便于本地开发和迁移窗口验证；生产切换前必须先完成正式迁移和 Worker 灰度。
- BM1688 导出表增加动作单列索引，Worker 扫描复合索引改为 `(status, worker_action, queue_lease_expires_at, id)`，与现在的自动/手工统一候选条件一致。

## 3. 验收证据

```text
python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/alembic/test_dispatch_lease_migration_candidate.py -q --disable-warnings --tb=short
46 passed

python -m py_compile labelu/internal/domain/models/yolo_bm1688_export.py labelu/internal/application/service/bm1688_worker_runtime.py labelu/internal/application/service/yolo_bm1688_export.py labelu/docs/7gai_phase_one_migration_candidates/gai_p1_08_add_dispatch_leases.py
git diff --check
通过
```

覆盖点包括：手工导出在独立 Worker 开启时不调用 Web 线程池、Worker 动作路由、手工重新编译执行 fencing、双进程唯一领取、过期接管、有效执行租约拒绝、候选迁移幂等升级和降级。

## 4. 并发与性能边界

- 本地 SQLite 只证明动作路由、条件领取和测试隔离，不证明 MySQL 锁等待、生产执行计划或真实编译吞吐。
- `worker_action` 不是新的业务状态机；任务状态仍使用现有 `PENDING/PROCESSING/COMPLETED/FAILED`。
- Web 只负责短事务入队，编译器、日志、制品和质量评估仍由现有编译函数负责；执行期间继续使用 execution lease 和 fencing。
- 手工重新编译在 Worker 返回非异常但未完成的历史异常路径仍需补充终态监控，正式灰度前必须把“无记录/ONNX缺失/工具链失败”统一落为可重试或失败状态，不能长期停留在 `PENDING`。

## 5. 生产放行条件

1. 将 `worker_action` 和复合索引纳入正式唯一 Alembic 迁移链，并在同版本 MySQL 空库、脱敏生产快照和重复升级中验证。
2. 在 MySQL 上完成两进程/20 并发领取、锁等待、租约过期接管和旧 token 写入拒绝。
3. 在真实 BM1688 节点以 systemd 启动独立 Worker，验证编译工具链、板卡资源释放、重启接管和共享目录清理。
4. 先灰度自动导出，再灰度手工导出/重新编译；观察 Web P95、队列等待、编译耗时、失败率和磁盘增长。
5. 以上证据完成前，独立 Worker 保持 `fail-closed`，不能把本地 45 个测试写成生产可用。

## 6. 角色交付

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R03 架构 | 统一自动/手工动作模型，保持现有任务和租约边界 | 本地代码与候选迁移完成；MySQL待验收 |
| R04 后端 | 入队、动作路由、Worker执行分派 | 本地完成 |
| R06 模型算法 | 确认 export/recompile 不改变 PT、ONNX、三种 BModel 产物关系 | 本地静态确认；真实 TPU-MLIR 未验收 |
| R07 边端 | 确认手工编译不绕过 BM1688 执行租约 | 本地静态确认；真实板卡未验收 |
| R09 测试 | 动作路由、重新编译 fencing、双进程领取和迁移候选专项 | 46 passed |
| R10 运维 | Worker/systemd、MySQL、磁盘和资源释放 | 本轮未执行生产环境 |

## 7. 交付状态

本轮为本地 L1，未提交、未推送、未部署。未改变已发布模型、AICM 记录或平台下载状态；下一轮优先完成正式迁移链和 MySQL 演练。
