# RUN-0270 MySQL 2/10/20/50 并发矩阵编排入口（2026-09-20）

## 目标

迁移发布闸门要求 `2/10/20/50` 四档真实 MySQL 并发证据。已有 `rehearse_mysql_execution_fencing.py` 只能单次执行一个并发档位，容易出现只跑 20 并发却误提交矩阵证据的问题。

## 交付

- 新增 `scripts/rehearse_mysql_concurrency_matrix.py`。
- 固定执行档位：`2、10、20、50`，不允许调用方改成其他集合。
- 统一输出 `status/database_type/matrix`，每档包含：并发数、最大锁等待、重复领取数、旧 token 拒绝、最终状态和原始报告。
- 任一档异常时保留已完成档位和失败原因，整体返回 `FAILED`；不会伪造通过项。
- 默认只读失败关闭；必须同时显式指定：
  - `--模式 演练`；
  - `--允许修改`；
  - 本机 `labelu_migration_sandbox*` MySQL URL；
  - `LABELU_MYSQL_REHEARSAL_CONFIRM=YES`。
- 不接受远程主机、业务库、生产库或 SQLite，不执行备份恢复以外的业务表写入，只使用演练临时表并清理。

## 验证

```text
python -m pytest scripts/tests/test_rehearse_mysql_concurrency_matrix.py labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py labelu/tests/internal/common/test_migration_release_gate.py -q --disable-warnings --tb=short
20 passed, 123 warnings

python scripts/rehearse_mysql_concurrency_matrix.py --url mysql+pymysql://local/labelu_migration_sandbox_test
退出码：2，状态：BLOCKED（未显式进入写入演练）
```

## 真实环境边界

- 本轮未连接 MySQL、未建表、未执行 DDL/DML。
- 本机仍没有已授权的 `labelu_migration_sandbox*` 沙箱、脱敏快照和恢复目标，因此没有生成真实矩阵证据。
- 该脚本只解决四档编排和证据格式，不替代真实 MySQL、生产快照、备份恢复和业务表级锁等待验证。

## 状态

- 本地代码：L1，入口和失败关闭测试通过。
- 正式迁移：仍未放行。
- Git/部署：未提交、未推送、未部署，等待用户明确“提交代码”。

