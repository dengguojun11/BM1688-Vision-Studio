# RUN-0218 生产只读探针与部署阻断记录

## 1. 目标

由 R10 运维、R03 架构和 R09 测试对生产地址 `http://113.141.72.175:10000` 做只读核对，确认当前部署是否已经具备一期主链和退休控制面边界。本轮不登录、不写入业务数据、不执行迁移、不调用 S3/AICM/BM1688。

## 2. 只读证据

采集时间：2026-09-20（Asia/Shanghai）。

| 探针 | 结果 | 结论 |
|---|---|---|
| `/openapi.json` | HTTP 200，JSON，295 条路径 | OpenAPI 可读取，但不是当前本地一期契约 |
| `/healthz` | HTTP 200，网页文本 | 不是 JSON 健康探针 |
| `/readyz` | HTTP 200，网页文本 | 不是 JSON 就绪探针 |
| `/version` | HTTP 200，网页文本 | 不是 JSON 版本探针 |
| `/api/v1/tasks` | HTTP 401 | 认证边界存在 |
| `/api/v1/yolo-train-jobs` | HTTP 401 | 旧训练入口仍存在 |
| `/api/v1/yolo-bm1688-exports` | HTTP 401 | 编译入口仍存在 |
| `/api/v1/yolo-edge-validation/jobs` | HTTP 401 | 边端验证入口仍存在 |
| `/api/v1/published-models` | HTTP 401 | 发布模型目录仍存在 |
| `/api/v1/strategy-integration-s3-config` | HTTP 401 | 第三方 S3 配置入口仍存在 |
| `/api/v1/datasets` | HTTP 404 | 一期数据集入口未部署 |
| `/api/v1/algorithms` | HTTP 404 | 一期算法目录未部署 |
| `/api/v1/model-versions` | HTTP 404 | 一期模型版本入口未部署 |
| `/api/v1/workspace/summary` | HTTP 404 | 一期工作台汇总未部署 |
| `/api/v1/edge-device-jobs` | 未认证请求 HTTP 401；管理人员只读 GET 为 HTTP 404 | OpenAPI 仍列出历史路径，但实际旧入口未提供可用读取能力；退休写入口仍需部署后专门探针确认 |

对 `/openapi.json` 路径名做只读比对：一期必需的 `datasets`、`algorithms`、`model-versions`、`workspace/summary` 均缺失；旧 `edge-device-jobs` 仍被 OpenAPI 列出但管理人员 GET 实测 404；`/api/v1/workflows` 和 Outbox 路径当前未暴露。管理人员只读结果以 RUN-0218 生产路由复核记录为准。

## 3. 角色结论

| 角色 | 结论 |
|---|---|
| R03 架构 | 线上 OpenAPI 与本地一期契约不是同一版本，不能进入生产迁移或放量验收 |
| R09 测试 | 业务路由未登录返回 401 只能证明认证存在，不能证明一期功能已部署 |
| R10 运维 | 生产部署阻断；需先部署已授权版本，再重做 JSON 探针、主链路径、退休路径和两类账号验收 |

## 4. 放行条件

1. 获得本轮代码提交授权并完成部署；
2. 部署后 `/healthz`、`/readyz`、`/version` 返回 JSON；
3. `/openapi.json` 具备一期必需路径，Outbox 三条内部路径按契约保留；
4. 普通工作流、项目、组织、设备发布和旧边端作业写入口按一期规则退休；
5. 再执行管理人员和标注人员两类账号的页面与权限回放。

本轮未提交、未推送、未部署。该记录是生产阻断证据，不代表生产方案完成。
