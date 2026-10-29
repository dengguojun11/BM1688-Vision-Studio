# RUN-0114 边端验证独立 Worker 最小执行入口

## 1. 本轮目标

把生产边端验证从 Web 进程内线程执行收口为可独立启动的 Worker 入口。Web 仍负责队列和设备分配；Worker 负责 SSH、样本包上传、BM1688 推理、结果回传和执行期租约。开关默认关闭，数据库字段不完整时失败关闭。

本轮只完成本地最小执行边界，不连接真实 BM1688，不打开生产 Worker，不执行真实 SSH、S3、AICM 或生产写入。

## 2. 交付内容

- 新增 `labelu/internal/application/service/edge_validation_worker_runtime.py`：
  - 通过 `YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED` 控制；
  - 检查 `yolo_edge_validation_job` 的 queue/execution 租约字段；
  - 复用现有 `dispatch_pending_edge_validation_jobs(start_workers=False)` 分配设备，不复制状态机；
  - 扫描已分配且未被执行租约持有的任务，调用现有 `_run_manual_job_sync()`；真正 SSH/推理前仍由该函数原子领取 execution lease；
  - 提供 `--once`、轮询间隔和批量上限命令行入口。
- 新增 `labelu/scripts/edge_validation_worker.py` 独立进程入口。
- Web 调度器在独立 Worker 开关打开且结构就绪时，不再启动边端验证线程；结构未就绪时保留旧路径并记录告警，避免切换后无人消费。
- 新增配置：
  - `YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED=false`；
  - `YOLO_EDGE_VALIDATION_DEDICATED_WORKER_INTERVAL_SECONDS=5`；
  - `YOLO_EDGE_VALIDATION_DEDICATED_WORKER_BATCH_LIMIT=1`。

## 3. 本地验证

```text
python -m pytest \
  labelu/tests/internal/application/service/test_edge_validation_worker.py \
  labelu/tests/internal/application/service/test_yolo_edge_validation_scheduler.py \
  -q --disable-warnings --tb=short
```

结果：`11 passed, 112 warnings`。

附加检查：

- Worker、调度器和脚本 `py_compile` 通过；
- `git diff --check` 通过。

合并历史 BM1688 业务流专项时，既有测试夹具在创建 `task` 时因本地测试库缺少 ORM 已使用的 `dataset_id` 列失败；该失败发生在本轮 Worker 代码执行前，属于现有测试数据库结构漂移，不能记为 Worker 通过，也未在本轮修改测试库。

## 4. 验收结论

- 本地：L1 通过（独立 Worker 和调度器专项）。
- 预生产/生产：未验收。
- 菜单、按钮、角色、项目维度、API 和业务数据表字段：本轮不新增业务入口；只新增运行配置。
- 真实边端 Worker：继续关闭，直到 MySQL、systemd、设备和资源释放证据完成。

## 5. 生产放行前置条件

1. 同版本 MySQL 上验证设备分配、execution lease 接管、旧 Worker 拒绝和重复调度。
2. systemd 启停、进程崩溃、SSH 中断、上传断点续传、板卡重启和远程目录清理。
3. 真实 BM1688 加载 FP32/FP16/INT8 BModel，验证有效推理数、错误率、精度、P95 和短稳。
4. Web 调度器、独立 Worker、BM1688 编译 Worker 的混合负载和资源隔离。

## 6. 交付状态

本轮未提交、未推送、未部署，等待用户明确“提交代码”。
