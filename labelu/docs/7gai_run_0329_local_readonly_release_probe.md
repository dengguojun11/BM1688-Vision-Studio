# RUN-0329 本地只读放行探针与 OpenAPI 契约验收（2026-09-21）

## 1. 本轮目标

使用统一的 `staging_acceptance.py` 只读模式核对当前本地运行服务的健康、版本、OpenAPI、一期主链路路由和退休边端路径，确认权限边界与方案一致。

## 2. 责任角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP2/WP5 | OpenAPI 必需路径和退休路径契约 |
| R04 后端 | WP2 | 未登录主链路权限返回和健康接口 |
| R08 前端/UI | WP4 | 页面入口依赖的 API 路由存在 |
| R09 测试 | WP5 | 统一只读放行探针 |
| R10 运维 | OPS | 证明探针只访问本机，不触碰生产写接口 |

## 3. 验收命令与结果

```text
python scripts/staging_acceptance.py \
  --base-url http://127.0.0.1:8002 \
  --readonly-route-audit \
  --evidence .tmp/evidence/run0329-local-route.json
```

结果：命令退出码 `0`，全部探针通过。

关键结果：

- `/healthz`、`/readyz`、`/version`、`/openapi.json`：`200`
- `/api/v1/algorithms`、`datasets`、`model-versions`、`published-models`、任务、训练、模型中心、边端验证等受保护接口：未登录均按契约返回 `403`
- 退休路径 `/api/v1/edge-device-jobs`：`404`
- OpenAPI 必需路径和退休路径检查：通过

## 4. 证据与边界

- 证据文件：`.tmp/evidence/run0329-local-route.json`
- 本轮只发送 GET，不登录、不创建资源、不上传文件、不执行训练、不调用 AICM/S3。
- 本地探针通过不等于生产反向代理、MySQL、真实账号或外部依赖已经验收。

## 5. 提交与部署状态

本轮未提交、未推送、未部署、未重启生产。继续等待用户明确说“提交代码”。
