# RUN-0188 正式迁移覆盖候选租约草稿

**日期**：2026-09-20（Asia/Shanghai）  
**环境级别**：本地 L1；未提交、未推送、未部署  
**责任角色**：R03 架构、R04 后端、R09 测试、R10 运维  
**关联工作包**：WP2 迁移与 Worker、WP5 测试验收

## 1. 结论

只读对账确认正式迁移 `z4a5b6c7d8e9_add_execution_leases_and_bm1688_worker_actions.py`
已经包含候选 `gai_p1_08` 的全部租约字段、`worker_action`、扫描索引和列顺序。
候选文件的 `gai_p1_07` 父版本不在正式 Alembic 图，因此不能作为第二条迁移链执行；
它现在明确标记 `SUPERSEDED_BY = z4a5b6c7d8e9`，仅用于历史重建和语义对账。

## 2. 本轮改动

- `labelu/docs/7gai_phase_one_migration_candidates/gai_p1_08_add_dispatch_leases.py`
  增加正式覆盖标记和中文说明，禁止再次复制到 `versions`。
- `labelu/internal/common/migration_release_gate.py`
  增加候选状态校验：只有候选明确声明由当前正式版本覆盖，且正式 ORM/迁移闭包通过时，
  才将候选检查判为通过；未标记候选仍失败关闭。
- `labelu/tests/internal/common/test_migration_release_gate.py`
  增加正式覆盖通过和未标记候选阻断场景。
- `labelu/docs/7gai_delivery_ledger.md`、`labelu/docs/7gai_role_delivery_plan.md`
  同步本轮记录和 RUN 编号。

## 3. 并发与失败关闭

- 不执行任何数据库连接、升级、回滚或生产写入。
- 候选状态只影响迁移发布前的静态闸门，不改变正式 Alembic 图。
- 未标记、无法加载或声明其他正式版本的候选仍然阻断；唯一 head、ORM 字段/索引来源、
  沙箱 URL、备份恢复和 MySQL 锁等待证据仍必须全部通过。
- 模型发布不使用数据库回滚；已发布模型的问题处理仍按“禁止后续下载并发布替代模型”。

## 4. 本地证据

命令：

```text
python -m pytest labelu/tests/internal/common/test_migration_release_gate.py \
  labelu/tests/alembic/test_execution_lease_candidate_alignment.py \
  labelu/tests/alembic/test_dispatch_lease_migration_candidate.py -q --disable-warnings --tb=short
```

结果：`16 passed`。`compileall` 通过。

测试中的备份、锁等待和沙箱 URL 是测试夹具，不是 MySQL 生产证据；当前验收等级仍为 L1。

## 5. 下一步与放行边界

- R10 仍需在同版本本机 MySQL 沙箱完成真实备份恢复、重复升级、20 并发领取、锁等待和旧
  fencing token 拒绝，并把证据文件交给迁移闸门校验。
- 用户明确“提交代码”后，才能提交、推送和部署；部署前不连接生产数据库。
- 生产仍需重新探针、两类账号回放、四个 Worker 重启接管和真实 S3/AICM/BM1688 验收。

