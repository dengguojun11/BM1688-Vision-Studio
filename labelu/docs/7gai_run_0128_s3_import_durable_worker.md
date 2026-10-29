# RUN-0128 第三方 S3 问题数据回流持久化 Worker

## 1. 目标

生产问题图片来自策略系统配置的 S3 数据包。原实现由接口进程调用临时线程执行，Web 进程重启、多实例部署时可能丢失任务，也没有跨进程唯一领取证据。本轮将自动回流落成可恢复的数据库队列和独立 Worker；管理人员只处理异常、规则和少量抽查。

## 2. 交付内容

| 角色 | 交付 | 结果 |
|---|---|---|
| R03 架构 | 回流任务使用 Worker 租约和 fencing token，沿用现有导入状态机 | 已完成本地实现 |
| R04 后端 | 增加 Worker 所有者、到期时间、递增 token、尝试次数；接口只入队 | 已完成；独立 Worker 开启时不启动临时导入线程 |
| R05 外部平台 | S3 URI、metadata.json、摘要去重、任务创建、预标注和异常分流逻辑不变 | 已复用既有逻辑 |
| R09 测试 | 双 Worker 竞争、过期接管、旧 token 释放拒绝和禁用 fail-closed | 已完成本地专项 |
| R10 运维 | 提供 `labelu/scripts/experiment_dataset_import_worker.py` 独立入口和配置项 | 已完成入口；systemd 尚待 Ubuntu 验证 |

新增配置：`EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED`（默认关闭）、`EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS`（默认 5 秒）、`EXPERIMENT_DATASET_WORKER_LEASE_SECONDS`（默认 300 秒）、`EXPERIMENT_DATASET_WORKER_BATCH_SIZE`（默认 1）。

新增字段：`worker_lease_owner`、`worker_lease_expires_at`、`worker_fencing_token`、`attempt_count`，并增加状态/租约/主键扫描索引。正式迁移文件为 `z7a8b9c0d1e2_add_experiment_import_worker_lease.py`，只做前向兼容；模型已发布到其他平台的制品不因数据库迁移回滚。

## 3. 并发与失败语义

1. Worker 在数据库事务内按状态和过期租约条件领取，成功领取时 token 加一。
2. 旧 Worker 在开始、状态更新和释放租约时必须匹配 owner+token；匹配失败不再写入。
3. 进程重启后，超过租约的 `PENDING/PROCESSING` 任务可被新 Worker 接管。
4. `package_id` 仍是入口幂等键，内容摘要不一致继续返回冲突；S3 图片去重、metadata 校验、正式任务/草稿任务、预标注和异常样本统计继续复用原状态机。
5. Worker 禁用时接口保留本地兼容线程，便于迁移期回退；生产启用后 Web 不执行导入副作用。

## 4. 本地验证

- Worker 租约专项：`3 passed`。
- 迁移契约：`1 passed`。
- 既有 S3 导入路由：`19 passed`（约 116 秒）。
- 新增 Worker、服务、持久化和模型文件 `compileall` 通过；`git diff --check` 通过。

以上均为本地 Windows/SQLite 证据，不等同于生产通过。

## 5. 尚未放行事项

- 尚未在同版本 MySQL 执行迁移、锁等待、双进程 20 并发和重启接管。
- 尚未增加 Ubuntu/systemd 单元；生产开启前需要将 Worker 纳入部署脚本和日志/告警清单。
- 尚未连接生产 S3 做真实下载、metadata 兼容和大包容量验证。
- 回流任务导入完成后仍由现有训练入口决定是否创建训练作业；本轮不改变“人工确认后冻结 DatasetVersion”的业务门槛。
- 当前没有提交、推送或部署；等待用户明确“提交代码”。
