# RUN-0180 迁移发布闸门与 MySQL 沙箱证据收口

- 日期和时区：2026-09-20，Asia/Shanghai。
- 方案章节与需求编号：7GAI 方案第 15.8 节并发与发布验收；`MIG-U0B-RELEASE-01`、`MIG-U0B-WORKER-01`。
- 工作包/角色：WP2 数据库迁移与执行器；R03 架构主责，R04 后端、R09 测试、R10 运维协作。

## 本轮解决的问题

1. 新增 `labelu/internal/common/migration_release_gate.py`，只读取 Alembic、ORM 和 JSON 证据，不建立数据库连接。
2. 闸门检查正式 Alembic 是否唯一 head、三张执行表的 ORM 字段/扫描索引/`worker_action` 是否都有正式迁移来源。
3. `labelu/docs/7gai_phase_one_migration_candidates` 下仍有候选文件时直接失败，不能把停留在方案目录的候选当成正式迁移。
4. URL 只接受 SQLite 或本机 `labelu_migration_sandbox*` MySQL；生产 IP、远程 MySQL、正式库名及其他数据库方言直接拒绝。
5. 缺少备份恢复证据或 MySQL 并发/锁等待/旧 fencing token 拒绝证据时 fail-closed。证据只做字段和状态校验，不把 JSON 文件冒充真实执行。
6. `scripts/ops/migrate_deploy.sh` 在任何 `alembic upgrade head` 之前调用闸门；没有沙箱 URL、备份证据和锁等待证据就不连接 `DATABASE_URL`，也不能通过环境变量关闭。

## 代码、页面、数据/API影响

- 代码：新增迁移闸门模块、命令行入口 `labelu/scripts/migration_release_gate.py` 和部署脚本前置检查；不改变业务 API、菜单或页面。
- 数据库：本轮不连接、不写入、不执行正式 Alembic；正式版本目录和生产数据库均未修改。
- 证据接口：部署前必须提供 `LABELU_MIGRATION_SANDBOX_URL`、`LABELU_MIGRATION_BACKUP_EVIDENCE`、`LABELU_MIGRATION_LOCK_WAIT_EVIDENCE`。三者只用于本机沙箱/证据读取。

## 测试与证据

- `python -m pytest labelu/tests/internal/common/test_migration_release_gate.py scripts/tests/test_migration_release_deploy_gate.py -q --disable-warnings --tb=short`：应覆盖当前候选停留 docs 失败、完整证据通过、证据缺失失败、危险 URL 拒绝和部署顺序。
- 现有正式迁移和候选演练测试继续保留；SQLite 只能证明结构/幂等，不能替代同版本 MySQL 的锁等待和执行计划。
- 当前工作区预期为 fail-closed：候选仍位于 docs，真实备份恢复与锁等待证据尚未提供，因此不能放行生产迁移。

## 并发、回退与风险

- 闸门本身无数据库连接，不制造锁；真实 MySQL 演练必须在本机沙箱使用双 Worker、至少 20 并发、短 `lock_wait_timeout`、重复领取计数和旧 token 拒绝证据。
- 迁移失败后的生产恢复仍使用迁移前备份恢复，不执行危险自动降级；已发布模型不回滚，只按模型目录规则禁止后续下载。
- 禁止把生产 `DATABASE_URL` 传给 `--sandbox-url`；生产 URL 被识别为远程/正式库时直接失败。

## 当前验收等级与遗留项

- 本轮达到本地 L1：静态闭包、URL 安全和证据 fail-closed 逻辑可测试。
- 未达到预生产/生产 L2/L3：候选迁移尚未合入正式唯一替换链；没有同版本 MySQL 脱敏快照、备份恢复、锁等待、20 并发、重启接管和生产结构对账证据。
- 下一依赖：R03/R10 在同版本本机 MySQL 沙箱执行候选演练并产出不可篡改证据；候选合入正式链后重新运行本闸门；之后才允许用户明确“提交代码”并进入提交、部署和生产只读验证。

- 提交/推送/部署状态：未提交、未推送、未部署；本轮没有生产写入。
