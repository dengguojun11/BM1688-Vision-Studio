# RUN-0298 自动样本预标注独立 Worker、租约 fencing 与并发限流

## 1. 目标与范围

本轮针对“新图片/第三方导入后自动预标注”链路。此前 API 进程会通过
`threading.Thread` 启动 `_run_durable_auto_preannotate_job`，虽然已有租约，
但长时间模型推理仍占用 Web 进程生命周期，进程重启时只能依赖 API 内 watchdog
重新拉起。本轮补充可由 systemd 单独运行的 Worker 入口，并把租约接管升级为
数据库 fencing token。

本轮不宣称已改造手工“开始预标注”中的 `YoloPreannotateJob`、TagTool 和
VLX/Rex-Omni 作业；这些作业仍走各自的兼容线程路径，后续需单独迁移为持久化
队列后才能关闭对应 API 线程。

## 2. 代码与数据变更

- `auto_label_job.lease_fencing_token`：每次领取递增，旧 Worker 的 token 不能
  更新进度、续租或写终态。
- `crud_auto_label_job.claim_auto_sample_job_with_token`：原子领取并返回新 token；
  原 `claim_auto_sample_job` 保留布尔兼容接口。
- `auto_sample_preannotate.py`：租约续期、逐样本进度、完成和失败均校验 owner
  与 token；启用独立 Worker 时 API 不启动预标注线程/watchdog。
- `auto_preannotate_worker_runtime.py` 与 `scripts/auto_preannotate_worker.py`：
  独立扫描、限流执行、过期租约接管和 `--once` 入口。
- `config.py`：增加独立 Worker 开关、扫描周期、租约时长、批量上限和并发上限，
  默认关闭，未完成真实验收时失败关闭。
- `zc1d2e3f4a5b_add_auto_preannotate_fencing.py`：正式 Alembic 迁移。

## 3. 并发、重启和失败关闭

1. 同一个作业只能被数据库条件更新成功的 Worker 领取；领取同时递增 token。
2. Worker 暂停或进程崩溃后，租约过期即可被新 Worker 接管；新 token 使旧进程
   的迟到写入失败，不会覆盖新进度或终态。
3. 默认每个 Worker 进程一次只执行一个作业（`AUTO_PREANNOTATE_MAX_CONCURRENT_JOBS=1`），
   只有完成内存、GPU 和 P95 实测后才允许提高上限。
4. 开关为关闭、数据库字段未迁移或租约/token 校验失败时，系统不执行模型推理，
   作业保持可重试状态并记录失败原因。

## 4. 验证

```text
python -m pytest labelu/tests/internal/application/service/test_auto_preannotate_worker_runtime.py labelu/tests/internal/application/service/test_auto_sample_preannotate.py -q --disable-warnings --tb=short
17 passed
python -m compileall -q labelu/internal/application/service/auto_sample_preannotate.py labelu/internal/application/service/auto_preannotate_worker_runtime.py labelu/internal/adapter/persistence/crud_auto_label_job.py labelu/internal/domain/models/auto_label_job.py labelu/alembic_labelu/versions/zc1d2e3f4a5b_add_auto_preannotate_fencing.py
```

专项覆盖：fencing token 递增、旧 owner/token 写入拒绝、独立 Worker 关闭时拒绝
执行、独立开关开启时 API 不启动线程。

## 5. 环境证据与放行

- 本地 SQLite：L1，通过上述专项；未执行真实模型推理。
- 同版本 MySQL/预生产：未完成。
- 第三方 S3、真实 YOLO 权重、GPU、systemd 重启接管：未完成。
- 生产 `http://113.141.72.175:10000`：未连接、未迁移、未部署。
- 本轮不提交、不推送、不部署；`AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED` 保持关闭。

## 6. 下一依赖

在预生产 MySQL 完成重复领取、锁等待、过期接管和断电恢复后，由运维补充
systemd 单元与日志/监控；随后再为手工 YOLO/TagTool/VLX 预标注作业建立统一
持久化队列，才能宣称“所有预标注均不在 Web 进程内运行”。
