# RUN-0257 异步评估执行代次与迁移闸门现状审计

- 日期：2026-09-20（Asia/Shanghai）
- 类型：本地只读代码、ORM、迁移和测试审计
- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维
- 工作包：WP2（训练与评估执行器）、WP5（迁移与生产放行）
- 验收等级：L1 审计；未生成正式迁移、未连接 `.env` MySQL、未执行生产写入

## 1. 审计范围与安全边界

本轮只读取 RUN-0246 设计、当前 ORM、评估服务、持久化层、Alembic 源码和本地测试；未读取或连接 `.env` 中的数据库 URL。没有执行 `alembic upgrade/downgrade`，没有修改生产数据库，也没有生成新的正式迁移文件。

核对对象：

- `labelu/internal/domain/models/yolo_train_job.py`
- `labelu/internal/domain/models/yolo_eval_report.py`
- `labelu/internal/adapter/persistence/crud_yolo_train_job.py`
- `labelu/internal/adapter/persistence/crud_yolo_eval_report.py`
- `labelu/internal/application/service/yolo_train_evaluation.py`
- `labelu/alembic_labelu/versions/e6f7a8b9c0d1_add_yolo_eval_report.py`
- `labelu/docs/7gai_run_0246_eval_generation_migration_design.md`

## 2. 与 RUN-0246 设计的逐项对照

| 设计要求 | 当前代码事实 | 结论 |
|---|---|---|
| 训练作业保存全量评估当前执行代次 | `YoloTrainJob` 只有 `ai_eval_full_status`，没有 `ai_eval_full_operation_id` | 未实现 |
| 训练作业保存下一轮评估当前执行代次 | 只有 `ai_eval_next_status`，没有 `ai_eval_next_operation_id` | 未实现 |
| 评估报告记录来源代次 | `YoloEvalReport` 没有 `operation_id` | 未实现 |
| 同一作业/类型/Provider/代次唯一 | 报告表没有对应唯一索引；`crud_yolo_eval_report.create()` 直接插入 | 未实现 |
| 领取时状态和代次一次条件更新 | `update_eval_status_if_current()` 只比较状态字段并写 `RUNNING` | 部分实现，仅能防空闲状态重复领取 |
| 完成/失败只允许当前代次写入 | `_run_yolo_eval_sync()` 和 `_set_job_eval_flag()` 按作业状态直接写回 | 未实现 |
| 旧进程迟到报告被拒绝 | 无报告代次条件，旧 Provider 结果仍可能插入 | 未实现 |
| 手工评估和自动评估共用代次状态机 | 两条路径都没有代次参数和代次条件 | 未实现 |

当前实际能力是：数据库条件比较更新可以让两个进程在 `NULL/RUNNING/FAILED` 状态转换时只有一个成功，但进程失联后重新把同一阶段置为 `RUNNING` 时，旧进程和新进程都没有可比较的执行身份。`_AUTO_EVAL_SCHEDULED` 只在单个 Python 进程内生效，不能作为跨进程租约。

## 3. 已执行本地验证

命令：

```text
python -m pytest labelu/tests/internal/application/test_yolo_auto_eval.py labelu/tests/internal/common/test_migration_release_gate.py labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py -q --disable-warnings --tb=short
```

结果：`23 passed, 123 warnings in 7.81s`。

这些测试证明：现有状态 CAS、自动评估顺序、孤儿 `RUNNING` 状态恢复触发和迁移闸门字段/证据格式检查可以运行；不证明 RUN-0246 要求的 operation_id、报告唯一性、跨进程旧代次拒绝或真实 MySQL 锁行为。

迁移闸门只读命令：

```text
python labelu/scripts/migration_release_gate.py --evidence-output .tmp/run0257-migration-gate.json
```

关键结果：

- 正式 Alembic head 唯一：`z9f3a4b5c6d7`。
- ORM 字段/索引闭包和历史候选迁移替代关系检查通过。
- 演练 URL 安全：失败，未提供沙箱 URL。
- 备份恢复证据：失败，未提供文件。
- MySQL 锁等待证据：失败，未提供文件。
- `2/10/20/50` 并发矩阵：失败，未提供文件。
- 总体 `valid=false`，迁移闸门失败关闭。

## 4. 当前阻断与风险

1. **旧评估进程可能写入新评估结果**：评估重启接管后，旧进程调用 Provider 返回，当前代码没有代次条件，可能插入重复或过时报告，并把阶段状态覆盖为 `COMPLETED`。
2. **报告重复不能依赖数据库**：当前每次手工评估会先按作业和类型删除历史报告，再重新插入；自动评估不带唯一请求键。跨进程并发或 Provider 重试不能保证一个代次只产生一个结果。
3. **历史数据兼容尚未验证**：RUN-0246 设计允许历史 `operation_id=NULL`，但新增复合唯一索引、不同 MySQL 版本的 NULL 语义、既有重复报告清理均未在同版本 MySQL 沙箱验证。
4. **迁移证据不具备**：本地 SQLite/`Base.metadata.create_all` 不能证明生产表结构、在线 DDL、锁等待、备份恢复或生产行数下的并发行为。

## 5. 后续实现闸门（本轮不执行）

取得同版本 MySQL 沙箱和脱敏快照后，才允许进入候选迁移实现，至少应覆盖：

1. 增加训练作业两个可空代次字段和报告 `operation_id` 字段；新评估领取必须生成新的随机代次。
2. 报告写入按作业、报告类型、Provider、代次执行唯一幂等；唯一冲突读取已存在结果，不能重复触发后续副作用。
3. 完成、失败、续租和恢复均按当前代次条件更新；旧代次只记录拒绝原因，不改变新代次状态。
4. 在 MySQL 沙箱执行首次升级、重复升级、旧报告查询、备份恢复、锁等待和 `2/10/20/50` 并发矩阵；逐档保存重复领取数、最大锁等待、旧代次拒绝数和恢复耗时。
5. 候选迁移通过闸门后，再更新正式 Alembic head；在此之前自动评估 Worker 保持 `fail-closed`，不得把本地 L1 结果标记为生产完成。

## 6. 交付状态

- 本轮变更：仅新增本审计文档。
- 正式迁移：未生成。
- 生产数据库：未连接、未修改。
- 提交、推送、部署：未执行，等待用户明确授权。
- 结论：RUN-0246 仍为架构设计/迁移前置状态；异步评估执行代次和报告唯一幂等键是当前生产放行阻断项。

## 后续状态

RUN-0261 已在本地代码补齐执行代次、报告幂等和旧代次拒绝；本审计记录保留为实现前的阻断基线。正式数据库迁移和生产放行条件仍未完成。
