# RUN-0135 规范路由与 Nginx 分流契约审计

## 1. 基本信息

- 日期：2026-09-19（Asia/Shanghai）
- 工作包：WP0/WP5，R03 架构、R04 后端、R09 测试、R10 运维
- 范围：本地 FastAPI 路由注册、认证和响应契约；一期退休控制面；`deploy.sh` 生成的 Nginx 精确路径与前端 SPA 回退顺序。
- 环境边界：本轮只做本地代码/测试和生产既有只读证据对账；未提交、未推送、未部署，未写入生产数据库或业务数据。

## 2. 本地审计结论

### 2.1 一期规范路由

本地 `labelu/internal/adapter/routers/__init__.py` 已注册以下规范路由：

| 路径 | 本地状态 | 认证 | 响应约定 |
|---|---|---|---|
| `/api/v1/datasets` | 已注册 `GET/POST` | `get_current_user` + Bearer | `OkRespWithMeta` 列表、`OkResp` 创建 |
| `/api/v1/algorithms` | 已注册 `GET/POST` | `get_current_user` + Bearer | `OkRespWithMeta` 列表、`OkResp` 创建 |
| `/api/v1/model-versions` | 已注册 `GET/POST` | `get_current_user` + Bearer | `OkRespWithMeta` 列表、`OkResp` 创建 |
| `/api/v1/workspace/summary` | 已注册 `GET` | `get_current_user` + Bearer | `OkResp[WorkspaceSummaryResponse]` |

本地 `app.openapi()` 实测四条路径均存在，操作均带 `HTTPBearer` 安全声明；未带令牌时由统一认证依赖拒绝。中文 OpenAPI 的 `/api-docs/openapi.json` 与根 `/openapi.json` 均由当前 FastAPI 应用生成，不能由前端静态文件替代。

### 2.2 退休控制面

`edge_device_job.router` 仍作为兼容地址挂载，但所有路由经过 `ensure_legacy_control_plane_open`，调用立即返回业务码 `30004`、HTTP 403；`hide_router_from_openapi(router)` 在应用注册前将三条旧路径从 OpenAPI 隐藏。它不是一期板端验证入口，一期入口是 `yolo-bm1688-exports/{export_id}/board-validation` 和内部执行程序使用的 `/board-agent/jobs/*`。

本地 OpenAPI 未发现：

- `/api/v1/edge-device-jobs`
- `/api/v1/edge-device-jobs/claim`
- `/api/v1/edge-device-jobs/{job_id}/result`

### 2.3 Nginx 分流

`deploy_write_nginx_frontend_config()` 已将 `/api/`、`/internal/`、`/ws/` 和后端文档/探活路径代理到后端；`/docs`、`/redoc`、`/openapi.json`、`/healthz`、`/readyz`、`/version`、`/api-docs`、`/api-docs/redoc`、`/api-docs/openapi.json` 使用精确匹配，且定义在 `location / { try_files ... /index.html; }` 之前。`^~ /api-docs/` 负责文档静态资源代理。Nginx 精确匹配优先级高于前缀匹配，因此不会被 SPA 回退覆盖。

生产曾出现 `/api-docs/openapi.json` 返回前端 HTML 的证据，说明旧配置未按当前脚本重生成。为防止 HTTP 200 HTML 被误判为成功，本轮在 `deploy.sh` 的直连检查和 Nginx 检查中补充该路径的 JSON 内容类型及首字符探针；探针失败会进入部署失败/回退路径。

## 3. 验证证据

- `python -m pytest labelu/tests/internal/common/test_openapi_docs.py labelu/tests/internal/adapter/routers/test_phase_one_retired_control_plane.py labelu/tests/scripts/test_staging_acceptance.py scripts/tests/test_deploy_nginx_backend_routes.py -q`：`22 passed`。
- `app.openapi()` 静态实测：四条规范路径存在且带 Bearer 安全声明；三条 `edge-device-jobs` 路径不存在。
- `git diff --check`：本轮文件无空白错误。
- `bash -n deploy.sh`：当前 Windows 环境无可用 Bash/WSL，未执行；必须在 Ubuntu 部署主机补做。
- 生产 `RUN-0134` 只读证据保持有效：`/openapi.json` 为真实 JSON，四条规范路径缺失，旧 `edge-device-jobs` 暴露，`/api-docs/openapi.json` 为 HTML；本轮未声称生产已修复。

## 4. 放行与后续

1. 用户明确“提交代码”后，才允许提交、推送和部署本轮 Nginx 探针改动。
2. 部署主机执行 `bash -n deploy.sh`、Shell 契约测试、`systemd-analyze verify`，再执行 Nginx `nginx -t`。
3. 部署后使用标注人员和管理人员账号分别读取四条规范路径，匿名请求必须 401/403；`/api-docs/openapi.json` 必须为 `application/json`，首字符为 `{` 或 `[`。
4. 生产 OpenAPI 仍缺四条规范路径或仍暴露旧路径时，部署验收硬失败；不以进程存活、HTTP 200 或前端页面返回替代 API 契约验收。

## 5. 本轮状态

- 代码：已做最小部署探针增强。
- 测试：本地专项通过；Linux/Bash、生产和 Nginx 真机待执行。
- 提交/推送/部署：未执行，等待用户明确授权。
- L0/L1/L2/L3/L4：本地路由与分流达到 L1；生产路由修复、Nginx 真机和业务账号验收未完成，平台整体不得超过 L1。
