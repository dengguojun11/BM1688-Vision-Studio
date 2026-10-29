# RUN-0157：服务探针与 SPA 回退边界

日期：2026-09-19（Asia/Shanghai）  
范围：服务健康检查、就绪检查、版本信息、规范接口文档、前端静态资源挂载

## 本轮结论

生产只读探针曾返回前端 HTML，不能据此判断服务健康。代码审计确认健康路由在
根静态资源挂载之前注册；本轮增加防回归测试，强制服务探针和规范 OpenAPI
入口返回 JSON，不能被 SPA 的 `index.html` 回退吞掉。

## 代码交付

- 新增 `labelu/tests/internal/adapter/routers/test_health.py`：
  - `/healthz`、`/readyz`、`/version` 必须为 JSON，且包含版本信息；
  - `/openapi.json` 必须为 JSON，并返回中文接口文档标题。
- 未增加菜单、登录角色、业务表或生产配置；保留现有根级未鉴权探针设计。

## 本地验收证据

```text
python -m pytest labelu/tests/internal/adapter/routers/test_health.py -q --disable-warnings --tb=short
2 passed

python -m pytest labelu/tests/internal/common/test_openapi_docs.py -q --disable-warnings --tb=short
8 passed
```

## 生产边界

本地路由顺序已验证，但生产曾返回 HTML，说明线上 Nginx/应用版本仍需在部署后
重新验证。当前没有提交、推送或部署，也没有修改生产 Nginx。上线验收必须记录
四个 URL 的 HTTP 状态、`Content-Type`、响应首字符、版本号和时间戳；任一探针
返回 HTML 都要阻断放量。

本轮等级为本地 L1。
