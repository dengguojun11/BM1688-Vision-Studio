# RUN-0235 Outbox 常驻循环租约心跳透传

## 1. 问题确认

单次 `process_outbox_batch` 已支持租约心跳，但常驻 `run_outbox_loop` 原先没有向下传递心跳回调和心跳间隔。生产中的第三方 S3 回流、训练派发或编译事件可能运行时间超过租约，旧 Worker 的租约过期后会被新 Worker 接管；旧 Worker 若继续写入，就可能产生重复副作用。

## 2. 本轮范围

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP2 | 明确常驻消费者必须支持和单批处理相同的租约心跳契约 |
| R04 后端 | WP1/WP3 | `run_outbox_loop` 增加心跳回调、心跳间隔参数并透传到 `process_outbox_batch`；非法间隔失败关闭 |
| R09 测试 | WP5 | 增加参数透传和非法间隔回归，保留原有租约丢失不确认测试 |
| R10 运维 | OPS | 记录常驻 Worker 启动配置要求；真实 systemd、MySQL 锁等待和重启接管仍待生产前执行 |

## 3. 验收证据

```text
python -m pytest labelu/tests/internal/application/service/test_auto_training_outbox.py labelu/tests/internal/application/service/test_auto_training_dispatch.py labelu/tests/internal/application/service/test_auto_training_events.py labelu/tests/internal/application/service/test_auto_training_watchdog.py -q --disable-warnings --tb=short
50 passed, 116 warnings in 6.74s
```

覆盖内容：

- 常驻循环把 `heartbeat` 和 `heartbeat_interval_seconds` 传入每次批处理；
- 心跳间隔小于等于零时启动前抛出校验错误；
- 原有租约过期接管、租约丢失禁止终态确认、重试和死信行为未改变。
- 自动训练派发、事件和看门狗联合回归未受影响。

## 4. 生产边界

本轮只修改本地代码和测试；未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688，未修改 systemd，未提交、未推送、未部署。生产放行时必须为常驻 Worker 配置小于租约时长的心跳间隔，并用真实 `2/10/20/50` 并发、锁等待、Worker 重启接管证据复验。
