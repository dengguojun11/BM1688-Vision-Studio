# RUN-0318 生产只读路由复核（2026-09-20）

## 1. 执行

```text
python scripts/staging_acceptance.py --readonly-route-audit --base-url http://113.141.72.175:10000 --timeout 10 --evidence .tmp/evidence/run0318/production-readonly-route-audit.json
```

本轮只执行 GET，未携带账号、请求体或写操作。

## 2. 结果

证据：`.tmp/evidence/run0318/production-readonly-route-audit.json`。

| 检查项 | 实际结果 | 判定 |
|---|---|---|
| `/healthz`、`/readyz`、`/version` | HTTP 200，但返回文本/SPA 内容，不是 JSON | 失败 |
| `/openapi.json` | HTTP 200 JSON | 通过格式检查 |
| `/api/v1/algorithms` | 404 | 未部署一期路由 |
| `/api/v1/datasets` | 404 | 未部署一期路由 |
| `/api/v1/model-versions` | 404 | 未部署一期路由 |
| `/api/v1/workspace/summary` | 404 | 未部署一期路由 |
| `/api/v1/edge-device-jobs*` | OpenAPI 仍暴露三条退休路径 | 失败 |
| 其他受保护主链路 | 未登录返回 401 | 认证边界存在，但不能证明版本正确 |

脚本退出码为 `1`，整体 `passed=false`。

## 3. 判断

生产当前仍是旧后端、旧静态文件或旧反向代理组合。由于本地源码已经包含健康 JSON 探针、一期主链路和退休路径隐藏逻辑，当前不能通过本地测试结果替代生产证据，也不能在没有部署授权和可达主机的情况下修改生产。

### 3.1 SSH 只读定位结果

本轮使用配置中的 SSH 端口 `12043` 进行只读核验，未执行写入、重启或迁移：

- 主机：`ecm-9baa`，SSH 可达；
- Nginx：运行中，外部 `10000` 转发到后端 `127.0.0.1:10001`；
- 服务：`labelu.service` 运行中，`ActiveEnterTimestamp=2026-09-17 22:28:21 CST`；
- 工作目录：`/data/labeIU/labeIuMedia`；
- 当前服务实际加载源码：`/data/labeIU/labeIuproject/labeIU`；
- 该远端源码目录没有当前本地新增的 `labelu/internal/adapter/routers/health.py`，后端直连三个探针均为 JSON 404；
- 远端源码提交为 `146610b3`，工作树存在历史未提交变更，不能直接覆盖或强制清理。

因此，生产缺口已经从“网络不可达”收敛为“当前代码尚未按正式提交流程切换到运行服务”。

## 4. 下一依赖

1. 使用当前工作树构建产物部署到预生产；
2. 先验证 Nginx `/healthz`、`/readyz`、`/version`、`/openapi.json` 和 `/api-docs/openapi.json`；
3. 使用同一 `staging_acceptance.py` 复验一期路由和退休路径；
4. 再用标注人员、管理人员账号执行浏览器验收；
5. 用户明确“提交代码”后，才进入提交、推送、部署和生产复验。

本轮未修改业务代码、未连接生产数据库、未执行生产写入、未提交、未推送、未部署。
