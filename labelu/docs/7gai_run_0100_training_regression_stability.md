# RUN-0100 训练子进程兼容回归与并发测试稳定性（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`TRAIN-PHASE-COMPAT-01`、`LOCAL-CONCURRENCY-TEST-01`。
- 工作包和角色：WP2 训练与执行器；R04 后端主责，R09 测试协作，R03 架构复核。
- 本轮目标：修复 RUN-0099 引入的无 fencing 兼容路径阶段事件结构回归，并让 Windows spawn + SQLite 的 20 进程本地竞争专项不因固定等待窗口误报失败。

## 实际代码变化

- `yolo_train_queue.py` 的 YOLO/车牌子进程阶段写入只有在确实提供 owner/token 时才传 fencing 参数；历史 Web 路径继续生成原有阶段事件结构，不出现 `None` 字段。
- `test_yolo_train_worker.py` 的 20 进程专项将 Windows spawn 下的进程等待上限从 30 秒调整为 90 秒，并保留“20 个进程只有一个成功领取、无第二副作用”的断言。该调整只影响测试等待，不改变生产租约、数据库连接或队列逻辑。

## 本地证据

```text
python -m pytest \
  labelu/tests/internal/application/service/test_yolo_train_queue.py::test_plate_subprocess_persists_child_runtime_pid \
  labelu/tests/internal/application/service/test_yolo_train_queue.py::test_yolo_subprocess_persists_child_runtime_pid \
  -q --disable-warnings --tb=short
2 passed

python -m pytest \
  labelu/tests/internal/application/service/test_yolo_train_worker.py::test_twenty_processes_only_one_can_claim_same_training_job \
  -q --disable-warnings --tb=short
1 passed（54.08 秒）

本轮合并训练、车牌、远程、test-val 和边端入口回归：`146 passed, 117 warnings`（`run0100-regression-final`）。
```

## 并发与真实性边界

- 20 进程证据仍然是本地 SQLite 条件领取证据，不等同于生产 MySQL 锁等待、执行计划或跨主机双进程接管。
- 本轮未改变生产租约时长、连接池、队列查询或副作用调用；没有真实 MySQL、GPU、systemd、S3、AICM、TPU-MLIR 或 BM1688 证据。
- 测试等待上限放宽是为了覆盖 Windows 进程启动和 SQLite 文件锁等待，不能据此宣称生产容量达标；生产容量仍需按真实 MySQL 和训练节点重新测量。

## 交付状态

- 本地 L1：通过；未提交、未推送、未部署，等待用户明确说“提交代码”。
- 下一依赖：R03/R09 在同版本 MySQL 上做双进程领取、锁等待和失租接管；R04/R06 继续补齐车牌训练阶段世代透传；R07/R10 做真实 GPU、systemd 和混合负载验证。
