# RUN-0072：YOLO/车牌独立训练执行器第一步

## 产出记录

| 角色 | 本轮产出 | 验收证据 |
| --- | --- | --- |
| 模型算法架构师 | 将训练领取拆成 `queue_lease_*`（短租约）和 `execution_lease_* + execution_fencing_token`（训练执行期保护） | `gai_p1_08_add_dispatch_leases.py`；同一作业双 owner/过期接管测试 |
| 边端算法工程师 | 明确 GPU 领取发生在启动训练子进程前，记录 `assigned_gpu_json`；训练类型同时覆盖 YOLO 检测、车牌检测、车牌识别 | `yolo_train_worker.py` 复用现有 GPU 分配器和三类训练入口 |
| 后端研发 | 新增数据库领取、执行租约、过期接管、旧 owner 条件写入和清理入口 | `internal/application/service/yolo_train_worker.py` |
| 测试工程师 | 隔离迁移演练、双 owner 领取、旧 owner 释放拒绝、既有 fencing/队列回归 | `tests/alembic/test_dispatch_lease_migration_candidate.py`；`tests/internal/application/service/test_yolo_train_worker.py` |
| 运维工程师 | 新增独立进程入口，支持单轮/循环扫描；默认关闭 | `scripts/yolo_train_worker.py`；`YOLO_TRAIN_DEDICATED_WORKER_ENABLED=false` |

## 当前边界

1. 默认仍由 Web 旧路径消费训练作业；未连接生产、未提交、未部署。
2. 即使开关开启且数据库字段齐全，目前仍保持独立切换不可用，Web 旧路径继续消费。YOLO/车牌子进程内部仍直接写作业状态，外层 token 不能防止失租后的旧子进程覆盖接管者；必须先完成子进程全链路 fencing 和失租终止协议。
3. Worker 领取顺序为：数据库原子领取短租约 -> 领取执行租约并递增 fencing token -> 检查暂停/停止标记 -> GPU 资源检查与记录 -> 启动现有训练子进程。
4. 本轮尚未把候选迁移合并进正式 Alembic head，也未把独立 Worker 接入生产 systemd；需要下一轮完成隔离库演练、GPU 并发压力和真实训练回归后再切换。

## 本地验证

```text
python -m pytest labelu/tests/alembic/test_dispatch_lease_migration_candidate.py labelu/tests/internal/application/service/test_yolo_train_worker.py -q
8 passed

python -m pytest labelu/tests/internal/common/test_execution_fencing.py labelu/tests/internal/application/service/test_yolo_train_queue.py -q
36 passed

python -m py_compile labelu/internal/application/service/yolo_train_worker.py labelu/scripts/yolo_train_worker.py
git diff --check
```

## 下一验收门

- 在隔离 MySQL 演练候选迁移，确认并发领取为单 owner；
- 将 YOLO/车牌子进程所有状态/终态写入绑定 owner/token，并在失租时终止子进程，覆盖双进程竞争测试；
- 用两份本地 Worker 进程跑 CPU/模拟 runner，确认旧 owner 终态写入被拒绝；
- 用真实 GPU 节点做两作业并发和暂停/恢复测试；
- 形成 systemd 单元和回滚/停用操作手册后，才允许把开关从默认关闭改为显式开启。
