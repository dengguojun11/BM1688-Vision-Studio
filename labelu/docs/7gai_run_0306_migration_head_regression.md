# RUN-0306 Alembic 正式 head 与 legacy 对账回归（2026-09-20，本地 L1）

## 1. 本轮结论

当前正式 Alembic 链从生产基线 `h7i8j9k0l1m2` 可达，唯一正式 head 已推进到
`ze1f2a3b4c5d`。`za0b1c2d3e4f` 仍是合法历史迁移节点，不能删除、改写或作为
当前 head 继续固定在测试中。

本轮将审计基线从旧 head 更新为当前 head，并把 `zb1c2d3e4f5g`、
`zc1d2e3f4a5b`、`zd1e2f3a4b5c`、`ze1f2a3b4c5d` 四个正式新增迁移补入
`LEGACY_SUFFIX_PLAN`。因此生产基线之后的迁移仍保持逐修订、对象级映射，未通过
删除历史节点或回退迁移掩盖差异。

## 2. 变更范围

- `phase_one_migration_rebuild_audit.py`
  - `CURRENT_LOCAL_HEAD` 更新为 `ze1f2a3b4c5d`；
  - legacy 对账由 47 条扩展为 51 条，新增四条预标注/附件迁移映射；
  - 保留 `za0b1c2d3e4f` 的创建幂等映射。
- Alembic 审计测试
  - 正式 head 断言更新为 `ze1f2a3b4c5d`；
  - legacy 计数更新为 51，替换项更新为 39；
  - 正式迁移图总节点数更新为 144。

## 3. 本地验证

```text
python -m pytest \
  labelu/tests/alembic/test_execution_lease_candidate_alignment.py \
  labelu/tests/alembic/test_execution_lease_migration_audit.py \
  labelu/tests/alembic/test_execution_lease_formal_closure.py \
  labelu/tests/alembic/test_phase_one_migration_rehearsal.py \
  labelu/tests/alembic/test_phase_one_migration_plan.py \
  labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py \
  -q --disable-warnings --tb=short
```

结果：`17 passed`，退出码 `0`；`git diff --check` 通过（仅存在工作区已有的
换行格式提示）。

## 4. 生产边界

本轮仅修改本地审计常量、legacy 映射和测试基线；未连接生产、未执行 Alembic
迁移、未写生产数据库、未提交、未推送、未部署。生产迁移仍须在用户明确“提交代码”
后，按备份、同版本 MySQL 隔离演练、迁移、部署和线上只读复核顺序推进。
