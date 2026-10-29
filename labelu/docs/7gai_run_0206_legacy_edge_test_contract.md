# RUN-0206 旧边端控制面测试契约同步

## 责任角色

- R03 架构：核对一期旧设备作业控制面退休边界。
- R04 后端：保持 `ensure_legacy_control_plane_retired()` 在旧写入口最前置执行。
- R09 测试：同步旧测试到失败关闭契约。

## 变更

`edge_device_job.create()` 属于已取消的旧设备作业控制面。当前一期主链使用 BM1688 编译与边端验证任务，旧入口不能再先查询导出作业、校验父任务或创建数据库记录。因此测试不再要求“无父任务导出”返回 `409`，而是验证旧入口在任何业务查询和写入前直接返回 `403`。

## 验收

执行范围：

```text
python -m pytest labelu/tests/internal/adapter/routers/test_algorithm.py labelu/tests/internal/adapter/routers/test_training.py labelu/tests/internal/application/service/test_project_scope_guards.py labelu/tests/internal/application/service/test_model_version_no_project.py -q
```

本轮首次基线为 `32 passed, 1 failed`，失败原因是旧测试仍断言 `409`。同步契约后复跑为 `33 passed`；Pydantic 弃用告警不影响断言。

## 边界

本轮未连接生产、未写生产数据、未提交、未推送、未部署。真实 BM1688 验证仍以新边端验证链路和设备证据为准。
