# RUN-0212 边端旧入口与全局回流测试契约迁移

## 本轮范围

本轮只迁移测试契约，不改变生产业务代码，不恢复项目写入口，也不打开自动训练计划写入开关。目标是让测试与一期“无项目、全局回流、旧控制面退休”的现行语义一致。

## 迁移内容

| 测试范围 | 迁移后的契约 |
|---|---|
| `edge-device-jobs` | 创建、领取、结果回传均验证一期退休门禁返回 `403`；调用前后 `edge_device_job` 数量不变，证明无数据库副作用 |
| `test_third_party_calls` | 第三方请求和历史包解析不再构造 `StrategyTaskIntegration.project_id`；审计表保留可空历史字段，但新请求的 `project_id` 必须为空 |
| `test_flexible_callback` | 回调夹具移除项目创建和 `project_id`；无作用域历史集成继续走兼容投影且不凭空生成工作流；派发器单独使用 `scope_key=global` 工作流夹具验证成功重放和死信 |
| `test_edge_feedback` | 难例接收、去重、审核、主动学习查询全部使用全局作用域；仅管理人员可审核全局难例；不再创建项目或成员，不依赖项目占位簇 |

## 证据

专项命令：

```text
python -m pytest labelu/tests/internal/adapter/routers/test_edge_device_job.py labelu/tests/internal/adapter/routers/test_third_party_calls.py labelu/tests/internal/application/service/test_flexible_callback.py labelu/tests/internal/adapter/routers/test_edge_feedback.py -q --disable-warnings --tb=short
```

结果：`13 passed`。

静态检查：已执行 `git diff --check`，通过。

## 生产边界

本轮未连接生产 MySQL、第三方 S3、AICM、TPU-MLIR 或 BM1688，未提交、未推送、未部署。真实生产回流包、真实账号、并发去重和边端设备证据仍需由运维验收批次完成。

## 后续放行条件

1. 生产探针确认旧 `edge-device-jobs` 返回 `403` 且无新增作业。
2. 使用真实第三方 S3 回流包确认审计记录保持全局、重复包幂等、异常包不会创建项目。
3. 使用管理人员和标注人员账号分别回放全局难例查看/审核权限。
4. 在同版本 MySQL 沙箱执行并发回流和回调派发测试后，才允许进入提交与部署流程。
