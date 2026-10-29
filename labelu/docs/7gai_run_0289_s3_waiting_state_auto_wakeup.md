# RUN-0289 第三方 S3 回流等待状态自动唤醒（2026-09-20）

## 目标

修复第三方 S3 问题数据回流进入 `WAITING_FOR_MODEL`、`WAITING_FOR_CATEGORY`、`WAITING_FOR_SAMPLES` 后无人重新入队的问题。正常包仍由独立 Worker 自动处理，管理人员不参与常规重试；管理人员只处理异常、规则和抽查。

本轮不改附件去重策略、不改 AICM 模型 ID 解析顺序、不改任务创建和预标注业务规则。

## 实现

| 场景 | 唤醒方式 | 说明 |
|---|---|---|
| 等待更多图片 | Worker 周期扫描 | 等待行超过 `EXPERIMENT_DATASET_WAITING_REQUEUE_SECONDS`（默认 30 秒）后转为 `PENDING`，由现有租约领取流程继续处理 |
| AICM/本地模型刚发布 | 发布模型目录注册、模型目录同步后主动唤醒 | 只唤醒等待模型的行；注册场景按目标模型 ID 过滤，同步场景覆盖等待模型行 |
| 数据集分类新增或更新 | 分类服务提交前主动唤醒 | 唤醒等待分类的行，实际分类解析和任务创建仍由 Worker 执行 |
| 同一第三方包重复到达 | `accept` 幂等分支即时转 `PENDING` | 仅等待状态可即时重试，处理中任务不会被抢占 |

所有唤醒更新都满足：不覆盖未过期 Worker 租约、清理旧的开始/结束时间和错误信息、由 `claim_pending` 再次生成新的 fencing token。重复包到达只有在等待行没有有效租约时才即时唤醒；多 Worker 竞争仍由现有条件更新和租约保护。

## 变更文件

- `labelu/internal/adapter/persistence/crud_experiment_dataset_import.py`
  - 新增带冷却时间、租约条件和模型过滤的 `requeue_waiting`。
- `labelu/internal/application/service/experiment_dataset_import.py`
  - 新增 `wake_waiting_imports`；重复包到达时即时唤醒等待状态。
- `labelu/internal/application/service/experiment_dataset_import_worker.py`
  - 每轮领取前扫描等待行，并在结果中记录 `requeued` 数量。
- `labelu/internal/application/service/published_model_catalog.py`
  - 模型注册/同步完成后唤醒等待模型回流。
- `labelu/internal/application/service/system_labels.py`
  - 分类新增/更新完成后唤醒等待分类回流。
- `labelu/internal/common/config.py`
  - 新增 `EXPERIMENT_DATASET_WAITING_REQUEUE_SECONDS`，默认 30 秒。
- `labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py`
  - 新增等待转 `PENDING`、Worker 先唤醒再领取和既有租约测试覆盖。

## 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q --disable-warnings --tb=short
13 passed

python -m pytest labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py labelu/tests/internal/application/service/test_legacy_s3_adapter.py labelu/tests/internal/adapter/routers/test_strategy_task_integration.py -q --disable-warnings --tb=short
39 passed

python -m pytest labelu/tests/internal/application/service/test_published_model_catalog.py -q --disable-warnings --tb=short
23 passed

python -m compileall -q labelu/internal/application/service/experiment_dataset_import.py labelu/internal/application/service/experiment_dataset_import_worker.py labelu/internal/application/service/published_model_catalog.py labelu/internal/application/service/system_labels.py labelu/internal/adapter/persistence/crud_experiment_dataset_import.py
通过

git diff --check
通过（仅保留仓库既有换行符提示）
```

## 生产验收条件

当前只完成本地 L1，未提交、未推送、未部署。生产放行前必须在同版本 MySQL 和真实 Worker 上验证：

1. 等待模型、等待分类、等待样本三类记录能在冷却后自动恢复；
2. 模型发布/目录同步和分类新增/更新后，相关回流在一个 Worker 周期内被领取；
3. 两个 Worker 同时扫描同一聚合组时只有一个产生任务和预标注副作用；
4. Worker 重启、租约过期接管、MySQL 锁等待和 `2/10/20/50` 并发矩阵通过；
5. 指标和日志能看到扫描数量、唤醒数量、领取数量、等待原因和最终失败原因。

未满足上述证据前，生产继续失败关闭，不能把本地测试结果当作线上完成。
