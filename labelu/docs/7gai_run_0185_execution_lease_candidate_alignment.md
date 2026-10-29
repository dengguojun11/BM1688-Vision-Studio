# RUN-0185 执行租约候选与正式 Alembic 链只读对账

- 日期和时区：2026-09-20，Asia/Shanghai。
- 工作包/角色：WP2 数据库迁移与执行器；R03 架构主责，R04 后端、R09 测试协作。
- 审计脚本：`labelu/scripts/audit_execution_lease_candidate_alignment.py`。
- 证据文件：`.tmp/run0185-alignment.json`（本地命令输出；不含生产数据）。

## 对账结论

| 对象 | 结果 |
|---|---|
| 正式唯一 head | `z9f3a4b5c6d7`，唯一 head 通过 |
| 生产基线 | `h7i8j9k0l1m2`，沿正式父链可到达当前 head |
| 正式租约迁移 | `z4a5b6c7d8e9`，父版本 `z3a4b5c6d7e8` |
| 候选租约迁移 | `gai_p1_08`，父版本 `gai_p1_07` |
| 字段对账 | 三表字段集合完全一致：无正式独有、无候选独有 |
| 索引对账 | 三表索引名称和列顺序完全一致 |
| 候选是否在正式 versions | 否，仍在 `labelu/docs/7gai_phase_one_migration_candidates` |
| 候选父版本是否存在正式图 | 否 |
| 候选状态 | 已声明 `SUPERSEDED_BY=z4a5b6c7d8e9`，仅保留历史对账 |
| 直接收口是否安全 | 否，必须保持 fail-closed |

## 最小阻断证据

1. 候选声明父版本 `gai_p1_07`，正式 Alembic 图中不存在该 revision；直接把候选复制进正式 `versions` 会形成无法解析的父链，而不是合法的线性迁移。
2. 候选和正式 `z4...` 的字段/索引语义虽然一致，但这只能证明内容可复用，不能证明 revision 身份和父链可合入。
3. 当前正式 head 已从生产基线 `h7...` 连续可达；正式 `z4...` 已覆盖候选能力，不能把 `gai_p1_08` 作为第二条链挂入，也不需要新增空迁移。

## 推荐的最小后续动作

- 不移动、不删除候选文件；保留 `SUPERSEDED_BY=z4a5b6c7d8e9` 标记用于历史重建和审计，禁止再次执行或复制到正式目录。
- 当前不新增执行租约正式迁移；继续运行唯一 head、字段/索引闭包和生产基线可达性审计，若出现第二个 head、未知 parent 或正式来源缺失，迁移发布闸门继续失败。
- 仍需同版本 MySQL 的备份恢复、重复升级、锁等待、20 并发领取和旧 fencing token 拒绝证据；本轮没有连接 MySQL 或生产。

## 验证与状态

- `python -m pytest labelu/tests/alembic/test_execution_lease_candidate_alignment.py -q --disable-warnings --tb=short`：`2 passed`。
- 只读审计命令按预期返回退出码 `1`，因为直接合入不安全；输出明确列出两个父链阻断项。
- `compileall` 与 `git diff --check` 通过。
- 未提交、未推送、未部署；未修改正式迁移目录、生产数据库或候选文件。
