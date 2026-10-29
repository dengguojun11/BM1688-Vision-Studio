# RUN-0106 自动续训与手工重启执行租约门禁

## 1. 本轮目标

独立训练 Worker 已持有有效 `execution lease` 时，自动续训、看门狗恢复或手工重启不能再次启动同一个训练作业。否则会出现两个训练进程同时写入同一作业目录和数据库，造成阶段文件、指标、权重与最终状态互相覆盖。

## 2. 实现内容

- 在 `execution_fencing.py` 增加纯内存函数 `execution_lease_is_active()`，统一判断 owner 存在、过期时间有效且尚未过期；非法时间值按失效处理。
- 旧训练队列复用该判断函数，避免不同模块对有效租约产生不同解释。
- 自动续训从检查点恢复和按现有配置重新启动两条路径，首次读取作业和事务内再次读取作业时都检查有效执行租约；检测到有效租约立即跳过。
- `job_can_auto_resume()` 在有效执行租约存在时返回不可恢复，避免看门狗先启动后才发现重复执行。
- 保留无执行上下文的历史调用兼容，但只在没有有效执行租约时允许继续；不新增菜单、用户角色、项目维度、接口或数据库字段。
- 两个执行期 Monitor 测试改用动态当前时间，避免固定历史时间导致租约在测试运行时必然过期；生产租约判断逻辑未放宽。

## 3. 验收证据

专项队列测试：

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings --tb=short
```

结果：`39 passed`。

执行期 fencing、Worker、稳定性、队列和契约合并回归：

```text
python -m pytest labelu/tests/internal/common/test_execution_fencing.py labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_stability.py labelu/tests/internal/application/service/test_yolo_train_queue.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short
```

结果：`81 passed`，零失败。

`py_compile` 和本轮相关文件 `git diff --check` 通过。

## 4. 并发、性能与失败边界

- 本轮只在启动前增加两次轻量租约读取，不增加训练数据、模型制品或外部接口请求；事务内复核用于缩小接管窗口。
- 自动续训与手工重启遇到有效租约会跳过并等待当前 Worker 结束或租约失效，不会强制终止持有者。
- 本地测试使用 Windows/SQLite 和模拟作业；尚未证明同版本 MySQL 锁等待、双进程接管、20 并发、systemd 重启、GPU 释放、真实训练、TPU-MLIR、BM1688、S3、AICM 或生产容量。
- 独立 YOLO/车牌 Worker 在真实迁移和执行器证据完成前继续 `fail-closed`，本轮不得切换生产独立消费路径。

## 5. 角色验收

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R04 后端 | 自动续训/手工重启两条入口的租约门禁及事务内复核 | 本地完成 |
| R03 架构 | 统一有效租约判断，保持 queue lease 与 execution lease 分离 | 本地静态确认 |
| R06 模型算法 | 确认跳过重复启动不改变 PT、指标、ONNX/BModel 输入契约 | 本地静态确认；真实训练未验收 |
| R07 边端 | 确认重复训练不会触发重复编译或真机验证 | 未连接 TPU-MLIR/BM1688，保留前置项 |
| R09 测试 | 覆盖有效、过期、缺 owner、非法时间、自动续训和队列回归 | `81 passed` |
| R10 运维 | 复核重启接管、systemd、GPU 释放和生产观察边界 | 本轮未执行生产环境 |

## 6. 交付状态

本轮为本地 L1，未提交、未推送、未部署。下一依赖为同版本 MySQL 双进程锁等待、真实 Worker 重启接管、systemd/GPU 释放和共享目录清理证据。
