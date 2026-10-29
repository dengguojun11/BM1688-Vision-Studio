# RUN-0129 第三方 S3 回流 Worker 部署契约

## 1. 本轮目标

把 RUN-0128 的数据库租约 Worker 接入现有 `deploy.sh` 生命周期，确保生产启用后不会出现“接口已停止临时执行，但独立 Worker 没有启动”的无人消费状态。

## 2. 交付内容

| 角色 | 工作包 | 交付 |
|---|---|---|
| R03 架构 | WP0/WP4 | 明确部署开关 `LABELU_ENABLE_EXPERIMENT_DATASET_WORKER` 与应用开关的边界；API 单元显式注入 `EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false/true` |
| R04 后端 | WP4 | 不改 S3 导入状态机；继续由数据库租约和 fencing token保证唯一执行 |
| R05 外部平台 | WP4 | Worker 继续复用生产 S3 配置、metadata.json、去重和任务创建链 |
| R09 测试 | WP5 | 新增 `scripts/tests/test_deploy_experiment_dataset_worker_units.sh`，覆盖 unit、参数、安全限制和生命周期调用 |
| R10 运维 | WP4 | 新增 `labelu-experiment-dataset-worker.service`；接入 `start/stop/restart/reset-clean` 和部署检查清单 |

部署参数：

- `LABELU_ENABLE_EXPERIMENT_DATASET_WORKER=0`：默认关闭，控制是否生成并启用 systemd 单元。
- `EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS=5`：扫描间隔。
- `EXPERIMENT_DATASET_WORKER_LEASE_SECONDS=300`：数据库租约。
- `EXPERIMENT_DATASET_WORKER_BATCH_SIZE=1`：单轮领取数量，一期保持 1，避免 S3 下载和数据库连接池突发压力。

Worker 单元使用 `NoNewPrivileges`、`ProtectSystem=strict`、`ProtectHome`、`PrivateTmp`，只允许写入运行目录和应用数据目录；数据库连接、S3 密钥仍通过 `EnvironmentFile` 注入，不进入页面或日志。

## 3. 验证结果

- PowerShell 等价静态契约：11 项通过，覆盖常量、默认关闭、API/Worker 开关、入口、参数校验、启停/重启/停止和检查清单。
- Python 部署契约回归：`2 passed`；Worker/迁移联合回归：`6 passed`；禁用开关命令行烟测通过并 fail-closed 退出。
- `git diff --check`：通过。
- `bash -n deploy.sh` 和 shell 契约脚本：当前 Windows 的 `C:\Windows\System32\bash.exe` 指向不存在的 WSL `/bin/bash`，无法执行；不能记为通过。

## 4. 生产前置条件

1. Ubuntu 预生产执行 `bash -n deploy.sh` 和 shell 契约测试。
2. 执行 `systemd-analyze verify labelu-experiment-dataset-worker.service`，确认单元安全限制和路径存在。
3. 同版本 MySQL 做双进程领取、过期租约接管、20 并发和重启恢复。
4. 使用真实策略 S3 小包和大包验证下载超时、临时文件清理、metadata 兼容和任务幂等。
5. 以上证据完成前，生产开关继续为 `0`；未提交、未推送、未部署。
