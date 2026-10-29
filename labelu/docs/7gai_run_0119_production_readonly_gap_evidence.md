# RUN-0119 生产只读验收差距证据

## 1. 核验范围

对生产地址 `http://113.141.72.175:10000` 执行新的无项目只读验收脚本。只读取健康探针、OpenAPI、登录、当前用户、任务、数据集、训练、模型、BM1688、边端验证、已发布模型目录和第三方 S3 配置；没有创建任务、上传文件、启动训练、编译模型、执行真机验证、修改下载开关或写入任何生产数据。

证据文件：`.tmp/evidence/run0119-production-admin-readonly.json`。

证据 SHA256：`578830BA91720F1013B06B3E89B15D529131BF812A4CB3AFC853062C350EB6E1`。

## 2. 生产实测结果

| 核验项 | 结果 | 说明 |
|---|---|---|
| `/healthz` | 通过，HTTP 200 | 响应约 4.7 秒 |
| `/readyz` | 通过，HTTP 200 | 响应约 4.6 秒 |
| `/version` | 通过，HTTP 200 | 响应约 4.6 秒 |
| `/openapi.json` | 通过，HTTP 200 | `application/json`，是真实后端 JSON |
| `/api-docs/openapi.json` | 不可作为生产 OpenAPI 真源 | HTTP 200 但返回前端 HTML；验收脚本已改用 `/openapi.json` |
| 未登录 `/api/v1/tasks` | 通过，HTTP 401 | 未认证请求被拒绝 |
| 管理员登录 | 通过，HTTP 200 | 返回令牌并能访问当前用户 |
| 任务 | 通过，HTTP 200 | `/api/v1/tasks` |
| 数据集 | 失败，HTTP 404 | `/api/v1/datasets` 不存在 |
| 算法目录 | 失败，HTTP 404 | `/api/v1/algorithms` 不存在 |
| 训练作业 | 通过，HTTP 200 | `/api/v1/yolo-train-jobs` |
| 模型版本 | 失败，HTTP 404 | `/api/v1/model-versions` 不存在 |
| 已发布模型目录 | 通过，HTTP 200 | `/api/v1/published-models` |
| BM1688 编译作业 | 通过，HTTP 200 | `/api/v1/yolo-bm1688-exports` |
| 边端验证作业 | 通过，HTTP 200 | `/api/v1/yolo-edge-validation/jobs` |
| 第三方 S3 配置 | 通过，HTTP 200 | `/api/v1/strategy-integration-s3-config` |
| 工作台汇总 | 失败，HTTP 404 | `/api/v1/workspace/summary` 不存在 |

## 3. 已退休路径残留

生产 `/openapi.json` 仍暴露以下一期不应开放的路径：

- `/api/v1/edge-device-jobs`；
- `/api/v1/edge-device-jobs/claim`；
- `/api/v1/edge-device-jobs/{job_id}/result`。

这说明当前生产部署仍是旧版本或未完成一期路由切换，不能把生产标记为无项目、无设备发布控制面的目标版本。

## 4. 结论与处理优先级

生产只读验收结论：**失败，不能放行**。

P0：部署包含数据集、算法目录、模型版本和工作台路由；移除或隐藏 `edge-device-jobs` 旧路径；确认 Nginx 将 `/openapi.json`、`/docs` 和前端静态路由分流正确。

P1：重新执行管理员和标注人员两类账号的任务可见性、预标注、人工确认、训练、BM1688 编译/验证、发布目录和下载禁止探针。

P2：在真实 S3、AICM、TPU-MLIR 和 BM1688 设备上补齐端到端证据；当前 HTTP 200 只证明单接口存在，不证明业务闭环。

## 5. 角色交付和状态

| 角色 | 工作包 | 结果 |
|---|---|---|
| R03 架构 | WP0/P5 | 确认生产 OpenAPI 真源为 `/openapi.json`，旧控制面残留为阻断 |
| R04 后端 | WP1/WP2/WP3 | 生产新路由缺失，需部署当前代码并做 API 对账 |
| R09 测试 | WP5 | 生成 JSON 证据并按必需路径判定失败 |
| R10 运维 | P5 | 确认 Nginx/API 分流和版本部署差距，未执行修复部署 |
| R01 产品 | WP0 | 生产尚未达到无项目主流程，放行结论为失败 |

本轮未修改生产、未提交、未推送、未部署。工作树代码只完成验收工具修正；生产差距必须在用户明确“提交代码”后，按迁移、部署和回归流程处理。
