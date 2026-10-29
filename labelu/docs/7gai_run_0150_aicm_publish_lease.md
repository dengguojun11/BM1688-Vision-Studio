# RUN-0150 AICM 发布跨进程租约与幂等记录

- 日期：2026-09-19（Asia/Shanghai）
- 工作包：WP3，模型中心发布链
- 责任角色：R03 架构与迁移、R04 后端业务、R09 测试；R05 外部平台和 R10 运维协作
- 当前等级：本地 L1；未提交、未推送、未部署、未写入生产

## 1. 为什么要改

原发布入口先在文件目录创建 JSON 任务，再由线程调用 AICM。两个 Web 进程或自动进化重试可以同时看到同一个已编译导出记录并各自上传，文件任务目录不能作为跨主机唯一真源。这样会产生两个 AICM 模型集、两个发布编号和后续回流无法确定源模型的问题。

本轮复用现有 `yolo_bm1688_export`，不另建发布主表，也不引入项目、设备发布、OTA 或模型回滚。AICM `model_set_id` 仍是外部平台和第三方问题回流使用的模型编号；发布到外部平台后，本平台继续保留模型目录和下载开关。

## 2. 状态和字段

| 字段 | 含义 | 写入规则 |
|---|---|---|
| `publish_status` | `PROCESSING`、`COMPLETED`、`FAILED` | 领取置处理中；成功置完成；异常置失败 |
| `publish_job_id` | 发布 JSON 任务号 | 领取时写入，和文件任务一一对应 |
| `publish_lease_owner` | 当前发布执行器身份 | Web 进程生成唯一 owner |
| `publish_lease_expires_at` | 租约过期时间 | 领取和续租时更新 |
| `publish_fencing_token` | 每次接管递增的令牌 | 旧执行器带旧令牌不能续租或写终态 |

业务状态只有三条有效路径：

1. 没有已发布模型且没有有效租约：数据库条件领取，写入 `PROCESSING` 和递增 token。
2. 有效租约仍在：其他请求返回“正在发布”；同一 `job_id + owner` 重复请求保持幂等。
3. 过期或失败：允许新任务接管并递增 token；已写入 `published_model_id` 或 `COMPLETED` 的记录直接拒绝重复发布。

## 3. 代码改动

- `labelu/internal/domain/models/yolo_bm1688_export.py`：增加五个发布租约字段和索引。
- `labelu/internal/adapter/persistence/crud_yolo_bm1688_export.py`：增加领取、条件续租、fencing 完成/失败写入；领取同时带状态、已发布编号和过期时间条件。
- `labelu/internal/application/service/yolo_aicm_publish.py`：`start_publish_job` 在创建文件任务和启动线程之前领取数据库租约；后台任务使用续租监控，完成目录登记后再按 token 写 `COMPLETED`，非成功结果和异常写 `FAILED`，旧 token 写入失败关闭。
- `labelu/internal/common/aicm_publish_job.py`：支持由服务传入已领取的 `job_id`，避免数据库任务号和文件任务号不一致。
- `labelu/alembic_labelu/versions/z9b0c1d2e3f4_add_aicm_publish_claim_lease.py`：正式 Alembic 增量迁移，降级采用备份恢复，不自动删列。
- `labelu/tests/conftest.py`：隔离 SQLite 测试库补齐五个兼容列。

## 4. 本地验证

已执行：

- `python -m compileall -q`：发布服务、CRUD、模型、发布任务通过。
- `git diff --check`：目标文件通过。
- `python -m pytest labelu/tests/internal/application/service/test_yolo_aicm_publish_claim.py labelu/tests/internal/application/service/test_yolo_aicm_publish_helpers.py labelu/tests/internal/application/test_aicm_publish_sop_gate.py -q`：`127 passed`。
- `python -m pytest labelu/tests/internal/application/service/test_yolo_aicm_publish_claim.py labelu/tests/alembic/test_empty_database_upgrade.py labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py -q`：`39 passed`。
- `alembic -c labelu/alembic_labelu/alembic.ini heads`：唯一 head 为 `z9b0c1d2e3f4`。
- Alembic/空库/迁移审计专项：`10 passed`；正式 head 已更新为 `z9b0c1d2e3f4`。

专项覆盖：单一领取者、有效租约拒绝第二执行器、过期接管递增 token、续租必须匹配 owner/job/token、旧 token 完成写入拒绝、成功后再次领取拒绝。

## 5. 并发、外部副作用和生产边界

- 本地 SQLite 只证明代码路径和条件契约，不等价于生产 MySQL 行锁、锁等待和跨进程 20 并发。
- AICM 上传是不可回滚的外部副作用；租约过期前由后台监控续租，接管后旧执行器不能再写本地完成状态。若外部平台在网络分区期间已经接受上传，仍需用 AICM `model_set_id` 对账和人工禁止下载处理，不能声称自动回滚。
- 尚未执行同版本 MySQL 迁移、2/10/20/50 并发请求、真实 AICM 上传、systemd 重启接管、生产模型目录和第三方 S3 回流对账；生产发布/回流 Worker 保持失败关闭。
- 联合第三方调用回归仍有 3 项失败，均为并行去项目改造与旧测试夹具不一致：`StrategyTaskIntegration` 已不再接受 `project_id`，旧测试仍构造该字段。该失败记录为下一工作包的去项目化迁移项，不能通过恢复项目字段或默认项目 ID 绕过。
- 迁移正式执行前必须备份并核对 `z7a8b9c0d1e2 -> z9b0c1d2e3f4` 单链；不得直接在生产库用 ORM 自动建表替代 Alembic。

## 6. 下一步放行条件

R03/R10 在同版本 MySQL 沙箱完成迁移和执行计划核对；R09 完成 20 并发唯一发布、副作用次数和重启接管证据；R05 提供脱敏 AICM `model_set_id`、S3 `metadata.json`、图片包和 LabelU 任务/样本对账；上述证据全部通过后，才允许评估预生产开关。当前仍等待用户明确“提交代码”，本轮不得提交、推送或部署。
