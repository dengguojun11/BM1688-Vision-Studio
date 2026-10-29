# RUN-0197 自动训练派发失败关闭与边端任务原子门禁（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R07 BM1688/边端、R09 测试；R06 模型算法、R10 运维协作。
- 工作包：WP2 自动训练与数据回流触发、WP3 BM1688/边端执行、WP5 并发与生产放行。

## 本轮确认并修改

### 1. 自动训练/回流派发租约失败关闭

统一训练、BM1688、边端及其恢复领取入口的租约条件：

- 持有人为空，可以领取；
- 持有人不为空时，必须存在明确过期时间且已经过期，才可以接管；
- “持有人存在但过期时间为空”的损坏记录不再被当成可领取，避免两个 Worker 因异常字段同时执行。

复用 `_lease_available` 条件，覆盖训练派发、BM1688 派发、边端派发、BM1688 恢复和边端恢复五条路径。

### 2. BM1688 导出到边端任务创建的原子门禁

边端自动验证任务创建时，导出执行 token 的条件更新与任务插入放在同一数据库事务中。旧 Worker 即使在前置检查后被接管，也无法创建新的待执行边端任务；打包阶段失租不再把任务无条件改为失败，保留给当前 Worker/恢复器处理。

## 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_auto_training_dispatch.py -q --disable-warnings --tb=short
26 passed

python -m pytest labelu/tests/internal/application/service/test_auto_training_outbox.py labelu/tests/internal/application/service/test_yolo_edge_validation_pack.py -q --disable-warnings --tb=short
23 passed

python -m py_compile labelu/internal/application/service/auto_training_dispatch.py labelu/internal/application/service/yolo_edge_validation.py
通过
```

新增断言：

- 训练派发损坏租约阻断；
- BM1688 派发与恢复损坏执行租约阻断；
- 边端派发与恢复损坏队列租约阻断；
- 旧 BM1688 执行 token 无法创建边端验证任务。

## 测试边界与生产状态

- 自动训练 reconciliation 的一组既有测试当前被本地持久化测试库结构漂移阻断：`task_annotation_contract` 表缺少 ORM 已有的 `label_scope_mode` 列；这不是本轮派发代码失败，需由测试库结构对齐工作包单独修复。
- 未连接生产 MySQL、第三方 S3、AICM、TPU-MLIR、BM1688 真机或 systemd；未提交、未推送、未部署。
- 本轮等级：本地 L1。进入 L2/L3 前仍需同版本 MySQL 2/10/20 并发、损坏租约恢复策略、Worker 重启接管和真实回流对账。
