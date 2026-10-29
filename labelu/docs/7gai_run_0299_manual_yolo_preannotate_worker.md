# RUN-0299 手工 YOLO 批量预标注独立 Worker

## 1. 本轮目标

将“任务与标注”页面发起的手工 YOLO 批量预标注从 Web 进程的
`run_in_executor` 长任务迁移为可持久化、可重启接管、带租约 fencing 的独立
Worker。页面/API 的查询和状态字段保持兼容；在生产迁移和 Worker 托管完成前，
开关保持关闭。

## 2. 代码路径核验结论

改造前 `yolo_preannotate.create_batch_job` 在 API 进程内解析模型和样本后，
把运行时参数直接传给 `_run_yolo_preannotate_sync`，数据库只保存作业统计，
Web 进程重启后没有可重建的输入。已有的自动样本预标注 Worker 已具备
`AutoLabelJob` 的租约与 fencing 模式，本轮在手工 YOLO 作业上复用同一边界。

## 3. 实现内容

### 3.1 持久化输入

`yolo_preannotate_job.payload_json` 保存已验证且已解析的执行输入：

- 目标 `sample_ids`；
- 权重绝对路径、模型类型和模型编号；
- `imgsz`、`conf`、`overwrite`；
- 任务和创建者编号；
- 任务模型类型及车牌组件制品路径/元数据。

不再依赖重启后的请求体或重新选择模型。

### 3.2 租约与 fencing

新增 `lease_owner`、`lease_expires_at`、`lease_fencing_token`。Worker 使用
条件更新原子领取 `PENDING` 或已过期 `PROCESSING` 作业，每次接管递增 token。
每个样本开始前续租；模型推理结束、写预标注前再次校验 owner/token；进度和
完成/失败状态均使用 fenced 更新，旧 Worker 不能覆盖新 Worker 的状态。

### 3.3 Worker 入口和并发边界

- 运行时：`labelu/internal/application/service/yolo_preannotate_worker_runtime.py`；
- systemd 入口：`labelu/scripts/yolo_preannotate_worker.py`；
- 默认每个进程一次只处理 1 个作业，默认并发上限为 1；
- `YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED=false` 时拒绝启动 Worker；
- 开关关闭时保留原有 API 线程池路径，作为迁移期间兼容方式；
- 开关开启时 API 只入队，不启动模型推理线程，Worker 扫描并领取作业。

## 4. 数据库迁移

正式迁移：
`labelu/alembic_labelu/versions/zd1e2f3a4b5c_add_yolo_preannotate_worker_contract.py`

- `down_revision=zc1d2e3f4a5b`，接当前唯一 head；
- 为既有 `yolo_preannotate_job` 增加四个字段及租约索引；
- downgrade 明确要求使用生产备份恢复，不提供破坏性回滚。

## 5. 验证证据

执行：

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_preannotate_worker_runtime.py labelu/tests/internal/application/service/test_yolo_preannotate_progress.py labelu/tests/internal/adapter/routers/test_yolo_preannotate_router.py -q --disable-warnings --tb=short
7 passed

python -m compileall -q \
  labelu/internal/application/service/yolo_preannotate.py \
  labelu/internal/application/service/yolo_preannotate_worker_runtime.py \
  labelu/internal/adapter/persistence/crud_yolo_preannotate_job.py \
  labelu/internal/domain/models/yolo_preannotate_job.py \
  labelu/alembic_labelu/versions/zd1e2f3a4b5c_add_yolo_preannotate_worker_contract.py \
  labelu/scripts/yolo_preannotate_worker.py
```

覆盖范围：租约接管 token 递增、旧 owner/token 写入拒绝、持久化 payload 被 Worker
读取、关闭开关时失败关闭、API 路由回归。以上是本地 SQLite/L1 证据，不代表生产
验收。

## 6. 影响和风险

- API 返回的作业状态结构未增加必填字段，现有页面仍可轮询；开启 Worker 后初始
  状态可能短暂为 `PENDING`，被领取后变为 `PROCESSING`。
- 权重路径必须在 Worker 进程可读的同一存储上；生产若使用不同挂载点，必须先
  统一路径或增加受控制品存储映射。
- 本轮没有调用真实 AICM、TPU-MLIR 或 BM1688；手工预标注只负责 YOLO 图片推理。
- 未在真实 MySQL、高并发、多进程 systemd 重启和真实模型上验收。

## 7. 验收等级和下一依赖

当前等级：**本地 L1**。

开启生产开关前必须完成：真实 MySQL Alembic 迁移演练、Worker systemd 托管、
共享模型路径检查、重启接管演练、并发压力与真实 YOLO 权重验证。生产切换仍需
用户明确“提交代码”后才能执行提交、推送、部署和线上验证。

## 8. 提交状态

本轮仅本地修改、测试和文档记录；未提交、未推送、未部署。
