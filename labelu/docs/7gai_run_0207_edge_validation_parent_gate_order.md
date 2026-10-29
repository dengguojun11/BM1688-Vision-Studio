# RUN-0207 边端验证父任务一致性先行门禁

## 责任角色

- R03 架构：明确边端验证请求的校验顺序。
- R04 后端：在读取训练作业和编译平台前校验导出任务与当前任务的父任务关系。
- R09 测试：复现历史替身缺少训练字段时的跨任务请求，并验证稳定返回冲突。

## 变更

边端验证创建入口现在按以下顺序执行：

1. 读取导出记录和当前任务。
2. 校验导出记录的 `task_id` 与请求任务一致，不一致立即返回 `409`。
3. 通过父任务校验后，才读取训练作业、校验一期算法类型和 BM1688 编译平台。

这样跨任务请求不会先访问训练作业内部字段，也不会因历史记录或测试替身字段不完整而返回错误的 `404/500`。

## 本地验收

```text
python -m pytest labelu/tests/internal/application/service/test_project_scope_guards.py::test_edge_validation_rejects_export_task_parent_mismatch labelu/tests/internal/adapter/routers/test_yolo_edge_validation.py -q --disable-warnings --tb=short
```

结果：`32 passed`。

## 边界

本轮未连接生产、未执行 BM1688 真机验证、未写生产数据、未提交、未推送、未部署。
