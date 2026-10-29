# RUN-0226 训练 Worker、冻结清单与执行 fencing 回归

## 1. 目标

验证训练主链在并发和 Worker 重启场景下保持单次副作用：训练作业只能使用冻结 `DatasetVersion` 和服务端生成的清单；新执行代次可以接管过期租约；旧 Worker 不能覆盖新状态、写入旧日志或继续创建边端/质量副作用。

## 2. 角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP2/WP5 | 执行租约、fencing token、训练代次和失败关闭边界 |
| R04 后端 | WP1/WP3 | 训练派发、状态持久化、重试和 Worker 接管 |
| R06 模型算法 | WP3 | 冻结数据清单、评估集和训练资源档案 |
| R09 测试 | WP5 | 训练服务、Worker、代次、GPU 资源和旧 token 联合回归 |
| R10 运维 | OPS | systemd 重启接管和真实 MySQL 锁等待待后续执行 |

## 3. 本轮验证

```text
python -m pytest \
  labelu/tests/internal/application/service/test_auto_training_dispatch.py \
  labelu/tests/internal/application/service/test_auto_training_lifecycle.py \
  labelu/tests/internal/application/service/test_auto_training_persistence.py \
  labelu/tests/internal/application/service/test_training_scheduler.py \
  labelu/tests/internal/application/service/test_training_worker_persistence.py \
  labelu/tests/internal/application/service/test_training_worker_runtime.py \
  labelu/tests/internal/application/service/test_yolo_train_worker.py \
  labelu/tests/internal/application/service/test_yolo_train_frozen_manifest.py \
  labelu/tests/internal/application/service/test_yolo_train_phase_generation.py \
  labelu/tests/internal/application/service/test_yolo_test_val_fencing.py \
  labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py \
  labelu/tests/internal/common/test_execution_fencing.py \
  labelu/tests/internal/common/test_auto_training_runtime.py \
  labelu/tests/internal/common/test_yolo_train_gpu_allocator.py \
  labelu/tests/internal/common/test_yolo_train_resource_profile.py \
  -q --disable-warnings --tb=short

118 passed, 116 warnings in 24.69s
```

## 4. 关键断言

- 训练作业引用冻结数据版本，用户不能用手工清单覆盖服务端血缘；
- 同一作业的重复领取只允许一个有效租约；
- 租约过期后由新代次接管，旧 token 的状态/日志/结果写入被拒绝；
- 重试生成新的执行代次，不复用旧制品和旧租约；
- 训练未完成、清单不完整或资源档案不兼容时失败关闭；
- GPU 资源档案和批量限制参与派发，避免超卖导致并发性能失控。

## 5. 环境边界与验收等级

本轮是本地 SQLite/模拟 Worker 证据，达到 **L1**。同版本 MySQL 锁等待、`2/10/20/50` 领取矩阵、真实 GPU 训练、systemd 重启接管和生产 Worker 尚未验收；未提交、未推送、未部署。

