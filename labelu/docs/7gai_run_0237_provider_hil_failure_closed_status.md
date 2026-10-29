# RUN-0237 Provider 本地 HIL 失败状态失败关闭

## 1. 问题确认

`local_hil_check` 捕获预测异常后虽然会记录 `local_hil_status=FAILED`，但原实现仍把 `runtime_evidence.status` 无条件写为 `READY`。这会让管理页面和运行时证据出现“本地 HIL 失败但 Provider 就绪”的矛盾状态，增加误启用风险。

## 2. 本轮范围

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品/体验 | WP0/WP4 | 统一 Provider 失败状态展示语义：成功为“就绪”，失败为“异常” |
| R03 架构 | WP2 | 保持运行时健康、HIL 证据和生产可用性三道闸门独立；失败不得写入 READY |
| R04 后端 | WP1/WP3 | `local_hil_check` 仅在 HIL 成功时写 `runtime_evidence.status=READY`，失败写 `UNHEALTHY` |
| R09 测试 | WP5 | 增加错误摘要校验失败的 HIL 回归，并执行 Provider 路由/Worker 联合回归 |
| R10 运维 | OPS | 保持真实镜像摘要、工具链版本和正式 HIL 证明仍为生产放行条件 |

## 3. 验收证据

```text
python -m pytest labelu/tests/internal/application/service/test_provider_runtime.py labelu/tests/internal/application/service/test_provider_runtime_bootstrap.py labelu/tests/internal/application/service/test_provider_adapter.py -q --disable-warnings --tb=short
10 passed, 113 warnings in 5.31s

python -m pytest labelu/tests/internal/adapter/routers/test_provider_registry.py labelu/tests/internal/adapter/routers/test_provider_job.py labelu/tests/internal/application/service/test_provider_worker.py labelu/tests/internal/application/service/test_provider_worker_runtime.py -q --disable-warnings --tb=short
35 passed, 118 warnings in 6.95s

pnpm exec vitest run src/pages/platform/pages/providers.test.tsx --reporter=dot
2 passed
```

新增回归验证：适配器返回错误的内联结果摘要时，HIL 记录为 `FAILED`，运行时证据为 `UNHEALTHY`，生产可用性保持关闭。

## 4. 生产边界

本轮只修改本地 Provider 状态投影和测试；未连接生产 Provider、S3、AICM、TPU-MLIR 或 BM1688，未修改生产配置，未提交、未推送、未部署。真实 Provider 镜像摘要、工具链版本、HIL 报告和生产端点仍必须在预生产/生产验收。
