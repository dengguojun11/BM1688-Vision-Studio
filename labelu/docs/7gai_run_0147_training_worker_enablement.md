# RUN-0147 独立 YOLO/车牌训练 Worker 可消费门禁与动态超参 fencing

## 1. 轮次信息

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R06 模型算法、R09 测试主责；R10 运维协作。
- 工作包：WP2 训练执行器与并发控制。
- 需求编号：`TRAIN-WORKER-ENABLE-01`、`TRAIN-FENCING-HYPERPARAM-01`。
- 交付级别：本地 L1；未提交、未推送、未部署。

## 2. 本轮产出

### 2.1 移除过期硬阻断

`yolo_train_worker.dedicated_worker_ready()` 之前即使训练表执行租约字段、生产安全配置和批量上限都满足，仍无条件返回“子进程状态写入尚未接入”。本轮确认父 Worker、YOLO/车牌子进程和阶段/终态写入已经传递并校验 owner/token，因此改为在前置条件通过时返回 `True, "ok"`。

仍保留以下失败关闭条件：训练开关未开启、独立 Worker 未开启、执行租约字段缺失、数据库结构无法确认、批量上限非法、生产安全配置缺失。

### 2.2 训练子进程与动态超参 fencing

- Worker 领取后先取得 execution lease/fencing token，再设置 `PROCESSING` 和 GPU 信息。
- owner/token 传入 YOLO11、车牌检测和车牌识别训练子进程；子进程阶段、运行进程号、数据准备、指标、best.pt 终态和失败状态均复用 fencing 校验。
- 运行中的动态超参应用增加 `before_apply_fn` fencing 检查；旧 Worker 不能修改新 Worker 持有的训练作业，待处理超参文件不会被旧执行者删除。
- 测试使用 CPU 槽位隔离，验证 Worker 真实领取、运行器调用和租约释放，不启动真实 GPU 训练。

## 3. 改动文件

- `labelu/internal/application/service/yolo_train_worker.py`
- `labelu/internal/application/service/yolo_training/hyperparams.py`
- `labelu/internal/application/service/yolo_train.py`
- `labelu/tests/internal/application/service/test_yolo_train_worker.py`
- `labelu/tests/internal/application/service/test_yolo_train_runtime_hyperparams.py`

## 4. 验收证据

- 训练 Worker + 队列专项：`53 passed`；
- 运行时超参和稳定性专项：`25 passed`，新增旧 owner/token 拒绝测试；
- 合计：`78 passed`；
- `python -m compileall -q labelu/internal` 通过；
- `git diff --check` 无错误（工作区其他历史文件只产生换行提示）。

## 5. 真实环境边界

本轮没有打开生产训练开关，也没有连接生产 GPU、MySQL 或 systemd。以下仍未验收：

- Ubuntu/systemd 独立训练 Worker 实际启停和重启接管；
- 真实 GPU/YOLO11 训练、显存和多卡资源调度；
- 同版本 MySQL 双 Worker、20 并发、锁等待和 fencing 接管；
- 真实数据版本冻结、PT 产物、静态 ONNX、FP32/FP16/INT8 BModel、BM1688 和 AICM 发布。

因此生产 `YOLO_TRAIN_DEDICATED_WORKER_ENABLED` 继续关闭；本轮不能写成生产训练已上线。

## 6. 下一依赖

R10 提供同版本 MySQL 沙箱、systemd 单元和 GPU 节点；R06/R07 提供真实 YOLO11 训练和 BM1688 编译验证；R09 执行双 Worker、重启接管和 20 并发报告。用户明确“提交代码”前不执行 commit、push 或部署。

