# RUN-0327 本地自动进化与自动训练门禁专项验收（2026-09-21）

## 1. 本轮目标

验证第三方专家回流进入平台后，自动进化链路具备明确的阶段门禁、幂等事件和失败关闭行为；自动训练计划在开关关闭或前置证据不完整时不能偷偷启动，只有满足数据、标签、评估集和质量策略要求后才允许进入下一阶段。

## 2. 责任角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品 | WP5 | 固化回流→难例→重新训练→评估的阶段状态和人工介入点 |
| R03 架构 | WP1/WP2 | 自动训练状态机、事件去重和失败关闭契约 |
| R04 后端 | WP1/WP2 | 计划、轮次、事件和回调 API 逻辑 |
| R06 模型算法 | WP1/WP3 | 训练前数据/标签/评估集/质量策略门禁 |
| R09 测试 | WP5 | 自动进化、计划开关、事件幂等和恢复回归 |
| R10 运维 | OPS | 确认本地开关默认边界，未启用生产自动计划 |

## 3. 验收命令与结果

```text
python -m pytest \
  labelu/tests/internal/adapter/routers/test_strategy_evolution.py \
  labelu/tests/internal/application/service/test_auto_training_phase_one_gate.py \
  labelu/tests/internal/application/service/test_auto_training_watchdog_switch.py \
  labelu/tests/internal/application/service/test_auto_training_events.py \
  -q --disable-warnings --tb=short
```

结果：

```text
83 passed, 114 warnings in 8.65s
```

## 4. 已验证行为

- 专家回流事件可进入自动进化链路，并按模型 ID、样本批次和任务血缘继续处理。
- 自动训练计划按轮次和阶段推进，不满足数据、标签、评估集或质量规则时停留在门禁状态。
- 重复事件不会重复创建同一轮训练或重复产生副作用。
- 自动训练开关关闭时 Watchdog 不启动，不会因 Web 进程启动而偷偷训练。
- 失败阶段可以记录原因并等待人工处理或受控重试，不把失败误标为完成。

## 5. 风险与下一依赖

- 本轮为本地服务/测试替身证据，未接真实 S3、AICM、GPU、MySQL 多进程或生产 Worker。
- 自动进化计划不能在真实环境证据完成前开启；训练 Worker、S3 Worker 和 AICM Worker 继续失败关闭。
- 下一依赖是预生产真实回流包、真实模型 ID、MySQL 锁等待和 Worker 重启接管回放。

## 6. 提交与部署状态

本轮未提交、未推送、未部署、未重启生产。继续等待用户明确说“提交代码”。
