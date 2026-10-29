# RUN-0236 自动训练看门狗 Outbox 租约心跳

## 1. 问题确认

生产自动训练看门狗会直接调用 `process_outbox_batch`。它原先只续调度器租约，没有续当前 Outbox 行租约；当 S3 回流、训练派发、BM1688 编译或边端验证事件处理时间超过 Outbox 租约时，新 Worker 可以接管，而旧看门狗仍可能继续写入。

## 2. 本轮范围

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP2 | 区分调度器领导租约与 Outbox 行租约，要求二者分别续期 |
| R04 后端 | WP1/WP3 | 看门狗为真实数据库会话注入独立短会话心跳；Outbox 初始租约至少 60 秒，续期不复用处理会话 |
| R09 测试 | WP5 | 增加真实数据库会话分支的心跳注入回归，并执行看门狗联合套件 |
| R10 运维 | OPS | 明确 Worker 长任务必须保留心跳配置；真实 MySQL 并发、锁等待和重启接管仍待生产前执行 |

## 3. 验收证据

```text
python -m pytest labelu/tests/internal/application/service/test_auto_training_watchdog.py -q --disable-warnings --tb=short
7 passed, 113 warnings in 1.40s

python -m pytest labelu/tests/internal/application/service/test_auto_training_outbox.py labelu/tests/internal/application/service/test_auto_training_dispatch.py labelu/tests/internal/application/service/test_auto_training_events.py labelu/tests/internal/application/service/test_auto_training_watchdog.py -q --disable-warnings --tb=short
51 passed, 116 warnings in 11.81s
```

本批次新增断言：真实数据库会话分支传入 `heartbeat` 回调和 60 秒初始租约；轻量测试会话保持兼容。完整 Outbox/自动训练联合回归在本批次完成后执行并记录于台账。

## 4. 生产边界

本轮只修改本地看门狗、测试和文档；未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688，未启动自动训练开关，未提交、未推送、未部署。生产放行必须验证心跳续期 SQL、`2/10/20/50` 并发领取、锁等待、旧 token 拒绝和 Worker 重启接管。
