# RUN-0338 本地验收种子血缘与去项目化修正（2026-09-21，本地）

## 1. 发现的问题

`scripts/seed_local_acceptance_flow.py` 原先把自动训练计划和训练作业写入 `project_id=1`，并把训练作业固定关联 `task_id=1`。脚本随后才创建验收任务，可能导致训练作业、标注合同和样本不属于同一条业务链。

## 2. 修正内容

| 责任角色 | 工作包 | 文件 | 修正 |
|---|---|---|---|
| R03 后端/架构 | WP0/WP2 | `scripts/seed_local_acceptance_flow.py` | 计划和训练作业使用 `project_id=None`，符合无项目方案 |
| R06 模型算法 | WP2/WP3 | `scripts/seed_local_acceptance_flow.py` | 先创建任务和标注合同，再创建训练作业 |
| R09 测试 | WP5 | `scripts/tests/test_seed_local_acceptance_flow.py` | 固化去项目、任务关联和 SQLite 拒绝远程库契约 |

训练链现在明确为：

```text
验收任务 -> 标注合同 -> 图片样本 -> 训练作业 -> 自动训练轮次 -> 评估结果
```

## 3. 验收证据

- `python -m pytest scripts/tests/test_seed_local_acceptance_flow.py -q`：`2 passed`。
- `python -m py_compile scripts/seed_local_acceptance_flow.py`：通过。
- 统一本地门禁：`.tmp/evidence/local-lineage-0338/local-gates-20260921T061636Z.json`，`blocking: 0`。
- 脚本仍在导入应用服务前校验数据库扩展名，并强制设置 `APP_ENV=local`；非 SQLite 数据库直接失败。
- 本轮未创建数据库、未写入业务数据、未连接生产、未提交、未推送、未部署。

## 4. 遗留边界

脚本仍是本地模拟验收数据生成器，不代表真实 GPU、TPU-MLIR、BM1688、AICM 或 S3 已验收；这些仍需各自真实环境证据。
