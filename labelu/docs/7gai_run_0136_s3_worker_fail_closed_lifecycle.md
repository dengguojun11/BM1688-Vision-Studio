# RUN-0136 第三方 S3 回流 Worker 失败关闭与重启接管门禁

## 1. 本轮范围

第三方问题图片的真实来源仍是生产策略页面配置的 S3/MinIO 数据包。LabelU 不新增 S3 轮询协议：外部系统通过现有回流接口提交 `package_id`、S3 URI、`metadata.json` 和 AICM `model_id`，系统把请求持久化为回流记录，再由独立 Worker 领取。本文只收口 Worker 的启动安全、单轮故障隔离和租约接管边界。

## 2. 发现与改动

### 2.1 启动前失败关闭

原独立入口开启后直接执行领取，未验证数据库是否已经包含租约字段，也未执行生产安全配置检查。现在增加 `dedicated_worker_ready()`：

- 校验回流表 ORM 和实际数据库中的 `worker_lease_owner`、`worker_lease_expires_at`、`worker_fencing_token`、`attempt_count`；
- 复用生产安全检查，生产环境缺少 JWT、策略 S3 凭据、回调密钥等条件时拒绝启动；
- 增加 `--preflight`，只做门禁检查，不领取数据；
- 失败返回退出码 `78`，systemd 配置 `RestartPreventExitStatus=78`，避免未迁移/缺配置时无限重启。

### 2.2 单轮故障隔离

Worker 一轮领取多个回流包时，单个包的异常不再中断整轮；异常计入 `failed`，后续已领取包继续处理。具体包的终态和错误信息仍由既有导入状态机写入，未新增第二套任务状态机。

### 2.3 聚合包租约边界

聚合导入发生租约接管并抛错时，旧 Worker 不再调用 `_mark_group_failed` 覆盖新 Worker 的处理结果；新 Worker 可以从仍未完成的包继续接管。非聚合包原有 owner+token 条件写入和心跳续租保持不变。

### 2.4 systemd 契约

第三方 S3 Worker 单元新增：

- `ExecStartPre=... --preflight`；
- `RestartPreventExitStatus=78`；
- 原有 `NoNewPrivileges`、`ProtectSystem=strict`、`ProtectHome`、`PrivateTmp` 和限定写目录继续保留；
- API 单元仍显式注入 `EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false`，Worker 单元显式注入 `true`；开关默认关闭。

## 3. 并发与恢复语义

1. 回流接口幂等键仍为 `package_id`，内容摘要不一致返回冲突；不改变 S3 图片、metadata、任务复用和 AICM `model_id` 对齐逻辑。
2. Worker 领取仍使用数据库租约和递增 fencing token；过期租约可被新进程接管，旧进程的状态/释放操作因 owner+token 不匹配而无效。
3. systemd 重启后，`PENDING` 和过期 `PROCESSING` 记录可重新领取；已完成/失败终态不进入 Worker 扫描。
4. 启动门禁失败时 API 不应切换到“已开启但无人消费”状态；生产开关保持关闭，直到迁移、systemd、MySQL 和真实 S3 验收完成。

## 4. 本地验证

- Worker 专项：`6 passed`，覆盖租约接管、禁用关闭、单包失败后继续、启动前门禁和 `--preflight` 退出码。
- 部署契约：`2 passed`，覆盖 systemd 入口、`--preflight`、`RestartPreventExitStatus=78`、默认关闭和参数校验。
- `compileall`：通过。
- `git diff --check`：通过。

以上均为 Windows/SQLite 或静态契约证据，仅证明本地行为，不代表 Linux/systemd 或生产可用。

## 5. 未完成与放行条件

- Windows 无可用 WSL，未执行真实 `bash -n`、shell 契约脚本或 `systemd-analyze verify`；
- 尚未在同版本生产 MySQL 做双 Worker 锁等待、20 并发、节点重启接管和连接池观察；
- 尚未连接生产 S3 验证真实 `metadata.json`、大包下载、超时、临时文件清理和 AICM 模型 ID 对账；
- 未提交、未推送、未部署，生产开关继续为 `0`。

