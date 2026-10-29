# RUN-0101 车牌训练组件制品世代隔离（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；执行契约 `CONCURRENCY-EXEC-02`、`LOCAL-TRAIN-PHASE-GENERATION-01`。
- 工作包和角色：WP2 训练与执行器；R04 后端主责，R06 模型算法、R07 边端、R09 测试协作。
- 本轮目标：审计车牌检测/识别子进程从队列到组件状态、PT/ONNX 制品和最终状态的执行世代传递，阻止旧 Worker 的磁盘制品在新 Worker 恢复时被误采用；保持无执行租约的历史 Web 路径兼容。

## 1. 调用链结论

独立训练 Worker 的调用链为：

```text
claim_execution_lease(owner/token)
  -> yolo_train_worker._default_runner
  -> yolo_train_queue._run_plate_recognition_train_subprocess
  -> spawn(_run_plate_recognition_train_child, owner/token)
  -> run_plate_detection_train_job / run_plate_recognition_train_job
  -> _update_job_component / 最终状态写入
```

子进程现有阶段写入继续由 `yolo_train._write_train_phase()` 根据 owner/token 生成 `phase_generation=owner:token`；组件状态和最终数据库状态在写入前后均调用训练执行 fencing。旧 Web 调用 `request_train_start()` 没有执行租约上下文，仍走无上下文兼容路径；当作业存在有效执行租约时，`_write_train_phase()` 及车牌组件/终态 guard 均拒绝这类旧写入。

本轮审计发现的实际缺口是：车牌组件训练工具先在共享作业目录生成 PT/ONNX 文件，恢复逻辑过去只判断文件存在，未核验文件属于哪个执行世代。失租 Worker 迟到留下的文件可能被新 Worker 当成可恢复组件。

## 2. 实际变更

- `plate_recognition_train.py`
  - 新增车牌执行世代规范化：租约上下文必须同时包含 owner 和 token；部分上下文失败关闭；无上下文保留旧 Web 行为。
  - 带租约的组件 artifact 快照增加 `execution_owner`、`execution_fencing_token` 和 `phase_generation`。
  - 每个带租约组件完成后，在组件 PT/ONNX 目录写入原子 `.组件名.artifact-generation.json` 标记，记录制品路径和世代。
  - `_recover_component_artifacts_from_disk()` 在带租约恢复时必须匹配当前 owner/token、世代和 PT/ONNX 路径；缺失、损坏、篡改或旧 token 标记一律跳过。无租约恢复仍保持历史文件存在判断，兼容旧 Web 数据。
  - 车牌检测单组件和车牌识别四组件都复用同一规则，未新增数据库字段、菜单、角色、项目维度或外部发布语义。
- `test_plate_recognition_train.py`
  - 覆盖制品标记写入、当前世代通过、旧 owner/token 拒绝、损坏 token 拒绝和无租约装饰兼容。
  - `test_yolo_train_queue.py`
  - 覆盖车牌检测子进程将 execution owner/token 传递到实际 runner；原有无上下文调用契约保持通过。
  - 无上下文车牌组件写入在检测到有效执行租约时拒绝，避免旧 Web 覆盖独立 Worker 状态。

## 3. 本地证据

```text
python -m py_compile labelu/internal/application/service/plate_recognition_train.py labelu/internal/application/service/yolo_train_queue.py
通过

python -m pytest labelu/tests/internal/application/service/test_plate_recognition_train.py labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings --tb=short
65 passed, 115 warnings

python -m pytest labelu/tests/internal/application/service/test_yolo_train_phase_generation.py labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_spawn.py -q --disable-warnings --tb=short
16 passed, 112 warnings

git diff --check -- labelu/internal/application/service/plate_recognition_train.py labelu/tests/internal/application/service/test_plate_recognition_train.py labelu/tests/internal/application/service/test_yolo_train_queue.py
通过（仅换行符提示，无差异错误）
```

## 4. 并发、失败和真实性边界

- 标记只允许“当前带租约执行器”恢复磁盘上的未入库组件；它不能替代数据库 fencing、租约续期或父进程在失租后终止子进程。
- PT/ONNX 仍由训练工具写入共享目录；独立 Worker 的失租终止由父进程租约监视器负责。本轮新增的是恢复和入库边界，不宣称能中断第三方训练库内部的每一次文件写入。
- 标记采用原子临时文件替换；损坏 JSON、缺少字段、token 不能转换为整数、路径不一致均按不匹配处理。
- `phase_generation` 仍是 owner/token 的派生值，没有新增数据库列；旧 Web 没有执行租约时不写世代字段，也不会被本轮改造阻断。
- 当前没有真实 MySQL 双进程、systemd 重启、共享存储、GPU、真实车牌训练节点、TPU-MLIR、BM1688、S3、AICM 或生产浏览器证据。

## 5. 本地、预生产和生产状态

- 本轮仅修改本地代码、单元测试和文档；未连接生产数据库，未触发训练、编译、边端验证、S3 回流或模型发布。
- 结论为本地 L1。独立车牌训练 Worker 在同版本 MySQL 双进程接管、重启恢复、GPU 释放和真实节点证据完成前继续保持 fail-closed，不切生产消费。
- 未提交、未推送、未部署；等待用户明确说“提交代码”。

## 6. 下一依赖

1. R03/R09 在同版本 MySQL 脱敏生产快照上验证车牌作业双进程领取、锁等待、旧 token 阶段/组件/终态拒绝。
2. R07/R10 在真实训练节点验证失租终止、进程重启、共享存储残留标记清理和 GPU 释放。
3. R06/R09 用真实车牌训练样本验证四组件 PT/ONNX 产物、恢复和后续 BM1688 编译输入一致性。
