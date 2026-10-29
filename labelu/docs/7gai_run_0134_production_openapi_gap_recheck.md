# RUN-0134 生产 OpenAPI 与健康入口只读复核

## 1. 核验范围

核验时间：2026-09-19（Asia/Shanghai）。

目标地址：`http://113.141.72.175:10000`。

本轮只读取 HTTP 响应和 OpenAPI 文档，不登录、不写数据库、不调用业务写接口、不修改生产配置。

## 2. 生产实测结果

| 检查项 | 实测结果 | 结论 |
|---|---|---|
| `/openapi.json` | HTTP 200，`application/json`，约 881747 字节 | 当前运行版本确实暴露真实 OpenAPI |
| OpenAPI 路由总数 | 295 条 | 以运行版本为准 |
| `/healthz` | HTTP 200，但返回 `text/html` 前端页面 | 不是健康接口证据 |
| `/readyz` | HTTP 200，但返回 `text/html` 前端页面 | 不是就绪接口证据 |
| `/version` | HTTP 200，但返回 `text/html` 前端页面 | 不是版本接口证据 |
| `/api-docs/openapi.json` | HTTP 200，但返回 `text/html` 前端页面 | 不能作为 OpenAPI 真源 |
| 生产 22 端口 | 不可达 | 本轮无法进行 SSH 主机验收 |
| 生产 10000 端口 | 可达 | HTTP 只读核验可执行 |

## 3. 方案主链路由差距

生产当前缺少：

- `/api/v1/datasets`
- `/api/v1/algorithms`
- `/api/v1/model-versions`
- `/api/v1/workspace/summary`

生产当前仍暴露退休控制面：

- `/api/v1/edge-device-jobs`
- `/api/v1/edge-device-jobs/claim`
- `/api/v1/edge-device-jobs/{job_id}/result`

生产已存在并可在 OpenAPI 中查到：

- `/api/v1/strategy-integration-s3-config`
- `/internal/v1/experiment-datasets`
- `/internal/v1/strategy-evolution/model-trainings`

## 4. 影响判断

- 本地已有规范路由和退休路径门禁代码，但当前生产 OpenAPI 仍未达到主方案要求，不能宣布生产菜单/API闭环完成。
- 由于健康入口被 SPA 回退，HTTP 200 不能证明服务健康；部署验收必须直接读取 `/openapi.json` 并检查必需/退休路径。
- 22 端口不可达意味着本轮不能执行 Ubuntu、systemd、MySQL、Worker、GPU 或 BM1688 主机级验收；不得用本地测试替代这些证据。
- 训练、BM1688、边端 Worker 的生产开关继续保持关闭。

## 5. 下一依赖

1. 由具备生产主机权限的部署轮次发布当前本地代码和路由契约。
2. 发布前执行数据库备份、迁移演练和 OpenAPI 路径门禁。
3. 发布后重新读取 `/openapi.json`，确认 4 条规范路径出现、3 条退休控制面消失，并修复健康/就绪/版本入口的反向代理回退。
4. 只有上述证据完成后，才进入同版本 MySQL、systemd、GPU、TPU-MLIR、BM1688、S3 和 AICM 验收。

## 6. 提交与部署状态

本轮仅生产只读核验，未提交、未推送、未部署，未修改生产数据。
