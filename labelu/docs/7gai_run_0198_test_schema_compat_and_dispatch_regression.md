# RUN-0198 测试库结构兼容与自动派发联合回归

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R09 测试；协作角色：R06 模型算法、R07 BM1688 边端。
- 工作包：WP2 执行租约与事务边界、WP3 边端任务创建、WP5 联合回归。

## 目标和涉及按钮

本轮不新增用户菜单或按钮，验证以下既有业务动作的后端闭环：

- 任务与标注：自动训练派发、恢复领取和失败关闭；
- 训练中心：训练作业租约、Outbox 事件和重试代次；
- 模型中心：BM1688 边端验证任务原子创建和打包；
- 进化中心：自动回流触发训练链路的幂等和租约保护。

## 修改内容

- `labelu/tests/conftest.py`：针对长期复用的 SQLite 测试库，补齐
  `task_annotation_contract.label_scope_mode` 兼容列；列默认值为 `FULL`，与 ORM
  和正式迁移语义一致。该补丁只作用于测试夹具，不修改生产数据库或正式 Alembic 链。
- 既有 `auto_training_dispatch.py` 和 `yolo_edge_validation.py` 的 RUN-0197 改动继续由本轮联合回归验证：持有人为空可领取；持有人存在但无过期时间时失败关闭；过期租约才能接管；导出执行 token 更新与边端任务插入在同一事务；旧 Worker 失租不能创建新的边端待执行任务。

## 并发、幂等与失败关闭

1. 租约接管必须同时满足“持有人存在且明确过期”；没有过期时间不视为可接管。
2. 边端任务创建必须持有当前执行 fencing token；旧 Worker 只能退出，不能写入新的待执行任务。
3. Outbox、恢复器和手动重试复用同一幂等键，重试生成新的执行代次，不覆盖旧代证据。
4. 测试库兼容列只为历史本地 SQLite 提供向前兼容，不可作为生产迁移完成的证据。

## 本地验证

结构漂移专项：

```text
python -m pytest labelu/tests/internal/application/service/test_auto_training_reconciliation.py -q --disable-warnings --tb=short
17 passed, 112 warnings in 4.57s
```

自动训练和边端联合回归：

```text
python -m pytest labelu/tests/internal/application/service/test_auto_training_dispatch.py labelu/tests/internal/application/service/test_auto_training_reconciliation.py labelu/tests/internal/application/service/test_auto_training_outbox.py labelu/tests/internal/application/service/test_yolo_edge_validation_pack.py labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py labelu/tests/internal/application/service/test_auto_training_bm1688_edge_flow.py -q --disable-warnings --tb=short
93 passed, 115 warnings in 11.66s
```

`py_compile` 和 `git diff --check` 的既有结果保持通过。本轮没有连接真实 MySQL、TPU-MLIR、BM1688、生产 S3 或 AICM，也没有执行业务写入。

## 环境证据和验收等级

- 本地：L1，通过专项和联合回归。
- 预生产：未执行；没有同版本 MySQL 并发接管、迁移升级/回滚和真实 Worker 证据。
- 生产：未连接；不能把本地通过写成生产完成。
- 真机：未执行；FP32/FP16/INT8 加载、有效推理、P95、短稳和设备重启接管仍待 R07/R10 验收。

## 风险和下一依赖

1. `label_scope_mode` 的测试兼容列已补齐，但生产历史数据库是否具备同字段必须由 R10 在同版本 MySQL 沙箱和备份快照中对账。
2. 真实部署前仍需执行迁移发布闸门、20 并发领取、锁等待、旧 fencing token 拒绝和重启接管演练。
3. 本轮不允许提交、推送或部署；等待用户明确“提交代码”，且生产放行证据完成后再进入交付流程。
