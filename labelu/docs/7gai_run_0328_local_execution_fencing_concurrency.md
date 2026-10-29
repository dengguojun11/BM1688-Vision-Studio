# RUN-0328 本地执行租约、fencing 与 Worker 并发契约验收（2026-09-21）

## 1. 本轮目标

在本地测试库验证多个 Worker/进程同时处理训练、AICM 发布和 BM1688 编译任务时，队列租约与执行租约分层，旧 token、失租任务和重复领取不会产生重复副作用。

## 2. 责任角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP0/WP2 | 执行租约、fencing token、状态条件更新和接管契约 |
| R04 后端 | WP1/WP2 | Worker 领取、续租、终态写入和旧 token 拒绝 |
| R05 发布集成 | WP3 | AICM 发布 Worker 的租约边界 |
| R07 BM1688 边端 | WP3 | 编译/边端 Worker 的设备与执行租约边界 |
| R09 测试 | WP5 | 并发领取、失租接管和迟到写入回归 |
| R10 运维 | OPS | 标明 SQLite 本地证据和真实 MySQL 放行边界 |

## 3. 验收命令与结果

```text
python -m pytest \
  labelu/tests/internal/common/test_execution_fencing.py \
  labelu/tests/internal/application/service/test_scheduler_lease.py \
  labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py \
  labelu/tests/internal/application/service/test_aicm_publish_worker_runtime.py \
  labelu/tests/internal/application/service/test_bm1688_worker_runtime.py \
  -q --disable-warnings --tb=short
```

结果：

```text
29 passed, 114 warnings in 46.53s
```

## 4. 已验证行为

- queue lease 负责领取交接，execution lease 负责实际副作用，两者不混用。
- 旧执行 token、过期租约和迟到回传不能更新阶段、进度或终态。
- 重启接管会产生新的 fencing token，旧 Worker 即使恢复也不能继续写入。
- AICM 发布 Worker 和 BM1688 Worker 在字段未就绪、开关关闭或证据缺失时失败关闭。
- 同一任务重复领取不会生成第二份有效副作用。

## 5. 风险与下一依赖

- 本轮是本地测试库契约回归，不等于真实 MySQL 行锁、锁等待、连接池和多副本进程证据。
- 真实生产放行仍需要 MySQL 2/10/20/50 并发矩阵、节点重启、Worker systemd 接管和 GPU/设备释放证据。
- 训练、AICM、BM1688 和 S3 独立 Worker 开关继续保持失败关闭。

## 6. 提交与部署状态

本轮未提交、未推送、未部署、未重启生产。继续等待用户明确说“提交代码”。
