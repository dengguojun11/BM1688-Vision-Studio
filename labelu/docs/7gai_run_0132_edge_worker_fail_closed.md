# RUN-0132 边端验证独立 Worker 未就绪时的消费路径失败关闭

## 1. 本轮目标

统一训练和边端验证的执行器边界：当 `YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=true` 时，Web 调度器可以分配设备，但只能由独立边端验证 Worker 执行 SSH、上传、BM1688 推理和结果回传。Worker 未就绪时不得回落到 Web 进程线程。

## 2. 代码变更

- `labelu/internal/application/service/yolo_edge_validation_scheduler.py`
  - 独立边端 Worker 已就绪：保持原行为，只分配设备并返回作业编号。
  - 独立边端 Worker 未就绪：保留已分配的作业和设备，不启动 `_run_manual_job_sync` Web 线程；记录错误日志，等待运维修复和 Worker 重新领取。
  - 未开启独立 Worker 时，历史 Web 线程路径保持不变，便于当前默认关闭配置兼容。

## 3. 业务与运维含义

边端任务被分配设备不等于已经开始推理。开启独立 Worker 后，设备分配和实际执行是两个阶段：作业状态会保留在处理中，执行租约由独立 Worker 领取；Worker 没有启动、数据库字段未迁移或环境探针未通过时，系统不偷偷启动 Web SSH 线程。

## 4. 本地验证证据

- `python -u -m pytest labelu/tests/internal/application/service/test_yolo_edge_validation_scheduler.py -q`
  - `9 passed`
- `python -m py_compile labelu/internal/application/service/yolo_edge_validation_scheduler.py` 通过。
- 新增用例确认 Worker 未就绪时不调用 Web 边端执行函数，已分配作业仍返回并保留状态。

## 5. 验收等级与未完成项

- 当前：本地 L1。
- 尚未证明：Ubuntu systemd、同版本 MySQL 租约接管、真实 SSH/BM1688 三精度推理、设备重启恢复、生产部署和浏览器联调。
- 生产独立边端 Worker 开关继续关闭，不能把本地 `9 passed` 写成真实设备通过。

## 6. 角色交付

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R03 架构 | 统一边端唯一消费者边界 | 完成 |
| R04 后端 | 调度器失败关闭和状态保留 | 本地完成 |
| R07 BM1688 边端 | 保持设备分配与真机执行分离 | 已复用 |
| R09 测试 | 未就绪回落阻断和调度回归 | `9 passed` |
| R10 运维 | 明确 Worker 恢复前不启动 Web 执行 | 本地完成，systemd 待验收 |

## 7. 提交与部署状态

本轮未提交、未推送、未部署，未修改生产数据库，等待用户明确“提交代码”。
