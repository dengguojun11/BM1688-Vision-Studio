# RUN-0145 迁移审计测试契约刷新与联合回归

## 1. 轮次信息

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R09 测试主责；R10 运维协作。
- 工作包：WP2 训练/边端执行基础设施、WP4 第三方 S3 回流闭环。
- 需求编号：`MIG-TEST-CONTRACT-03`、`MIG-PROJECT-SCOPE-AUDIT-02`。
- 交付级别：本地 L1；未提交、未推送、未部署。

## 2. 本轮产出

RUN-0144 已确认正式迁移图和 `experiment_dataset_import` 表结构没有立即缺口。本轮继续清理审计测试中遗留的早期目标，避免测试把已完成的去项目化和 `z7a8b9c0d1e2` 正式 head误报为失败：

- `test_phase_one_migration_plan.py` 使用正式唯一 head `z7a8b9c0d1e2`；迁移数量锁定为当前 134 个版本。
- 项目外键残留数量按当前 ORM 校准为 20 张。
- `strategy_task_integration` 已不含项目外键，从一期迁移计划的兼容段和残留清单移除。
- 保留唯一 head、生产基线、空库升级、租约字段/索引和重复归属等漂移门禁。

本轮没有修改业务 API、正式 Alembic DDL、生产数据或登录角色，也没有恢复项目/业务空间。

## 3. 验收证据

联合执行旧策略入口、S3 兼容转换、Worker 租约、迁移唯一 head、项目范围、空库升级和回流迁移专项：`35 passed, 123 warnings`。

另行确认：`python -m compileall -q labelu/internal` 和 `git diff --check` 通过；迁移/租约/空库专项独立 `15 passed`。

## 4. 真实环境边界

这些结果只证明本地代码和临时 SQLite 契约。尚未证明生产 MySQL 已升级到 `z7a8b9c0d1e2`、Ubuntu/systemd Worker 可启动接管、真实 S3/AICM 与第三方 `201 + PENDING` 兼容、TPU-MLIR/BM1688 真机和生产下载链路。因此 S3、训练和 BM1688 Worker 继续生产失败关闭。

## 5. 下一依赖

R03/R10 先完成同版本 MySQL 沙箱和脱敏生产快照升级演练；R05 提供真实 S3/AICM 脱敏样例；R07 提供 BM1688 真机证据；R09 补双 Worker 和 20 并发锁等待报告。用户明确“提交代码”前不执行 commit、push 或部署。

