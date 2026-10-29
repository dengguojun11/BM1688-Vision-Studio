# RUN-0311 BM1688 模型目录并发摘要合并（2026-09-20，本地 L1）

## 1. 目标

针对 `RUN-0310` 发现的生产模型中心并发排队问题，减少同一筛选条件下多个请求重复加载全部 BM1688 导出记录和重复计算质量摘要的开销。只做并发合并，不改变摘要口径、不引入结果缓存、不改变权限和分页行为。

## 2. 实现内容

- 在 `yolo_bm1688_export.list_exports_global` 中把全局摘要计算抽为 single-flight。
- 第一个请求负责读取全量摘要数据、批量查询边端状态和训练作业并计算质量门禁；相同用户、筛选条件和总数的并发请求等待同一结果。
- 计算完成后立即移除 flight；后续请求重新读取数据库，不使用旧摘要。
- 列表页当前页仍独立按用户权限查询和组装，发布/下载/边端状态字段不共享跨请求对象。
- 计算异常会传递给等待请求，并在 finally 中释放 flight，避免永久等待。

## 3. 修改文件与影响

| 层级 | 文件 | 影响 |
|---|---|---|
| 服务 | `labelu/internal/application/service/yolo_bm1688_export.py` | 增加摘要并发合并和全量摘要计算隔离 |
| 测试 | `labelu/tests/internal/application/service/test_catalog_performance_paths.py` | 增加两个并发调用只执行一次摘要计算的断言 |
| 文档 | 本记录、`7gai_role_delivery_plan.md`、`7gai_delivery_ledger.md` | 记录本地证据和生产依赖 |

## 4. 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_catalog_performance_paths.py labelu/tests/internal/adapter/routers/test_yolo_bm1688_exports.py -q --disable-warnings --tb=short
39 passed, 1 skipped, 119 warnings

python -m pytest labelu/tests -q --disable-warnings --tb=short
2707 passed, 56 skipped, 420 warnings

python -m compileall -q labelu
git diff --check
python labelu/scripts/release_gate.py
migration_heads=ze1f2a3b4c5d
security_valid=True
backup_restore_ok=True
release_gate_valid=True
```

覆盖：页内数据只组装当前页、全局摘要仍使用全部记录、相同筛选条件并发只执行一次、异常释放等待者、既有模型中心路由回归。

## 5. 尚未证明的内容

- single-flight 只在同一 API 进程内合并；多进程/多副本仍需通过同版本生产架构实测。
- 没有替代数据库聚合。真实 MySQL 连接池等待、锁等待、慢 SQL 和质量文件读取耗时仍未测量。
- 尚未在生产部署，不能把 `RUN-0310` 的 5 并发数据改写为已修复证据。

## 6. 验收等级

本轮为 **L1 本地完成**。提交前需补充 2/5/10/20 并发本地或预生产证据；部署后必须重测单请求 P95、5 并发 P95 和摘要计数一致性。未提交、未推送、未部署。
