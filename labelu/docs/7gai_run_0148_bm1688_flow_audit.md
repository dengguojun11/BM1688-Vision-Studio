# RUN-0148 BM1688 编译前缀、三精度落盘与真机通道审计

## 1. 轮次信息

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色：R06 模型算法、R07 BM1688 边端、R09 测试主责；R03 架构、R04 后端、R10 运维协作。
- 工作包：WP3 BM1688 编译验证与发布。
- 需求编号：`EDGE-BM1688-FLOW-02`、`EDGE-BM1688-CHANNEL-02`。
- 交付级别：本地 L1；未提交、未推送、未部署。

## 2. 审计结论

### 2.1 编译顺序

代码路径和编译脚本的真实顺序为：训练产出 `best.pt`，服务器导出静态 ONNX，再由锁定版本的 TPU-MLIR 或受控编译容器生成 BModel。BM1688 只接收运行包、加载模型并推理，不在设备上编译。`full` 模式要求同一模型前缀下的 `FP32`、`FP16`、`INT8` 三份 BModel；`fp32`、`fp16`、`int8`/`int8_only` 等模式只能用于诊断或补历史制品，不能绕过发布质量门禁。

### 2.2 本轮修复的实际缺口

训练权重可能是 `yolo11s.pt`、`yolo11m.pt` 等非默认模型。编译阶段已经用权重生成实际前缀（例如 `yolov11s`），但完成态检查过去使用配置默认的 `bmodel_basename`（通常为 `yolov11n`）判断三精度文件是否齐全。结果可能出现三种文件已经落盘，却被错误标记为 `ONNX_READY`。

本轮让 `_finalize_bmodel_status` 接收并使用训练权重的实际 `model_stem`，三精度检查与实际文件名保持一致；默认配置只在没有显式模型前缀的兼容调用中使用。

### 2.3 固定评估集

编译契约已经在缺少固定评估证据时失败关闭，封存验收包由同一份评估样本用于 PT、ONNX、FP32、FP16、INT8 和真机对比。当前代码测试证明的是“缺证据会阻断”，尚未证明生产中每个版本都由同一冻结评估集物化并在真实设备执行；这一点仍是生产放行项。

### 2.4 Board Agent 与 SSH 的边界

- **目标主路径：Board Agent 主动拉取。** 设备从 `/board-agent/jobs/next` 领取待验证任务，下载运行包，回传进度和报告；它只做 BM1688 真机验证，不做模型发布、设备发布、OTA 或回滚。
- **兼容路径：受控 SSH/SFTP。** 当前 `yolo_edge_validation_scheduler` 实际按设备 SSH 配置探测和分配，`_run_manual_job_sync` 负责连接、上传、执行和拉回报告。因此在当前本地代码和生产现状中，SSH 仍是可执行的旧兼容链，不能把 Board Agent 主路径写成已经接通生产。
- 两条路径必须使用同一验证任务、同一制品签名、同一冻结评估集和同一证据门禁；同一任务只能选择一条执行通道，不能并行上传或重复写终态。
- Board Agent 旧表/旧领取接口仍缺少与统一边端任务完全一致的执行租约和设备身份闭环；在生产切换前需由 R03/R04/R07/R09 完成统一任务适配、短期任务令牌、断网重领和重复回报测试。切换前保持 SSH 兼容链，不能同时打开两个消费者。

## 3. 改动文件

- `labelu/internal/application/service/yolo_bm1688_export.py`
- `labelu/tests/internal/application/service/test_bm1688_compile_flow.py`
- `labelu/docs/7gai_run_0148_bm1688_flow_audit.md`

## 4. 本地验收证据

- BM1688 编译流程和编译契约：`10 passed`；
- 新增用例验证配置默认前缀为 `yolov11n`、训练权重前缀为 `yolov11s` 时，三种 `yolov11s_*_1b.bmodel` 已落盘会正确返回 `COMPLETED`；
- `python -m compileall -q labelu/internal` 通过；
- `git diff --check` 通过。

## 5. 生产边界与上线前验收

本轮未连接生产数据库、真实 TPU-MLIR、BM1688、Board Agent、SSH、S3 或 AICM，未打开任何生产 Worker 开关。以下证据仍缺失：

1. 真实训练权重（至少一期 YOLO11 代表模型）从 PT 到静态 ONNX、FP32/FP16/INT8 同批落盘的制品清单、版本和 SHA256；
2. 同一冻结评估集在服务器评估和 BM1688 真机评估中的签名、有效推理数、精度、P95、错误率和 60 秒短稳；
3. Board Agent 主动拉取一次完整领取/下载/执行/回报链，含令牌、断网、超时接管和重复回报；
4. SSH 兼容链的同等报告，并证明切换开关不会产生双消费者；
5. 真实 MySQL 双 Worker、设备租约、systemd 重启接管和 20 并发记录。

因此本轮结论是：本地模型前缀缺口已修复，编译顺序和三精度门禁逻辑清晰；生产仍保持失败关闭和现有 SSH 兼容链，Board Agent 仅作为目标主路径，不能宣称已生产验收。

## 6. 后续责任

- R06/R07：提供代表性 YOLO11 权重、校准集、冻结评估集和 TPU-MLIR/BM1688 真机证据；
- R03/R04：统一 Board Agent 与 `yolo_edge_validation_job` 任务、执行租约和短期令牌，保持旧历史只读兼容；
- R09：执行三精度、固定评估集、双通道、断网接管和并发验收；
- R10：提供真实设备、systemd、MySQL 快照/备份和生产只读验证环境。

用户明确“提交代码”前，不执行 commit、push、部署或生产写入。
