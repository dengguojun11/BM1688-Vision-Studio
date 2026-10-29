# RUN-0304 第三方 S3 问题图片自动回流 Worker 审计（2026-09-20）

## 目标

按生产页面 `/strategy-integration-s3-config` 对应的第三方 S3 回流链路，核对“自动接收问题图片包 -> 校验与去重 -> 按 AICM `model_set_id` 对齐 -> 聚合/创建或复用任务 -> 触发预标注 -> 等待条件唤醒 -> 失败重试”的真实代码契约。管理人员只处理异常、规则和抽查；正常包不要求人工逐包创建任务。

## 角色结论

| 角色 | 本轮结论 | 验收边界 |
|---|---|---|
| R01 产品 | 进化中心是自动回流状态与异常处理入口，不是手工“创建数据集”入口 | 本地菜单/按钮契约已具备；未做生产页面写操作 |
| R03 架构 | Web 进程只落库 `PENDING`，独立 Worker 领取；数据库租约、过期接管和 fencing token 防止重复执行 | 本地 SQLite 竞争通过；同版本生产 MySQL 锁等待仍待执行 |
| R04 后端 | 保留统一导入状态机和历史策略兼容投影；任务、附件、样本、预标注和自动预标注均由 Worker 处理 | 既有代码审计无新增安全最小修复 |
| R05 外部平台 | 第三方请求以 `package_id` 幂等；S3 对象以桶、对象键、版本/ETag、大小和下载后 SHA-256 作为来源身份 | 真实 S3 版本化对象和供应方重试需预生产回放 |
| R06 模型算法 | `target.model_id` 接收外部 AICM `model_set_id`（同时兼容 `modelId`/`model_id`），通过本地发布目录/导出记录/发布作业血缘解析标签契约 | 真实 AICM 回执与回流 metadata 三方对账未完成 |
| R09 测试 | 覆盖领取竞争、过期接管、旧 token 拒绝、等待重入队、开关关闭和失败后继续处理 | Worker 专项 `13 passed`；未把模拟 S3/SQLite 视为生产通过 |
| R10 运维 | Worker、systemd、生产 MySQL、S3、AICM 均保持失败关闭，直到完成真实凭据、备份、迁移、重启和容量证据 | 未连接生产、未写生产、未提交、未推送、未部署 |

## 已核对的业务链

1. 第三方或策略兼容接口先校验来源请求并写入 `experiment_dataset_import`，唯一 `package_id` 防止重复包产生第二条回流记录。
2. Worker 从 `PENDING` 或过期 `PROCESSING` 领取任务，递增 `worker_fencing_token`，租约续期失败后旧 Worker 不再写入状态或副作用。
3. 读取 `metadata.json`，校验 S3 对象、图片扩展名、样本数量、图片下载完整性和 SHA-256；附件保存桶、对象键、版本、ETag、大小和内容摘要，同名不同对象使用稳定来源后缀。
4. 以 `target.model_id` 对齐 AICM `model_set_id`，解析本平台 `PublishedModelCatalog`、BM1688 导出记录和发布作业血缘；模型尚未就绪时进入 `WAITING_FOR_MODEL`，不创建错误任务。
5. 相同聚合身份的多个包进入 `ExperimentDatasetGroup`，达到最小有效样本数后创建或复用正式任务；不足时进入 `WAITING_FOR_SAMPLES`。标签分类未确定时进入 `WAITING_FOR_CATEGORY`。
6. 导入正式样本后写入 metadata 中的矩形框预标注，并通过统一自动预标注队列触发后续模型预标注；导入异常只形成失败/部分完成状态，不能悄悄报成功。
7. Worker 每轮先扫描等待状态，按冷却时间重新入队；模型发布、分类维护和重复包回调可主动唤醒等待记录。任务创建和预标注副作用均由 Worker 执行，不在 Web 请求线程中下载 S3 或创建任务。

## 并发与失败关闭审计

- `claim_pending` 的条件更新确保同一行在 `2/10/20` 个独立进程竞争时只有一个获胜者；租约过期后新 Worker 递增 fencing token 接管。
- `update_by_worker_lease`、`worker_lease_is_valid`、续租和释放均校验 owner、token 和未过期时间；旧 Worker 的迟到状态写入被拒绝。
- 聚合组使用行锁和 `GROUP_STATUS_PROCESSING` 防止同一聚合组并行创建正式任务；失租时不写失败/完成终态，下一代可接管。
- 进程级异常只计为当前包失败并继续处理下一包；单包 S3 超时不会阻塞整个批次。
- 独立 Worker 默认关闭；缺迁移字段、缺生产 JWT 安全配置或数据库不可检查时，预检直接失败，不领取任务。
- 生产容量仍需用同版本 MySQL、真实 S3 和受控 Worker 做锁等待、重复回调、下载吞吐、重启接管和 2/10/20/50 并发矩阵；本地 SQLite 结果不能替代该证据。

## 本轮验证

命令：

```text
python -m pytest labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q --disable-warnings --tb=short
```

结果：`13 passed`，约 50.71 秒，退出码 0。覆盖内容包括：租约竞争、过期接管、旧代次拒绝、Worker 开关关闭、预检失败关闭、等待状态重入队、单包失败后继续处理、2/10/20 独立进程领取竞争和迁移字段预检。

代码审计还核对了：

- `labelu/internal/application/service/experiment_dataset_import_worker.py`
- `labelu/internal/application/service/experiment_dataset_import.py`
- `labelu/internal/adapter/persistence/crud_experiment_dataset_import.py`
- `labelu/internal/domain/models/experiment_dataset_import.py`
- `labelu/internal/application/service/strategy_task_integration.py`

## 生产边界与后续放行条件

本轮只做本地代码和测试审计，没有连接 `http://113.141.72.175:10000` 的写接口，没有读取或写入生产数据库，没有访问生产 S3/AICM，没有执行迁移、提交、推送或部署。生产页面对应链路是否已经上线，不能由本地测试、OpenAPI 路径、HTTP 200 或 SQLite 竞争结果推断。

放行前必须补齐：同版本 MySQL 迁移/备份恢复；真实 S3 包下载、对象变更和断点续传；AICM `model_set_id`、本地下载目录、回流 metadata 的三方对账；systemd 重启接管；双 Worker 过期租约；任务/附件/预标注副作用幂等；真实 2/10/20/50 并发和监控告警。所有证据完成前保持 `EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false`。

本轮结论：本地 L1 契约完成，无新增安全最小代码修复；生产 L2/L3 未完成。
