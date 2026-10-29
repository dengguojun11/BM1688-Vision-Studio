# RUN-0261 异步评估执行代次本地实现（2026-09-20）

## 目标

补齐一期异步评估在多进程、进程重启和 Provider 重试场景下的执行代次隔离。手工评估和训练完成后的自动评估共用同一套领取、报告写入和完成/失败条件，不新增菜单或用户角色。

## 责任角色与工作包

| 角色 | 工作包 | 交付 |
|---|---|---|
| R03 架构 | WP2 | 规定 `训练作业阶段 + operation_id` 是评估当前执行真源，历史空编号报告只读兼容 |
| R04 后端 | WP2 | 领取生成代次、条件完成/失败、报告当前代次校验和同代次幂等 |
| R06 模型算法 | WP3 | 评估报告与固定训练作业、模型权重和评估阶段保持血缘 |
| R09 测试 | WP5 | 双领取、旧代次迟到、重复 Provider 重试、候选迁移幂等回归 |
| R10 运维 | WP2/OPS | 保持候选迁移失败关闭，不连接 `.env` MySQL |

## 代码变更

- `labelu/internal/domain/models/yolo_train_job.py`
  - 增加 `ai_eval_full_operation_id`、`ai_eval_next_operation_id`。
- `labelu/internal/domain/models/yolo_eval_report.py`
  - 增加可空 `operation_id`，新增同作业/报告类型/Provider/代次唯一约束和查询索引。
- `labelu/internal/adapter/persistence/crud_yolo_train_job.py`
  - 增加代次领取、当前代次完成/失败、旧代次恢复清理函数；领取和终态写入均为条件更新。
- `labelu/internal/adapter/persistence/crud_yolo_eval_report.py`
  - 报告按代次查询和幂等返回；写入前在事务内锁定训练作业并重新核验当前代次，旧 Provider 迟到时拒绝写入。
- `labelu/internal/application/service/yolo_train_evaluation.py`
  - 手工/自动评估统一传递代次；历史空编号仍可读取；自动迭代只有当前代次成功完成才触发。
- `labelu/internal/application/response/yolo_eval.py`
  - 评估报告、评估接收结果和评估中心返回代次信息，供审计和问题定位使用。
- `labelu/docs/7gai_phase_one_migration_candidates/gai_p1_09_add_yolo_eval_generations.py`
  - 新增候选迁移脚本；不属于正式 Alembic 版本链，重复索引和重复数据会失败关闭。

## 本地验证

```text
python -m pytest labelu/tests/internal/application/test_yolo_auto_eval.py -q --disable-warnings --tb=short
8 passed, 113 warnings

python -m pytest labelu/tests/internal/application/test_yolo_auto_eval.py labelu/tests/internal/application/service/test_yolo_train_evaluation_dataset_evidence.py labelu/tests/alembic/test_yolo_eval_generation_candidate.py -q --disable-warnings --tb=short
16 passed, 113 warnings

python -m pytest labelu/tests/internal/application/test_yolo_auto_eval.py labelu/tests/internal/application/service/test_yolo_train_evaluation_dataset_evidence.py labelu/tests/internal/application/service/test_yolo_quality_evidence_auto_recovery.py labelu/tests/internal/adapter/routers/test_yolo_edge_validation.py -q --disable-warnings --tb=short
53 passed, 114 warnings

python -m compileall -q labelu/internal labelu/tests/alembic/test_yolo_eval_generation_candidate.py
通过

python -m pytest labelu/tests -q --disable-warnings --tb=short
2651 passed, 56 skipped, 410 warnings

npm run type-check
通过

npm run build
通过，4094 modules transformed
```

新增测试覆盖：

1. 同一评估阶段第一次领取和恢复接管会生成不同代次。
2. 旧代次完成写入被拒绝，新代次可以完成。
3. 旧代次 Provider 迟到报告被拒绝。
4. 同一代次相同 Provider 重试返回同一报告，不重复插入。
5. 候选迁移可重复执行，历史空值保持兼容。

## 迁移与生产边界

本轮没有执行 Alembic，没有连接 `.env` 的 MySQL，没有修改生产表。候选迁移进入正式 head 前仍必须提供：同版本 MySQL 沙箱、脱敏快照、首次/重复升级、历史报告查询、重复键检查、锁等待、`2/10/20/50` 并发、备份恢复和旧代次迟到回放证据。

## 风险与遗留项

- 生产旧数据库没有新增字段时，评估 Worker 必须保持失败关闭，不得直接部署应用代码。
- MySQL 不同版本对可空列唯一索引的行为必须以真实沙箱验证；历史 `operation_id=NULL` 不回填伪造代次。
- 本地 SQLite 通过只证明代码和候选迁移逻辑，不证明在线 DDL、锁等待和生产行数下的并发性能。

本轮只读执行 `python labelu/scripts/migration_release_gate.py --evidence-output .tmp/run0261-migration-gate.json`，结果 `valid=false`：正式 head 和源码闭包通过，但仍缺少沙箱 URL、备份恢复、锁等待及 `2/10/20/50` 并发证据。

## 状态

- 本地代码：完成 L1。
- 本地测试：通过。
- 正式迁移：未放行。
- 生产：未连接、未写入、未部署。
- Git：未提交、未推送，等待用户明确“提交代码”。
