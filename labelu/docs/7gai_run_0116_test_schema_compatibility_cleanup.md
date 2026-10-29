# RUN-0116 测试数据库结构漂移兼容收口

## 1. 轮次结论

本轮解决上一轮 BM1688 业务流专项在创建任务前被旧测试 SQLite 结构阻断的问题。原因不是边端 Worker 业务逻辑，而是 pytest 明确跳过 Alembic、复用命名 SQLite 文件时，旧库没有跟上当前 ORM 的增量字段。

本轮达到本地 L1：没有连接生产 MySQL，没有执行生产迁移，没有启动真实 TPU-MLIR、BM1688、S3、AICM 或生产 Worker。

## 2. 需求和代码路径核验

- 测试入口：`labelu/tests/conftest.py`，`LABELU_SKIP_MIGRATIONS=true`，通过 `Base.metadata.create_all` 后执行旧库兼容列补齐。
- 业务模型：`labelu/internal/domain/models/task.py` 使用 `dataset_id`、`dataset_label_contract_id` 和 `task_label_scope_mode`。
- BM1688 模型：`labelu/internal/domain/models/yolo_bm1688_export.py` 使用 `worker_action`；正式迁移已由 RUN-0112 提供该列。
- 失败链：旧库缺列 -> `crud_task.create` 或 `crud_yolo_bm1688_export.create` flush 失败 -> 边端业务流尚未开始执行。

## 3. 实际改动

修改 `labelu/tests/conftest.py`：

- 对旧 `task` 表增补可空 `dataset_id`；
- 增补可空 `dataset_label_contract_id`；
- 增补带默认值 `FULL` 的 `task_label_scope_mode`；
- 对旧 `yolo_bm1688_export` 表增补带默认值 `export` 的 `worker_action`；
- 每次增补后重新读取列集合，保证同一进程内后续兼容检查看到最新结构。

这些是测试库兼容列，不是生产迁移替代品；生产仍必须执行正式 Alembic 链并做 MySQL 结构对账。

## 4. 验证证据

1. 复制确实缺少 `task.dataset_id`、`task.dataset_label_contract_id`、`task.task_label_scope_mode` 和 `yolo_bm1688_export.worker_action` 的旧 SQLite 文件作为隔离回归库。
2. 首次运行按缺列顺序暴露阻断，确认不是只在新库 `create_all` 上得到假绿。
3. 兼容层补齐后执行：

   `LABELU_TEST_DB_NAME=run0116-task-compat.db python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py -q`

   结果：`2 passed`（仅有既有依赖弃用警告）。
4. 新建进程数据库执行同一专项：`2 passed`。

## 5. 影响和边界

- 菜单、按钮、中文弹窗、两类登录人员、权限和业务 API 不变。
- 不新增项目/业务空间、设备发布、OTA 或模型回滚能力。
- 不改变 PT、ONNX、FP32/FP16/INT8 BModel 制品和发布目录语义。
- 只改善测试库在跳过迁移模式下的结构兼容，不能证明生产 MySQL 已升级。

## 6. 遗留项与下一依赖

- 仍需在同版本 MySQL 沙箱执行正式迁移、`SHOW CREATE TABLE`、索引/执行计划和双进程锁等待演练。
- 仍缺真实 TPU-MLIR、BM1688 真机、第三方 S3 回流、AICM 发布与本平台下载对账证据。
- 下一轮优先补齐真实 MySQL 迁移/备份恢复门禁，或在具备受控凭据后执行真实设备链路；独立 Worker 默认继续关闭并 fail-closed。

## 7. 交付状态

- 本地代码和测试：已完成，L1。
- 提交、推送、部署、生产验证：未执行，等待用户明确“提交代码”。
