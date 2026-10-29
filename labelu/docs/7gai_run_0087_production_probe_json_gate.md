# RUN-0087 生产探针与部署脚本 JSON 门禁

## 1. 生产只读结论

2026-09-18 对生产入口 `http://113.141.72.175:10000` 进行只读 GET：

- `/healthz`、`/readyz`、`/version`、`/api-docs/openapi.json` 返回 HTTP 200，但内容类型为 `text/html`，响应是前端单页应用 HTML，不能证明后端健康、就绪、版本或文档服务正常。
- `/openapi.json` 返回 `application/json`，只能证明该路径当前可返回 OpenAPI JSON，不能替代前三个运维探针。
- 未执行登录、写请求、任务创建、模型发布、下载、设备连接或配置修改。

## 2. 本轮本地实现

`deploy.sh` 新增统一的 `deploy_probe_json_url_expect_200`，对部署前后四个入口同时检查：

1. HTTP 状态码必须为 200；
2. `Content-Type` 必须包含 `application/json`；
3. 响应首字符必须为 `{` 或 `[`。

Nginx 前端模式生成精确后端代理规则，重载后再次执行相同探针；若返回 SPA HTML、状态码错误或内容类型错误，部署检查失败并回退 Nginx 前端模式，不能把 HTTP 200 当成服务就绪。

## 3. 本地验证

```text
python -m pytest scripts/tests/test_deploy_nginx_backend_routes.py -q --disable-warnings --tb=short
3 passed

D:\Git\bin\bash.exe -n deploy.sh scripts/tests/test_deploy_nginx_backend_routes.sh
通过
```

同时通过 `test_health.py` 与 `test_openapi_docs.py`：`14 passed`；目标文件 `git diff --check` 通过。

## 4. 未完成边界

- 本轮未提交、未推送、未部署，生产 Nginx 仍未证明已加载精确代理规则。
- 需要用户明确“提交代码”后，才能进入提交、预生产/生产配置发布和外部入口复验。
- 外部入口复验必须保存状态码、响应头、响应首字符、时间戳、Nginx 生效配置和应用版本；若仍返回 HTML，生产放行必须阻断。

结论：本地部署门禁达到 L1；生产探针实际修复、预生产验证和生产放行未完成。
