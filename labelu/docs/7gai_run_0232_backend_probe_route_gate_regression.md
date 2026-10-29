# RUN-0232 后端探针路由与部署闸门回归

## 1. 目标

核对部署脚本是否把 `/healthz`、`/readyz`、`/version`、`/openapi.json` 和中文接口文档路径优先代理到后端，避免 Nginx 的 SPA 兜底把健康探针返回成 HTTP 200 的 HTML。该批次只验证脚本契约，不代表线上 Nginx 已经加载当前配置。

## 2. 角色与交付

| 角色 | 工作包 | 本轮交付 | 验收边界 |
|---|---|---|---|
| R03 架构 | WP2 | 核对后端精确路径与 SPA fallback 的顺序关系 | 只读源码契约，不替代线上配置 |
| R04 后端 | WP2 | 核对健康/就绪/版本 JSON 响应和 OpenAPI 规范路径 | 不改业务接口 |
| R09 测试 | WP5 | 执行部署路由、迁移发布闸门和证据完整性专项 | `15 passed` |
| R10 运维 | OPS | 核对部署清单失败关闭和探针检查条件 | 未执行 Nginx reload 或生产探针 |

## 3. 本地验证

```text
python -m pytest scripts/tests/test_deploy_nginx_backend_routes.py \
  scripts/tests/test_migration_release_deploy_gate.py \
  scripts/tests/test_release_evidence_integrity.py \
  -q --disable-warnings --tb=short
15 passed in 0.18s
```

验证内容：

- 后端专用 exact route 出现在根 SPA location 之前；
- `/api-docs/` 资源前缀走后端代理；
- 健康/就绪/版本/OpenAPI 必须检查 `application/json`，HTML 兜底不能作为服务就绪；
- 迁移备份、并发、锁等待、OpenAPI 规范和退休路由证据缺失时发布清单失败关闭。

Windows 本地没有可用 WSL `/bin/bash`，`scripts/tests/test_deploy_nginx_backend_routes.sh` 未执行，不能把 shell 脚本结果写成通过；Python 版本覆盖了同一组源码断言。

## 4. 生产边界

本轮达到 **L1**。未连接生产、未修改 Nginx、未 reload/restart systemd、未提交、未推送、未部署。生产仍需部署当前代码后，用 `curl` 检查四个精确路径的响应头和 JSON 结构，并确认旧 SPA 静态资源不接管这些路径。

