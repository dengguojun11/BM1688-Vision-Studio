# RUN-0205 生产前 MySQL、并发矩阵与备份恢复证据门禁

- 日期和时区：2026-09-20，Asia/Shanghai。
- 负责角色：R10 运维、R09 测试；R03 架构、R04 后端协作；WP0/WP5。
- 目标：把“生产前真实 MySQL/并发/迁移演练缺口”变成可执行、失败关闭的本地只读门禁。
- 安全边界：门禁只读取 Alembic 源码和显式 JSON 证据；不建立数据库连接、不执行 DDL、不访问生产、S3、AICM、TPU-MLIR 或 BM1688。

## 本轮补齐

现有 `migration_release_gate` 已检查唯一 head、ORM/迁移闭包、候选迁移状态、沙箱 URL、备份恢复字段和一组 MySQL 锁等待证据。本轮补充：

1. 强制要求独立的 `2/10/20/50` 并发矩阵证据；每一档必须通过、重复领取为 0、旧 token 被拒绝，并记录最大锁等待时间。
2. 备份恢复证据必须明确备份来源 `backup_path`、备份摘要 `backup_sha256`、首次升级、重复升级、独立恢复目标 `restore_target` 和恢复结果，且数据库类型必须是 MySQL。
3. 命令行新增 `--concurrency-evidence`，仍保持只读，不因为传入 MySQL URL 而主动连接数据库。

修改范围：

- `labelu/internal/common/migration_release_gate.py`
- `labelu/scripts/migration_release_gate.py`
- `labelu/tests/internal/common/test_migration_release_gate.py`

没有修改业务 Worker、正式 Alembic DDL、生产配置或数据库。

## 证据文件格式

备份恢复证据示例：

```json
{
  "status": "PASSED",
  "database_type": "mysql",
  "backup_path": "/evidence/labelu-before-migration.sql.gz",
  "backup_sha256": "64位十六进制摘要",
  "upgrade_result": "PASSED",
  "repeat_upgrade_result": "PASSED",
  "restore_target": "独立恢复实例标识",
  "restore_result": "PASSED"
}
```

并发矩阵证据示例：

```json
{
  "status": "PASSED",
  "database_type": "mysql",
  "matrix": [
    {"status": "PASSED", "concurrent_workers": 2, "max_lock_wait_seconds": 0.2, "duplicate_claims": 0, "old_token_rejected": true},
    {"status": "PASSED", "concurrent_workers": 10, "max_lock_wait_seconds": 0.6, "duplicate_claims": 0, "old_token_rejected": true},
    {"status": "PASSED", "concurrent_workers": 20, "max_lock_wait_seconds": 1.1, "duplicate_claims": 0, "old_token_rejected": true},
    {"status": "PASSED", "concurrent_workers": 50, "max_lock_wait_seconds": 2.8, "duplicate_claims": 0, "old_token_rejected": true}
  ]
}
```

数值仅为格式示例，不是平台默认阈值。实际阈值须按同版本 MySQL、生产快照行数、`innodb_lock_wait_timeout` 和业务 SLO 由 R03/R10 复核后填入；门禁只判断证据完整和业务不变量，不替业务方擅自设定性能标准。

## 执行顺序

先在本机隔离库执行 DDL/并发演练并导出证据，再执行只读门禁：

```text
# 仅本机 labelu_migration_sandbox* MySQL，显式写入授权；禁止生产 URL
python labelu/scripts/rehearse_dispatch_lease_migration.py --url <本机沙箱URL> --允许修改
python labelu/scripts/rehearse_mysql_execution_fencing.py --模式 演练 --允许修改 --并发数 20

# 只读门禁，不连接数据库；证据文件由上面的受控演练或备份恢复流程生成
python labelu/scripts/migration_release_gate.py \
  --sandbox-url mysql+pymysql://<本机沙箱>/labelu_migration_sandbox_x \
  --backup-evidence .tmp/evidence/backup_restore.json \
  --lock-wait-evidence .tmp/evidence/lock_wait_summary.json \
  --concurrency-evidence .tmp/evidence/concurrency_matrix.json \
  --evidence-output .tmp/evidence/migration_gate_report.json
```

门禁通过前，正式迁移、独立训练/BM1688/边端/S3 Worker 开关和生产 DDL 均保持关闭。`rehearse_mysql_execution_fencing.py` 当前是通用临时表演练，不能替代四张业务表在同版本 MySQL 的逐表证据；需要另行把训练、BM1688、边端和 S3 的字段映射写入矩阵记录。

## 本地验证

```text
$env:LABELU_TEST_DB_NAME='run0205-gate'
python -m pytest labelu/tests/internal/common/test_migration_release_gate.py -q --disable-warnings --tb=short
```

结果：`10 passed`。覆盖完整 2/10/20/50 矩阵通过、缺档位失败、缺备份/锁等待/矩阵证据失败、候选迁移未标记失败、危险 URL 拒绝和本机沙箱 URL 仅解析不连接。

```text
python -m py_compile labelu/internal/common/migration_release_gate.py labelu/scripts/migration_release_gate.py
git diff --check
```

均通过。

## 仍然阻断的真实证据

- 当前门禁没有伪造或替代真实 MySQL 2/10/20/50 结果；未提供证据时必然失败。
- 未完成生产同版本脱敏快照的 22 张项目外键表结构、索引、字符集、行数摘要对账。
- 未完成大表在线 DDL、元数据锁、失败中止/重试和备份恢复到独立实例的真实记录。
- 未按四张业务表分别完成训练、BM1688、边端和 S3 的 MySQL 领取/接管矩阵。
- 未执行 Worker 重启、GPU/板卡释放、真实 S3 下载、TPU-MLIR、BM1688 或生产小流量。

## 状态

- 本地 L1：只读门禁代码和测试完成。
- 生产/MySQL：未连接、未写入、未部署；生产放行仍阻断。
- commit/push/deploy：未提交、未推送、未部署。
