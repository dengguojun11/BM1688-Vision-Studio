# RUN-0165 四类 Worker 领取并发与旧 token 接管审计

- 日期和时区：2026-09-20，Asia/Shanghai。
- 负责角色：R03 架构、R09 测试、R10 运维；R04 后端、R07 BM1688/边端协作。
- 验收等级：本地 L1；未连接生产、未执行同版本 MySQL 沙箱，未提交、未推送、未部署。
- 本轮增量：为第三方 S3 问题数据回流增加真实 `claim_pending` 的 2/10/20 独立进程本地竞争测试。

## 四类领取路径核对

| 路径 | 实际领取入口 | 租约/令牌 | 当前本地并发证据 | 旧 token 证据 | 结论 |
|---|---|---|---|---|---|
| 训练 | `yolo_train_worker.claim_pending_train_job` | `queue_lease_owner/queue_lease_expires_at`；执行阶段 `execution_lease_owner/execution_lease_expires_at/execution_fencing_token` | `test_yolo_train_worker.py` 已有 20 独立进程竞争，单一成功者 | 训练执行写入按 owner/token 条件拒绝，已有 stale token 和接管测试 | 本地覆盖较完整，真实 MySQL 未验收 |
| BM1688 编译 | `bm1688_worker_runtime._candidate_ids` + `_claim_export` | queue lease + execution lease/fencing | `test_bm1688_worker_runtime.py` 已覆盖 2 进程竞争、过期接管和有效租约拒绝 | execution fencing 契约和失租写入保护已有测试 | 本地 2 进程通过；10/20 需真实 MySQL/后续扩展 |
| 边端验证 | `edge_validation_worker_runtime.candidate_ids` 后由 `_run_manual_job_sync` 领取 execution lease | queue lease 字段用于调度兼容；实际 SSH/板端阶段使用 execution lease/fencing | 当前只有候选扫描和 Worker 入口测试，未有 2/10/20 独立进程真实领取测试 | `test_worker_execution_fencing_contract.py` 覆盖过期/失租停止和旧失败写入拒绝 | 本地结构覆盖，尚缺多进程副作用与设备独占证据 |
| 第三方 S3 回流 | `crud_experiment_dataset_import.claim_pending` | `worker_lease_owner/worker_lease_expires_at/worker_fencing_token`，并按 token 更新/续租 | 本轮新增 2、10、20 独立进程竞争，均只有一个成功领取 | 已有 worker-a 接管为 token=2，旧 token 释放/续租拒绝；本轮保留 | 本地覆盖达到 L1，真实 MySQL/S3 I/O 未验收 |

## 本轮实现

新增测试辅助进程 `_claim_import_process`，每个独立进程使用真实 SQLite 文件、独立 SQLAlchemy Session 和真实 `crud_experiment_dataset_import.claim_pending`，不使用线程锁或 mock 领取逻辑。参数化并发数为 `2/10/20`，每档建立一个 `PENDING` 回流任务并同时启动竞争进程。

断言内容：

- 所有子进程正常退出且没有异常回报；
- 只有一个进程返回一个 `(job_id, fencing_token)`；
- 最终 `worker_lease_owner` 等于唯一获胜者；
- `worker_fencing_token == 1`，`attempt_count == 1`，证明没有重复领取副作用。

修改文件：

- `labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py`
- 本记录文档。

## 本地验证

执行命令：

```text
$env:LABELU_TEST_DB_NAME='run0165-s3-concurrency'
python -m pytest labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q --disable-warnings --tb=short
```

结果：`9 passed`，其中新增 2/10/20 三档独立进程竞争全部通过，耗时约 112 秒。

相关路径回归：

```text
$env:LABELU_TEST_DB_NAME='run0165-training'
python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py -q --disable-warnings --tb=short
```

结果：`11 passed`。

```text
$env:LABELU_TEST_DB_NAME='run0165-edge'
python -m pytest labelu/tests/internal/application/service/test_edge_validation_worker.py -q --disable-warnings --tb=short
```

结果：`4 passed`。

```text
$env:LABELU_TEST_DB_NAME='run0165-bm'
python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py -q --disable-warnings --tb=short
```

结果：`9 passed`。该专项与训练专项并行运行时耗时约 197 秒，说明 Windows `spawn` 启动争用明显，但业务领取和 fencing 断言仍通过；这与 RUN-0161 的测试窗口审计一致。

目标测试文件 `py_compile` 通过，`git diff --check` 通过。

## MySQL 与生产缺口

已有 `labelu/scripts/rehearse_mysql_execution_fencing.py` 是通用真实 InnoDB 演练入口，支持 2 至 64 个竞争进程，生产标准值为 20；演练会记录锁等待、唯一 token 领取、旧 token 拒绝和新 token 成功写入。它目前只使用通用临时表，尚未按四张业务表逐一执行。

本轮没有可确认的同版本本机 `labelu_migration_sandbox*` MySQL，因此以下证据仍未验收：

- 训练 `yolo_train_job` 的 MySQL 2/10/20/50 领取与接管；
- BM1688 `yolo_bm1688_export` 的 MySQL 2/10/20/50 领取与接管；
- 边端 `yolo_edge_validation_job` 的 MySQL 2/10/20/50 execution lease 接管；
- S3 `experiment_dataset_import` 的 MySQL 2/10/20/50 worker lease 接管；
- 同版本 MySQL `SHOW CREATE TABLE`、`SHOW INDEX`、`EXPLAIN FORMAT=JSON`、元数据锁与锁等待；
- 真实 S3 下载、TPU-MLIR/BM1688 编译、SSH/板端资源独占和 Worker 重启接管。

## 后续建议

1. MySQL 沙箱可用后，先把通用演练扩展为按业务表/字段映射执行，至少跑 `2/10/20/50` 四档；每档必须保留锁等待、唯一领取、旧 token 行数为 0、新 token 行数为 1 的 JSON 证据。
2. 边端补一个只领取 execution lease 的无 SSH 多进程测试，验证 2/10/20 竞争不会重复进入 `_run_manual_job_sync`；通过后再做单设备真机并发，避免把 SSH 副作用带入数据库单测。
3. BM1688 本地测试保持 2 进程功能回归；10/20/50 作为 MySQL/预生产并发验收，不在 Windows SQLite 上宣称生产吞吐。
4. 将 RUN-0161 的 Windows `spawn` 启动超时统一常量和子进程清理修复合入测试夹具，避免并发回归因启动窗口不足误报。

## 状态

- 本轮代码：仅新增 S3 回流并发测试；无业务代码变更。
- 本地证据：S3 2/10/20、训练 20、BM1688 2、边端入口/fencing专项均通过。
- 生产/MySQL：未连接、未写入、未部署；四类真实 MySQL 2/10/20/50 仍未验收。
- commit/push/deploy：未提交、未推送、未部署，等待用户明确授权。
