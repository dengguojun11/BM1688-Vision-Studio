# RUN-0284 显式环境文件优先级与本地数据库隔离

日期：2026-09-20（Asia/Shanghai）  
责任角色：R03 架构、R04 后端、R09 测试、R10 运维  
工作包：WP0、WP2、WP5、OPS

## 1. 发现

本地启动时即使设置了 `LABELU_ENV_FILE=config/env/.env.local`，当前目录 `.env` 仍可能覆盖它；当 `.env` 留有共享 MySQL 地址时，开发进程会先读到 MySQL，再由安全闸门拒绝启动。虽然没有产生生产写入，但配置优先级不清晰，阻断了可靠的本地 HTTP 验收。

## 2. 修复

- `labelu/internal/common/config.py`：将当前目录 `.env` 放在前面，将显式 `LABELU_ENV_FILE` 放在最后；Pydantic 后加载的显式文件覆盖兼容文件。
- `labelu/tests/internal/common/test_config_env_bootstrap.py`：增加共享 `.env` 与显式环境文件冲突时，显式文件选择 SQLite 的回归测试。
- 不改变进程环境变量优先级；不修改任何生产 `.env` 内容；不新增迁移。

## 3. 验收边界

- 目标：本地明确选择隔离 SQLite，生产明确选择部署目录 `.env`，避免仓库根目录兼容文件误覆盖。
- 仍保持开发模式遇到 MySQL 且未显式放行时失败关闭。
- 本轮不连接生产、不发送业务写请求、不提交、不推送、不部署。

已验证：

- 配置专项：`9 passed, 113 warnings`。
- 未设置 `LABELU_DEV_DATABASE_URL`、未设置进程 `DATABASE_URL`，仅使用显式 `config/env/.env.local` 启动本地服务。
- 本地 HTTP 只读审计：`passed=true`；健康/就绪/版本 JSON `200`，OpenAPI `200`，退休 `edge-device-jobs` `404`。
- 证据：`.tmp/7gai-local-readonly-route-audit-env-precedence.json`。

## 4. 下一依赖

配置修复通过后，重新执行本地 HTTP 只读路由验收；生产部署仍需用户明确“提交代码”，并在部署后复核健康 JSON、OpenAPI、两类账号和 Worker 开关。
