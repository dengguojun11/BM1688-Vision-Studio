# RUN-0156：训练僵尸回收的原子 fencing 收口

日期：2026-09-19（Asia/Shanghai）  
范围：YOLO11/车牌训练 Worker、旧版训练队列回收、并发状态保护

## 本轮结论

训练 Worker 的执行租约已经保护子进程阶段、指标和终态写入，但旧版
`reconcile_stale_processing_jobs` 在检查“无执行租约”后仍可能使用对象写入。
如果独立 Worker 恰好在检查与写入之间接管同一作业，旧回收线程可能把新 Worker
正在执行的 `PROCESSING` 改回 `PENDING` 或覆盖失败信息。

本轮把这条旧兼容路径改为数据库条件更新：只有执行租约为空或已过期时才允许
改变状态；新 Worker 在窗口内接管后，旧回收更新影响行数为 0。

## 代码交付

- `labelu/internal/adapter/persistence/crud_yolo_train_job.py`
  - 新增 `update_status_if_execution_lease_free`。
  - 使用单条条件更新校验 `execution_lease_owner`、`execution_lease_expires_at`。
  - 更新成功后刷新会话对象，并同步 `TrainingTask.latest_job_status`。
- `labelu/internal/application/service/yolo_train_queue.py`
  - 僵尸 `FAILED/PENDING` 回收改用上述原子更新。
  - 不新增菜单、接口、登录角色或数据库字段。
- `labelu/tests/internal/application/service/test_yolo_train_worker.py`
  - 新增新 Worker 持有租约时旧回收不能覆盖状态、租约过期后可回收的回归测试。

## 验收证据

- 新增原子回收测试：`1 passed`。
- 训练 Worker 专项首轮：原有 10 项通过；20 进程 SQLite 领取测试通过；
  新增测试初次暴露会话缓存问题，修复后单测通过。
- 目标模块 `py_compile`：通过。
- 生产 MySQL/InnoDB 锁等待、20 并发、重启接管、真实 GPU 训练：未执行。

## 放行边界

本轮等级为本地 L1。生产独立训练 Worker 继续保持关闭；不能把 SQLite 多进程
证据等同于同版本 MySQL 证据。上线前仍需在脱敏生产快照和同版本 MySQL 完成
20 并发领取、旧租约接管、锁等待、systemd 重启、GPU 释放及 YOLO/车牌真实训练。

本轮未提交、未推送、未部署、未写入生产。
