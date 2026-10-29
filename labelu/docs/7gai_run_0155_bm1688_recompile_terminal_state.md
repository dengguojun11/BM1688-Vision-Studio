# RUN-0155 BM1688 重编译异常终态与可重试说明

日期：2026-09-19  
验收等级：本地 L1；未提交、未推送、未部署。

## 1. 本轮目标

收口 BM1688 手工“重新编译”在编译器预检、工具链启动或编译执行异常时的状态语义。此前独立 Worker 只释放 queue lease，任务可能长期停在 `PENDING` 并被反复领取；执行异常的兼容收尾还可能把任务写回 `ONNX_READY`，让页面误以为可以继续进入后续环节。

本轮不新增菜单、登录角色、数据库字段或第二套状态机，只复用现有 `FAILED` 状态、`error_message` 和 execution lease/fencing。

## 2. 实现内容

### 2.1 预检异常

`_run_bm1688_recompile_sync` 在独立 Worker 已领取 execution lease 后，如果 TPU-MLIR、Docker 或 BM1688 编译器预检失败：

- 任务写为 `FAILED`；
- 错误说明统一为“BM1688 重新编译失败（可重试）”；
- 保留原 ONNX 和已有制品，不删除可复用文件；
- 继续抛出异常，让 Worker 释放本次 queue lease，页面可通过重新编译动作再次入队。

### 2.2 编译执行异常

编译阶段抛出异常时：

- 任务写为 `FAILED`，不再写回 `ONNX_READY`；
- `retryable=true` 只作为内部摘要/CLI 展示和错误说明，不增加数据库列；
- 具备 execution monitor 时使用带 owner/token 的条件更新；
- `ExecutionFencingLost` 时不写任何失败终态，避免旧 Worker 覆盖新 Worker，保留当前执行者状态。

### 2.3 缺少 ONNX

缺少输入 ONNX 不属于编译器随机故障，任务仍写为 `FAILED`，错误说明为“ONNX 不存在，补齐 ONNX 后可重试”。

### 2.4 兼容边界

无 execution monitor 的直接写入只保留给迁移期间的历史兼容路径。独立 BM1688 Worker 在数据库租约字段缺失时继续失败关闭，不回退到无 fencing 编译路径。

## 3. 变更文件

- `labelu/internal/application/service/yolo_bm1688_export.py`
  - 新增重编译失败消息和条件持久化辅助函数；
  - 收口预检、缺 ONNX、执行异常的 FAILED 终态；
  - CLI 摘要增加 `retryable` 展示。
- `labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py`
  - 新增 setup 异常、fencing 条件写入和执行异常终态专项。

## 4. 本地验收证据

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py -q --disable-warnings --tb=short
36 passed

python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/common/test_bm1688_quality_rules.py labelu/tests/internal/common/test_bm1688_compile_contract.py -q --disable-warnings --tb=short
52 passed

python -m py_compile labelu/internal/application/service/yolo_bm1688_export.py
通过

git diff --check -- labelu/internal/application/service/yolo_bm1688_export.py labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py
通过
```

测试中的 SQLAlchemy/Pydantic 弃用警告为既有警告，不影响退出码。

## 5. 生产边界

本轮没有连接真实 TPU-MLIR、Docker、BM1688、MySQL、systemd 或生产数据库，没有执行真实编译和板端推理。仍缺：

1. 同版本 MySQL 双 Worker 锁等待、20 并发唯一领取和租约接管；
2. Ubuntu/systemd 重启后编译进程终止、共享目录清理和资源释放；
3. 真实 PT→ONNX→FP32/FP16/INT8 制品与固定评估集证据；
4. BM1688 真机加载、有效推理数、P95、错误率和短稳；
5. AICM 模型编号、本地包 SHA256 和发布目录三方对账。

因此本轮只能升级为本地 L1，独立 BM1688 Worker 生产开关继续保持关闭，不能据此宣称生产编译链路通过。

## 6. 后续放行条件

- 在同版本 MySQL 沙箱复核 FAILED 写入、旧 token 拒绝、重试入队和重复动作幂等；
- 在真实编译节点执行一次工具链缺失、编译超时、Worker 重启和恢复接管演练；
- 通过真实三精度制品、固定评估集和 BM1688 真机证据后，才允许打开独立 Worker 生产开关。

## 7. 提交与部署

本轮未提交、未推送、未部署，未写入生产数据库，等待用户明确“提交代码”。
