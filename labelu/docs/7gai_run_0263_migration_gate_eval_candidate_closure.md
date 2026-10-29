# RUN-0263 评估执行代次迁移闸门补漏（2026-09-20）

## 发现

迁移发布闸门此前只检查调度租约候选 `gai_p1_08_add_dispatch_leases.py`，没有检查评估执行代次候选 `gai_p1_09_add_yolo_eval_generations.py`。因此闸门可能在 `yolo_train_job` 的评估代次字段、`yolo_eval_report.operation_id` 和同代次唯一索引尚未进入正式 Alembic 链时误报通过。

## 修改

- 将原“候选迁移已合入正式 versions”拆为：
  - `调度租约候选迁移已合入正式 versions`；
  - `评估代次候选迁移已合入正式 versions`。
- 当前评估候选没有正式替代修订号，因此明确失败关闭；不会把候选脚本当成正式迁移执行。
- 保持闸门只读：不连接数据库，不执行迁移，不生成伪造的 MySQL、锁等待、备份恢复或并发证据。

## 验证

```text
python -m pytest labelu/tests/internal/common/test_migration_release_gate.py -q --disable-warnings --tb=short
10 passed

python labelu/scripts/migration_release_gate.py --evidence-output .tmp/run0262-migration-gate.json
退出码：1
```

当前失败原因：评估代次候选未进入正式 Alembic、未提供沙箱 URL、备份恢复、MySQL 锁等待和 `2/10/20/50` 并发证据。该失败是预期的放行阻断，不是测试缺陷。

## 放行结论

- 本地代码：L1，闸门逻辑和测试通过。
- 正式迁移：未生成，等待同版本 MySQL 沙箱首次/重复升级、历史报告查询、重复键、锁等待、备份恢复和并发演练。
- 生产：未连接、未写入、未部署。
- Git：未提交、未推送，等待用户明确“提交代码”。

