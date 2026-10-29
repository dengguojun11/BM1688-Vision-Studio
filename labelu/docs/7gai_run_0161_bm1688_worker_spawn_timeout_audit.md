# RUN-0161 BM1688 Worker 并行专项 ready_queue 超时审计

- 日期和时区：2026-09-19，Asia/Shanghai。
- 负责角色：R09 测试主责；R03 架构、R04 后端、R07 BM1688 边端协作。
- 范围：审计 BM1688 Worker 多进程专项在并行执行时出现的 4 个 `ready_queue` 超时。
- 变更边界：本轮只做本地验证和记录；未修改生产代码、未连接 MySQL/生产、未提交、未部署。

## 复现证据

专项文件为 `labelu/tests/internal/application/service/test_bm1688_worker_runtime.py`。
`_run_two_processes()` 使用 Windows `multiprocessing` 的 `spawn` 启动两个子进程，父进程在第 157 行等待：

```text
ready_queue.get(timeout=20)
```

单独串行执行：

```text
$env:LABELU_TEST_DB_NAME='bm1688-one-20260919'
python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py::test_two_processes_claim_one_pending_export_and_run_one_side_effect -q --disable-warnings --tb=short
```

通过，耗时约 24 秒。完整专项单独执行通过：`9 passed`，耗时约 60 秒。

同时启动 4 份完整专项（每份使用独立 `LABELU_TEST_DB_NAME`）后，4 份均得到相同结果：`5 passed, 4 failed`。4 个失败均为：

```text
_queue.Empty
labelu/tests/internal/application/service/test_bm1688_worker_runtime.py:157
ready_queue.get(timeout=20)
```

失败的 4 个用例完全一致：

1. `test_two_processes_claim_one_pending_export_and_run_one_side_effect`
2. `test_two_processes_take_over_one_expired_queue_lease`
3. `test_two_processes_reject_one_unexpired_queue_lease`
4. `test_worker_rejects_one_unexpired_execution_lease`

捕获日志显示，子进程在父进程启动后约 20 秒才完成 `labelu.internal.common.config` 导入并输出路径日志；父进程已经在 `ready_queue` 超时退出。因此失败发生在业务函数调用前，未执行 `_candidate_ids`、`_claim_export`、执行租约或编译副作用断言。

## 结论

这是测试夹具的启动窗口不足，不是 BM1688 Worker 领取事务或 fencing 实现缺陷。理由如下：

- 同一专项串行执行通过；
- 两份专项并行曾同时通过；
- 四份专项并行时，失败位置固定为 `ready_queue.get(timeout=20)`，没有出现数据库锁、重复领取、旧 token 写入或副作用次数错误；
- 业务运行时代码仅在子进程完成 `ready_queue.put(owner)` 后才会被调用，当前失败根本没有进入该路径。

## 建议

本轮不改生产代码。测试夹具应做以下修复后再纳入并发回归：

1. 将 Windows `spawn` 子进程启动相关的 20 秒窗口提升为 60 秒或 120 秒，并通过一个统一常量复用到 `ready_queue`、`start_event`、`candidate_barrier` 和 `result_queue`，避免不同阶段超时标准不一致。
2. `_run_two_processes()` 应使用 `try/finally` 清理子进程；当前 `ready_queue` 断言在启动阶段失败时会提前退出，可能留下未回收子进程，进一步放大后续并行测试的启动争用。
3. 启动等待失败时收集每个进程的 `pid`、`is_alive()`、`exitcode` 和子进程错误消息，再断言超时；这样能区分“导入慢”与“子进程启动崩溃”。
4. 业务并发断言仍保留串行基线和 2 份并行基线；扩大到 4 份并行只作为测试稳定性检查，不把本机 Python 启动耗时当作 BM1688 生产吞吐证据。

修复测试夹具后，应重新执行：

```text
python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py -q --disable-warnings --tb=short
```

并在不同并行度下重复；只有业务断言全部执行并通过，才能把本地多进程证据记为 L1。真实 MySQL 2/10/20/50 并发、生产迁移和 BM1688 真机证据仍不由本轮替代。

## 状态

- 本地专项：单份 `9 passed`；四份并行稳定复现 4 个启动阶段超时。
- 生产代码：无需因本问题修改。
- 测试代码：已按建议修改 `test_bm1688_worker_runtime.py`，统一使用 90 秒等待窗口；启动/结果超时会输出 pid、存活状态和退出码；所有异常路径都会释放子进程。
- commit/push/deploy：未提交、未推送、未部署。
