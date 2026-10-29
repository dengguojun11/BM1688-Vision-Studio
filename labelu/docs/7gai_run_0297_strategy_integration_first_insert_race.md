# RUN-0297 策略回流首次并发插入竞态（2026-09-20）

## 问题

策略回流入口按 `external_task_id` 先查询再创建 `strategy_task_integration`。两个首次请求同时到达时，两个事务都可能查询为空；数据库唯一约束最终只允许一个插入成功，另一个请求原来会直接抛出 `IntegrityError` 并返回 500。

## 修复规则

1. 预查询只作为快速路径，不能代替数据库唯一约束。
2. 首次插入由唯一键裁决；若插入触发 `IntegrityError`，先回滚当前事务，再按 `external_task_id` 加锁回读已提交的赢家记录。
3. 回读赢家后仍执行完整 `request_sha256`/请求正文一致性校验；相同请求继续幂等处理，不同请求返回 409，不能覆盖赢家参数。
4. 回读不到赢家时保留原始 `IntegrityError`，失败关闭，避免生成没有持久化依据的假成功。
5. 后续统一回流 `accept()` 仍按 `package_id` 和聚合键做幂等与并发收敛；本轮不重复改造其已存在的聚合组竞态处理。

## 代码与测试

- `labelu/internal/application/service/strategy_task_integration.py`
  - 新增 `_create_or_reload_integration()`，封装创建、事务回滚、唯一键竞态回读。
  - 首次竞争回读后设置 `was_existing=True` 并复用既有请求一致性校验。
  - 引入 SQLAlchemy `IntegrityError` 专门处理唯一键竞态。
- `labelu/tests/internal/application/service/test_strategy_task_integration_create_race.py`
  - 模拟插入竞争并确认回读赢家记录；
  - 模拟唯一键异常但回读不到赢家，确认仍失败关闭。

## 本地验证（L1）

```text
python -m pytest labelu/tests/internal/application/service/test_strategy_task_integration_create_race.py labelu/tests/internal/adapter/routers/test_strategy_task_integration.py -q
11 passed

python -m compileall -q labelu/internal/application/service/strategy_task_integration.py labelu/tests/internal/application/service/test_strategy_task_integration_create_race.py
通过

git diff --check -- labelu/internal/application/service/strategy_task_integration.py labelu/tests/internal/application/service/test_strategy_task_integration_create_race.py
通过
```

## 并发与性能边界

- 正常无竞争请求仍只做一次预查询和一次插入；没有增加额外锁。
- 只有发生唯一键竞争的败者才进行一次回滚后的加锁查询；不会在事务失败状态下继续写入。
- 同一 `external_task_id` 的不同请求不会因为竞争回读而互相覆盖，摘要不一致返回 409。
- 本地专项模拟了数据库异常路径，但未替代真实 MySQL 隔离级别、锁等待和多进程压测。

## 环境等级与未完成项

当前仅达到本地 L1：SQLite/服务层专项通过；未连接生产 MySQL、第三方 S3、systemd Worker 或真实回流入口，未提交、未推送、未部署。

生产放行前由 R09/R10 在同版本 MySQL 执行 2/10/20 并发首次相同请求和不同请求压测，核对只生成一条 `strategy_task_integration`、只生成一个统一导入包，败者得到成功幂等响应或 409，且没有重复任务、附件和样本副作用。

