# RUN-0343 自动训练看门狗分页扫描（2026-09-21）

## 问题

旧看门狗分别对训练作业、BM1688 导出、边端验证和发布轮次执行 `.all()`，业务历史增长后会一次性把全部自有自动训练记录加载进内存，再逐条同步。同步时间变长会压缩调度租约剩余时间，增加旧进程失租后继续处理的风险。

## 改造

- 新增 `AUTO_TRAINING_WATCHDOG_SYNC_BATCH_SIZE`，默认 `20`，允许范围 `1-100`。
- 新增 `_iter_watchdog_id_batches()`，真实 SQLAlchemy 会话按主键使用 `id > last_id`、`ORDER BY id`、`LIMIT batch_size` 查询。
- 训练、BM1688、边端和 AICM 发布四类记录都按页处理；每页开始和每条同步前调用已有 scheduler lease/fencing 守卫。
- 不改变状态机、业务计数、失败隔离、页面和 HTTP API；只改变内部扫描的内存上限和租约检查频率。
- 轻量测试替身没有数据库绑定时保留单批路径，避免测试假对象伪造生产 SQL 行为。

## 验证

新增看门狗 keyset 分页测试，确认三页数据按顺序返回为 `[1, 2]`、`[3]`，并执行自动训练服务专项：

```text
$files=Get-ChildItem labelu/tests/internal/application/service -Filter 'test_auto_training*.py' | Select-Object -ExpandProperty FullName
python -m pytest $files -q --disable-warnings --tb=short
183 passed
```

另行通过 `py_compile` 和 `git diff --check`。

## 未证明内容

- MySQL 真实执行计划、索引命中、锁等待和连接池容量；
- 长周期真实自动训练计划在 20/50 并发下的租约剩余时间和扫描耗时；
- 真实 GPU、TPU-MLIR、BM1688、AICM、S3 和生产 systemd Worker。

## 状态

本地 L1 完成。生产自动训练计划和 Worker 开关继续失败关闭，未提交、未推送、未部署，未修改生产数据。
