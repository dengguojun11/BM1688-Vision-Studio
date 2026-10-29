# RUN-0314 生产只读路由与 OpenAPI 版本漂移复核（2026-09-20）

## 1. 目标和安全边界

由 R01 产品、R03 架构、R04 后端、R08 前端、R09 测试和 R10 运维复核生产主链路是否已经部署。使用只读路由审计脚本，只发送 GET 请求，不登录、不创建任务、不上传文件、不训练、不编译、不发布、不修改数据库或配置。

## 2. 执行命令和证据

```text
python scripts/staging_acceptance.py --readonly-route-audit --base-url http://113.141.72.175:10000 --timeout 10 --evidence .tmp/evidence/run0314/production-readonly-route-audit.json
```

证据文件：

```text
.tmp/evidence/run0314/production-readonly-route-audit.json
```

脚本退出码：`1`。退出码为失败符合当前生产漂移事实，不是脚本误报：健康探针返回非 JSON，且 OpenAPI 主链不完整并暴露退休路径。

## 3. 生产观测结果

| 检查项 | 实际结果 | 判断 |
|---|---|---|
| `/healthz` | `200`，文本/前端 HTML | P0，不能作为后端存活 JSON 证据 |
| `/readyz` | `200`，文本/前端 HTML | P0，不能作为就绪证据 |
| `/version` | `200`，文本/前端 HTML | P0，不能确认后端版本 |
| `/openapi.json` | `200`，JSON | 文档可访问，但内容存在版本漂移 |
| `/api/v1/algorithms` | `404` | 缺少一期算法目录路由 |
| `/api/v1/datasets` | `404` | 缺少一期数据集路由 |
| `/api/v1/model-versions` | `404` | 缺少一期模型版本路由 |
| `/api/v1/workspace/summary` | `404` | 缺少工作台汇总路由 |
| `/api/v1/tasks` | `401` | 认证保护存在，但未证明任务页面已联调 |
| `/api/v1/published-models` | `401` | 认证保护存在，但未证明模型目录已联调 |
| `/api/v1/yolo-bm1688-exports` | `401` | 认证保护存在，但未证明模型中心已联调 |
| `/api/v1/yolo-edge-validation/jobs` | `401` | 认证保护存在，但未证明边端页面已联调 |
| `/api/v1/edge-device-jobs` | `404`，但 OpenAPI 仍登记该路径及写操作 | 退休控制面仍暴露，属于版本/路由漂移 |

OpenAPI 契约审计同时报告：缺少 `/api/v1/algorithms`、`/api/v1/datasets`、`/api/v1/model-versions`、`/api/v1/workspace/summary`；仍暴露 `/api/v1/edge-device-jobs`、`/api/v1/edge-device-jobs/claim`、`/api/v1/edge-device-jobs/{job_id}/result`。

## 4. 角色结论

作为部署漂移对照，本地直接读取当前 `app.openapi()` 的结果为：四个一期主链路路径均已注册，`/api/v1/edge-device-jobs*` 路径列表为空；`/healthz`、`/readyz`、`/version` 按设计使用 `include_in_schema=False`，但实际路由存在并返回 JSON。由此可以确认，当前生产结果反映的是旧后端/旧静态入口或反向代理部署，没有把本地实现发布到生产。

- **R01/R08**：线上页面不能依赖 `/healthz`、`/readyz`、`/version` 的 HTML 200；主菜单需要的接口必须先和当前部署版本对齐。
- **R03/R04**：本地已经存在内部鉴权和主链路路由契约，但生产 OpenAPI 仍是旧版本，说明部署/反向代理/后端代码存在漂移。
- **R09**：只读审计脚本能够稳定识别 HTML 假健康、缺路由和退休路由暴露，当前结果应作为阻断证据保留。
- **R10**：需要确认 Nginx 实际 upstream、后端进程版本、静态目录和服务重启状态；未取得部署授权前不得修改生产。

## 5. P0 整改项和放行条件

1. 在预生产修复四个 JSON 探针，返回 JSON、版本、时间戳和真实后端状态，不再由 SPA 回退接管。
2. 将当前本地后端和 OpenAPI 契约部署到预生产，确认四个一期主链路路由存在且认证行为正确。
3. 禁止生产 OpenAPI 暴露已退休的设备作业写入口；保留兼容读取时必须明确只读且不创建副作用。
4. 预生产通过后重新运行同一只读脚本，保存 `passed=true`、响应类型、状态码、版本和证据 SHA256；随后才能申请生产发布。

## 6. 状态

- 本轮代码变更：无。
- 生产写入/迁移/部署：无。
- 本轮等级：生产 L0，只读阻断审计。
- 训练、编译、边端、S3 回流和 AICM Worker：继续失败关闭。
- 提交/部署：不允许，等待 P0 修复、预生产证据和用户明确“提交代码”。
