# RUN-0222 第三方 S3 回流、问题样本自动处理与并发安全回归

## 1. 目标

验证生产实际来源于第三方 S3/MinIO 的问题图片包可以按全局链路自动接收、校验、去重、按 AICM `model_set_id` 归并、进入难例/主动学习池，并通过 Outbox/Worker 触发后续任务；管理人员只处理异常、规则和抽查，不要求逐包人工创建任务。

同时验证训练、回流、Provider、BM1688 和边端相关执行租约使用 fencing，旧执行者不能覆盖新状态或产生重复副作用。

## 2. 角色与工作包

| 角色 | 工作包 | 验收点 |
|---|---|---|
| R01 产品 | WP0 | 正常回流自动处理，异常才进入管理人员审核 |
| R03 架构 | WP2/WP5 | 全局作用域、幂等键、租约/fencing、失败阶段重试 |
| R04 后端 | WP1/WP3 | S3 配置、接收、去重、任务/样本创建和 Outbox 派发 |
| R05 外部平台 | WP4 | 第三方包、AICM ID、回调/重放和 Provider 适配 |
| R06 模型算法 | WP3 | 问题样本按模型 ID 对齐，进入重新标注和训练数据链 |
| R08 前端/UI | WP1/WP4 | 回流批次、异常审核、难例查看和重试均使用中文弹窗 |
| R09 测试 | WP5 | 回流、Worker、主动学习、媒体安全和并发专项 |
| R10 运维 | OPS | 生产 S3、Worker、MySQL 锁等待和重启接管证据 |

## 3. 本轮验证

```text
python -m pytest \
  labelu/tests/internal/adapter/routers/test_strategy_task_integration.py \
  labelu/tests/internal/adapter/routers/test_edge_feedback.py \
  labelu/tests/internal/adapter/routers/test_edge_feedback_global_scope.py \
  labelu/tests/internal/adapter/routers/test_ingestion.py \
  labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py \
  labelu/tests/internal/adapter/routers/test_third_party_calls.py \
  labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py \
  labelu/tests/internal/application/service/test_legacy_s3_adapter.py \
  labelu/tests/internal/application/service/test_provider_runtime.py \
  labelu/tests/internal/application/service/test_provider_worker.py \
  labelu/tests/internal/application/service/test_provider_worker_runtime.py \
  labelu/tests/internal/application/service/test_third_party_calls.py \
  labelu/tests/internal/application/service/test_auto_training_outbox.py \
  labelu/tests/internal/application/service/test_auto_training_dispatch.py \
  labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py \
  labelu/tests/internal/application/service/test_scheduler_lease.py \
  labelu/tests/internal/common/test_strategy_integration_s3_config.py \
  labelu/tests/internal/common/test_execution_fencing.py \
  labelu/tests/internal/common/test_media_scan.py \
  labelu/tests/internal/common/test_production_security.py \
  labelu/tests/internal/adapter/routers/test_active_learning.py \
  labelu/tests/internal/adapter/routers/test_active_learning_global_scope.py \
  labelu/tests/internal/application/service/test_legacy_provider_bridge.py \
  labelu/tests/internal/application/service/test_provider_adapter.py \
  -q --disable-warnings --tb=short

171 passed, 2 skipped, 129 warnings in 43.33s
```

覆盖：

- 第三方 S3 配置读取和安全校验；
- 问题包接收、内容指纹、重复包/重复图片幂等；
- 按模型 ID 关联来源、难例、主动学习和重新标注；
- Provider/实验数据集 Worker、回调和死信重放；
- Outbox 派发、执行租约、过期接管、旧 token 拒绝；
- 媒体扫描、文件安全和生产配置失败关闭。

## 4. 并发与失败关闭

- 同一外部包或图片重复到达不能重复创建任务、样本或训练事件；
- 正常回流自动进入处理队列，只有模型 ID 冲突、标签契约不兼容、低置信度和解析失败进入管理审核；
- Worker 租约损坏、过期或 fencing token 不匹配时停止写入；
- S3 不可用、签名/校验失败和对象下载失败进入可重试失败阶段，不伪造成功；
- 未完成的第三方包不会进入训练冻结版本。

## 5. 环境边界与验收等级

本轮为本地测试与模拟 Provider 证据，达到 **L1**。尚未接收真实生产 S3 问题包，尚未在同版本 MySQL 执行 `2/10/20/50` 并发、锁等待和 Worker 重启接管，未提交、未推送、未部署。

