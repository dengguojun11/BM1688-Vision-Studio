# RUN-0303 模型中心 AICM 发布独立 Worker

## 1. 本轮目标

模型中心的“发布模型”仍由标注人员和管理人员共用。发布按钮只负责校验
质量门禁并保存请求；启用独立 Worker 后，AICM 上传、平台模型下载包登记和
发布结果回写由独立进程执行。管理人员后续发现问题时只能关闭本平台下载，
不能撤回 AICM 已被其他平台下载的文件，也不提供自动回滚。

## 2. 现状审计

改造前 `start_publish_job` 在 Web 进程调用
`asyncio.get_event_loop().run_in_executor(..., _run_publish_job_sync, ...)`。
数据库虽然已有 `publish_status`、`publish_job_id`、租约和 fencing 字段，但
领取者是 Web 随机标识；Web 重启后没有独立进程可继续执行。发布任务 JSON
已有模型、标签和质量快照，但没有完整保存保留模板、精度和 BModel 路径等
执行参数。

## 3. 本轮实现

### 3.1 持久化发布请求

`aicm_publish_job.create_job` 新增并保存：

- 是否保留 AICM 模板缓存；
- 选择的 BModel 精度和相对路径；
- 原请求 Web 领取者和 fencing 值（审计字段）；
- 原有环境、发布模式、训练任务、标签契约、固定评估集和质量快照。

### 3.2 Web 与 Worker 分流

- `AICM_PUBLISH_DEDICATED_WORKER_ENABLED=false`：保留原 Web 线程池兼容路径；
- 开关为 `true`：Web 不领取租约、不启动上传线程，只把导出记录置为
  `PENDING` 并返回发布任务；独立 Worker 扫描并领取。
- 同一导出记录只能存在一个 `publish_job_id`，已完成发布仍返回不可重复发布。

### 3.3 Worker 租约与重启接管

运行时文件：`labelu/internal/application/service/aicm_publish_worker_runtime.py`；
命令入口：`labelu/scripts/aicm_publish_worker.py`。

Worker 使用现有 `claim_publish`、`renew_publish_claim`、
`finish_publish_claim`：

- 原子领取 `PENDING` 或已过期 `PROCESSING`；
- 每次接管递增 fencing token；
- 复用 `_run_publish_job_sync` 内的续租和完成写入保护；
- 缺少 job JSON 时以当前 token 标记失败；
- 数据库字段或迁移未就绪时失败关闭；
- 默认一进程一次一个发布作业，避免并发占用 AICM 上传和本地磁盘。

### 3.4 本平台下载目录

`PublishedModelCatalog._persist_local_package` 已经使用临时文件、ZIP 校验和
硬链接原子安装：同一个 AICM `model_set_id` 的相同包可重放，不同内容不会
覆盖长期包，而是失败并保留原文件。Worker 只复用该逻辑，不增加第二套目录。

## 4. 数据库迁移

本轮不新增迁移。所需发布状态、租约和 fencing 字段已由
`z9b0c1d2e3f4_add_aicm_publish_claim_lease.py` 正式迁移提供。生产若未执行该
迁移，Worker readiness 检查会拒绝运行。

## 5. 本地验证

```text
python -m pytest \
  labelu/tests/internal/application/service/test_aicm_publish_worker_runtime.py \
  labelu/tests/internal/common/test_aicm_publish_job.py \
  labelu/tests/internal/application/service/test_yolo_aicm_publish_claim.py \
  labelu/tests/internal/application/test_aicm_publish_sop_gate.py \
  -q --disable-warnings --tb=short
43 passed

python -m compileall -q \
  labelu/internal/application/service/aicm_publish_worker_runtime.py \
  labelu/internal/application/service/yolo_aicm_publish.py \
  labelu/internal/common/aicm_publish_job.py \
  labelu/internal/adapter/persistence/crud_yolo_bm1688_export.py \
  labelu/scripts/aicm_publish_worker.py
```

覆盖：开关关闭不访问数据库、字段未就绪失败关闭、持久化参数传递、单次
领取、缺少 payload 的 fenced 失败、租约 token 递增和旧 token 拒绝。

## 6. 生产边界与验收

当前为本地 L1。尚未连接真实 AICM、同版本 MySQL、共享模型目录、systemd
或生产账号，未执行迁移、提交、推送和部署。开启生产开关前必须完成：

1. MySQL 迁移和备份恢复演练；
2. Worker systemd 托管、重启接管和 2/10/20 并发竞争；
3. 真实 AICM `model_set_id` 与 LabelU 目录、SHA256、第三方回流 metadata
   三方对账；
4. 发布完成后验证普通下载、管理下载和“禁止后续下载”均只影响本平台；
5. 外部已下载模型不回滚，问题模型只能禁止后续下载并发布替代模型。

本轮未提交、未推送、未部署。
