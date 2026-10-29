# RUN-0108 自动派发与恢复领取的执行租约原子门禁

## 1. 本轮目标

RUN-0107 解决了恢复入口的前置短路，但“读取作业后到领取恢复租约前”仍存在并发窗口：另一个 Worker 可能在此期间取得执行租约，旧调度器随后仍可能领取队列/恢复租约。虽然真正执行器会再次拒绝，但会产生无效调度副作用。运行时派发和恢复领取都需要在同一 SQL 条件中排除未过期 execution lease。

## 2. 实现内容

- `_claim_bm1688_dispatch()` 和 `_claim_edge_validation_dispatch()` 增加原子条件：执行租约 owner 为空、过期时间为空或已过期时才允许领取队列租约。
- `claim_bm1688_recovery()` 和 `claim_edge_validation_recovery()` 同样增加执行租约条件，避免旧恢复器在接管窗口夺取恢复租约。
- 既有 queue lease 与 execution lease 仍保持分离：队列租约只表示调度交接，执行租约由实际编译/边端 Worker 领取并续租。
- Windows/SQLite 双进程演练表补齐三列执行租约字段，保证并发测试使用与当前 ORM 契约一致的隔离表；不改变生产表结构，生产结构仍由正式迁移提供。

## 3. 验收证据

```text
python -m pytest labelu/tests/internal/application/service/test_auto_training_dispatch.py labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py -q --disable-warnings --tb=short
```

结果：`64 passed`，零失败；覆盖训练/编译/边端派发、恢复租约、过期接管、双进程唯一领取和有效执行租约拒绝。

`py_compile` 和本轮相关文件 `git diff --check` 通过。

## 4. 并发、性能与失败边界

- 领取仍是单条条件更新，新增三个索引列条件，不创建额外锁表或后台线程；在 MySQL 上仍需用真实执行计划和锁等待验证索引命中与等待时间。
- 如果执行租约列尚未完成正式迁移，派发/恢复 SQL 会失败关闭；不能把旧库当作已具备跨进程执行 fencing 的生产库。
- 本轮没有连接真实 MySQL、真实 BM1688、SSH/SFTP、GPU 节点、S3、AICM 或生产队列；独立编译/边端 Worker 继续 `fail-closed`。

## 5. 角色验收

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R04 后端 | 派发与恢复四条领取函数的执行租约 SQL 条件 | 本地完成 |
| R03 架构 | 复核 queue lease/execution lease 分层和接管窗口 | 本地静态确认 |
| R06 模型算法 | 确认领取条件不改变模型训练、PT/ONNX/BModel 制品 | 本地静态确认 |
| R07 边端 | 确认有效执行者存在时不重复安排编译/SSH 验证 | 本地专项通过；真实设备未验收 |
| R09 测试 | 双进程唯一领取、过期接管及有效租约拒绝 | `64 passed` |
| R10 运维 | 复核正式迁移、MySQL 索引和锁等待要求 | 本轮未执行生产环境 |

## 6. 交付状态

本轮为本地 L1，未提交、未推送、未部署。下一依赖为正式迁移后的同版本 MySQL 双进程/20 并发锁等待、执行计划和生产备份恢复演练。
