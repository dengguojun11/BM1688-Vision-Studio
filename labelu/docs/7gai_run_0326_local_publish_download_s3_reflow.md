# RUN-0326 本地发布目录、下载控制与第三方 S3 回流专项验收（2026-09-21）

## 1. 本轮目标

在不连接生产 AICM/S3 的前提下，执行现有本地契约测试，确认模型发布后本平台保留发布目录和下载包，使用 AICM 模型 ID 作为对齐主键；下载开关关闭时管理端和公开端都不能下载；第三方 S3 专家回流能够按模型 ID 对齐，未登记模型进入等待状态而不是猜测导出记录。

## 2. 责任角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R05 发布集成 | WP3 | 发布目录、本地下载包、AICM ID 和下载开关契约 |
| R04 后端 | WP2/WP3 | 管理端/公开端下载权限和模型目录 API |
| R06 模型算法 | WP3/WP5 | 专家回流按已发布模型 ID 继承任务/标签血缘 |
| R09 测试 | WP5 | 发布目录、下载控制、S3 导入、等待和幂等回归 |
| R10 运维 | OPS | 确认本轮没有真实外部写入 |

## 3. 验收命令与结果

```text
python -m pytest \
  labelu/tests/internal/adapter/routers/test_published_model_catalog.py \
  labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py \
  labelu/tests/internal/adapter/routers/test_strategy_task_integration.py \
  -q --disable-warnings --tb=short
```

结果：

```text
39 passed, 117 warnings in 3.85s
```

## 4. 已验证行为

- 发布模型目录保留 AICM `model_set_id`，本地下载地址不使用内部 `export_id` 替代对外模型 ID。
- 本平台能够保留并提供模型下载包，即使 AICM 暂时不可用也不自动回源下载。
- 管理人员关闭下载开关后，管理端下载返回 `409`，公开下载返回 `404`；不会绕过开关再次请求 AICM。
- 第三方 S3 专家回流根据 `model_id` 找发布目录、导出、训练和标签分类血缘。
- 模型尚未登记时进入 `WAITING_FOR_MODEL`，不会猜测或绑定其他模型。
- S3 任务创建具备幂等和失败重试契约，少量异常才需要管理人员处理。

## 5. 风险与下一依赖

- 本轮使用测试替身和本地 SQLite 测试库，未连接真实 AICM、S3、MySQL 或生产账号。
- 本地通过不代表真实 AICM ID 对账、S3 权限、对象存储吞吐或回调签名已完成。
- 下一依赖是预生产真实 AICM/S3 沙箱和脱敏模型包，执行双向 ID 对账、下载并发、回流重复包和 Worker 重启接管。

## 6. 提交与部署状态

本轮未提交、未推送、未部署、未重启生产。继续等待用户明确说“提交代码”。
