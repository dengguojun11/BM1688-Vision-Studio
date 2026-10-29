# RUN-0073 手工边端验证样本包两阶段准备

## 1. 范围

- 日期：2026-09-18，Asia/Shanghai。
- 目标：修正手工边端验证任务的提交、样本包准备和 SSH 执行边界。
- 范围：`create_job` 和 legacy `create_legacy_edge_ssh_job_for_export`；不修改自动训练派发路径，不连接生产，不提交、不部署。

## 2. 生产链路问题与决定

旧链路在数据库事务内复制训练评估集，图片较多时会长时间占用事务；任务记录和文件目录也可能出现一方成功、一方失败。现在统一为两阶段：

1. 第一阶段只在事务内写入任务请求，状态保持 `PENDING`，不执行文件复制、压缩、SSH。
2. 第二阶段在事务外使用临时 staging 目录构建样本包，完成清单和文件检查后通过同文件系统原子改名发布到不可变目录。
3. 只有数据库回写 `result_json.pack.state=READY` 且 `manifest.json` 存在后，任务才允许进入边端执行。
4. 构建失败保留任务记录，写入 `pack.state=FAILED` 和错误摘要；失败任务不可调度到 SSH，后续可由重新发起操作重试。

## 3. 菜单/接口对应关系

- 训练产物详情中的“边端验证”：调用 `create_legacy_edge_ssh_job_for_export`，使用训练产物的 `test/val` 评估集。
- 任务与标注中的“创建边端验证”：调用 `create_job`，使用用户选择的样本/附件。
- 两条路径最终都由边端验证调度器分配设备，再进入 `_run_manual_job_sync`。
- `_run_manual_job_sync` 使用严格门禁 `edge_validation_pack_committed`；没有显式 `READY`、清单不存在或清单为空时，只释放设备并回到等待/失败处理，不执行 SSH。

## 4. 并发与文件安全

- 导出记录使用行锁避免同一导出并发创建重复任务。
- 样本包使用任务级不可变签名目录；staging 目录带随机后缀，避免并发构建互相覆盖。
- READY 回写带 `PENDING` 条件，任务已被其他执行者接管时不会误覆盖状态。
- 文件构建不在数据库事务内；失败会清理 staging，正式目录只保留已原子发布的包。

## 5. 本轮实现

- legacy 样本包支持 `root_override`，并改为事务外构建和原子发布。
- 新增严格 SSH 门禁 `edge_validation_pack_committed`。
- legacy 样本包准备异常保留任务行并写入 `FAILED` 包状态。
- 新增门禁契约测试：`PREPARING`、缺失清单均拒绝，`READY` 且清单存在才允许。

## 6. 验证与未完成项

- 本地专项测试：`python -m pytest labelu/tests/internal/application/service/test_yolo_edge_validation_pack.py labelu/tests/internal/application/service/test_yolo_edge_validation_scheduler.py -q --disable-warnings --tb=short`，`18 passed`；包含 READY 门禁、legacy 样本包失败持久化、调度兼容性测试。
- 未连接生产数据库、S3、AICM、TPU-MLIR 或 BM1688 真机；未执行真实 SSH。
- 未修改 `auto_training_dispatch.py`，自动训练路径保持原有语义。
- 本轮未提交、未推送、未部署；等待用户明确授权“提交代码”。
