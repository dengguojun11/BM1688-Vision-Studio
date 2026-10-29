# RUN-0158：主动学习旧项目测试迁移与一期自动训练门禁

日期：2026-09-19（Asia/Shanghai）  
范围：主动学习聚类、批次、审核、数据集冻结、自动训练计划门禁

## 本轮问题

旧专项测试仍通过 `POST /api/v1/projects` 创建项目，并把 `project_id` 传给主动
学习接口。当前生产契约已经退役项目写入口，导致 9 项测试在权限门禁处失败。
这不是业务代码缺陷，也不能通过恢复项目创建来“修复”。

## 本轮处理

- 重写 `labelu/tests/internal/adapter/routers/test_active_learning.py` 的测试数据
  构造，统一使用 `project_id=None`、`scope_key=global`、创建者血缘。
- 主动学习聚类、批次、接受、查询全部不传项目参数。
- 全局编排统一使用管理人员令牌，避免把标注人员权限误当成全局调度权限。
- 保留并验证：批次幂等、专家回流工作流、评分四维快照、审核快照冲突、批次
  预留、样本消费、聚类过滤。
- 将冻结行为按一期边界改为验证自动训练硬封锁：自动训练计划写入关闭时返回
  `403 / 57122`，数据集保持 `READY_TO_REVIEW`，不创建自动训练审计记录。

## 本地验收证据

```text
python -m pytest labelu/tests/internal/adapter/routers/test_active_learning.py -q --disable-warnings --tb=short
9 passed
```

此前同文件的 9 项 `KeyError: data` 已消失；失败根因是旧项目写入口 403，不是
全局主动学习链路失败。

## 生产边界与遗留

本轮只迁移测试，不写生产数据，不改变 API 状态机。真实第三方 S3 包、AICM
模型 ID、MySQL 并发和生产页面仍待验收；自动训练一期继续保持硬封锁，必须完成
真实执行器、重启恢复和质量证据后才允许打开计划写入开关。

本轮未提交、未推送、未部署。
