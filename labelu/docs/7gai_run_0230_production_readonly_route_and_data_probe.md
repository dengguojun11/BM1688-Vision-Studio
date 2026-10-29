# RUN-0230 生产只读路由与业务数据探针

## 1. 目标

重新核验生产环境当前实际部署版本、OpenAPI 路由、两类账号入口和关键业务数据，避免把本地测试结果或旧快照当成生产完成证据。本轮只读，不创建任务、不修改配置、不发布模型、不下载制品。

## 2. 角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品 | WP0 | 按菜单闭环核对任务、训练、模型中心、进化中心和管理入口是否已注册 |
| R03 架构 | WP1/WP2 | 对照 OpenAPI 核验一期路由和健康探针边界 |
| R04 后端 | WP2/WP3 | 只读查询真实业务列表和发布目录，核对响应结构 |
| R05 外部平台 | WP3 | 核对生产 S3 配置可读，不触发对象读取或回流写入 |
| R06/R07 算法与边端 | WP3 | 核对训练、BM1688 编译、边端验证数量和发布目录状态 |
| R09 测试 | WP5 | 保存 HTTP 状态、版本、数量和开关证据 |
| R10 运维 | OPS | 核对健康探针和部署路由，不执行重启、迁移或配置修改 |

## 3. 只读验证结果

生产地址：`http://113.141.72.175:10000`；OpenAPI 版本：`1.4.5`；已用管理员账号完成只读登录，账号响应为 `is_task_superuser=true`。

| 项目 | 生产实测结果 |
|---|---:|
| 任务 | 128 |
| YOLO 训练任务 | 63 |
| BM1688 编译记录 | 66，摘要显示 `blocked=22`、`publish_ready=37`、`failed=9` |
| 边端验证任务 | 96 |
| 发布模型目录 | 17，`active=17`、`public=17`、`unavailable=0` |
| 训练模型目录 | 68，`training_passed=61`、`training_failed=7` |
| 外部导入模型 | 3 |
| S3 配置 | 可读，存在活动连接 `env_default` |

OpenAPI 已注册任务、训练、模型、BM1688、边端验证、发布模型、第三方 S3 和自动训练路由；`/api/v1` 业务路径可访问并返回结构化 JSON。

与仓库的 `scripts/ops/7gai_openapi_contract.json` 对账结果：缺少必需路径 `/api/v1/datasets`、`/api/v1/algorithms`、`/api/v1/model-versions`、`/api/v1/workspace/summary`；同时仍暴露已退休的 `/api/v1/edge-device-jobs`（含领取和结果回传路径）。因此当前线上 OpenAPI 不能作为一期目标版本放行证据，说明生产仍未切换到本地目标路由集合。

对照证据：当前本地 `labelu.main.app.openapi()` 共 347 条路径，必需路径全部存在且退休路径为空；生产 OpenAPI 对账失败，结论是生产部署版本落后于当前工作树，而不是本地路由契约本身缺失。

只读验收脚本证据：`.tmp/production-readonly-route-probe-20260920.json`。脚本未提供密码，不执行登录和任何写操作；结果为健康探针 JSON 失败、OpenAPI 对账失败、未授权 API 正确返回 `401`。

## 4. 生产阻断与风险

- `/healthz`、`/readyz`、`/version` 当前均返回前端 HTML（HTTP 200），不能作为 JSON 健康/版本探针；需要先修正 Nginx 或应用路由，再进行部署验收。
- OpenAPI 路由对账不通过：缺少 4 条一期必需路径，并保留退休的边端作业控制面；不能用历史路由数量代替目标版本验收。
- `/api/v1/auto-training-settings` 返回 `schema_ready=false`、`outbox_enabled=false`、`pool_enabled=false`、`scheduler_enabled=false`、`train_enabled=false`、`bm1688_enabled=false`、`edge_validation_enabled=false`；自动进化链路尚未生产放行。
- 本轮没有证明真实 MySQL 迁移、备份恢复、`2/10/20/50` 并发领取、S3 问题包自动回流、TPU-MLIR 三精度编译、BM1688 真机推理或 Worker 重启接管。
- 生产统计只能证明接口和历史数据可读，不能替代端到端业务验收。

## 5. 状态

本轮达到生产只读 L2 证据；未提交、未推送、未部署，未修改生产数据。下一依赖是用户明确授权提交后，按 RUN-0229 的迁移/备份/并发闸门执行预生产或生产验证。
