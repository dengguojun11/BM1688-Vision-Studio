# RUN-0272 迁移证据来源可追溯闸门（2026-09-20）

## 背景

迁移发布闸门此前只检查备份恢复、锁等待和并发矩阵 JSON 的业务字段。只要有人手工写出 `status=PASSED`，理论上仍可能把无法确认来源的证据带入发布链，无法证明证据来自本机隔离 MySQL 沙箱。

## 修改

- `migration_release_gate.py` 对三类证据统一要求 `evidence_meta`：
  - `evidence_id`
  - `generated_at`
  - `script_version`
  - `git_revision`
  - `database_name`
  - `mysql_version`
- `database_name` 必须以 `labelu_migration_sandbox` 开头。
- `git_revision` 必须是至少 7 位十六进制提交摘要，`unknown`、`workspace`、`dirty` 和其他非摘要值全部拒绝。
- `rehearse_mysql_concurrency_matrix.py` 在矩阵结果中记录探针得到的数据库名和 MySQL 版本，继续保持只允许本机隔离库的约束。
- 矩阵单元测试显式替换 `probe_mysql`，不会因测试伪造 URL 而连接真实 MySQL。

## 验证

```text
python -m pytest labelu/tests/internal/common/test_migration_release_gate.py scripts/tests/test_rehearse_mysql_concurrency_matrix.py -q --disable-warnings --tb=short
18 passed, 123 warnings

python -m compileall -q labelu/internal/common/migration_release_gate.py scripts/rehearse_mysql_concurrency_matrix.py
通过

python -m pytest labelu/tests -q --disable-warnings --tb=short
2659 passed, 56 skipped, 410 warnings

cd frontend/labelU-Kit/apps/frontend
npm run type-check
通过
npm run build
通过（仅保留既有分包体积和依赖模块提示）

cd ../../..
node --test frontend/extensions/tests/*.test.mjs
22 passed

python labelu/scripts/migration_release_gate.py
退出码 1（按设计失败关闭：评估代次候选未正式合入，且未提供沙箱/备份/锁等待/并发矩阵证据）
```

新增失败用例覆盖：缺少 `evidence_meta`、错误沙箱库名、不可追溯提交摘要，以及不完整的 `2/10/20/50` 矩阵。

## 放行结论

- 本地代码和测试通过，迁移发布闸门仍保持失败关闭。
- 后端全量回归通过；跳过项仍是需要真实设备或外部服务的验收项。
- 前端类型检查、生产构建和 22 项扩展页面契约测试通过；构建输出的分包体积提示不阻断构建。
- 当前没有真实备份恢复、锁等待、四档并发矩阵和正式评估代次迁移证据，因此不得执行正式迁移。
- 未连接生产、未连接业务库、未提交、未推送、未部署。
