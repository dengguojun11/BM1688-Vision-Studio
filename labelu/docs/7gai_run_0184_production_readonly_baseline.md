# RUN-0184 生产只读基线与部署差距复核

**日期**：2026-09-20（Asia/Shanghai）  
**环境级别**：生产只读；未写入、未提交、未部署  
**责任角色**：R01 产品、R03 架构、R04 后端、R08 前端/UI、R09 测试、R10 运维  
**对应工作包**：WP0 主链与菜单、WP1 API、WP5 生产验收

## 1. 核验范围

目标地址：`http://113.141.72.175:10000`。本轮只读取探针和 OpenAPI，不登录、不创建任务、
不上传图片、不启动训练、不编译模型、不操作 BM1688、不写 S3/AICM、不修改下载开关。

## 2. 实测结果

| 地址 | 结果 | 结论 |
|---|---|---|
| `/openapi.json` | HTTP 200，`application/json`，长度约 742 KB | 当前生产后端 OpenAPI 真源 |
| `/healthz` | HTTP 200，`text/html` | 被 SPA 回退，不能作为 JSON 健康证据 |
| `/readyz` | HTTP 200，`text/html` | 被 SPA 回退，不能作为 JSON 就绪证据 |
| `/version` | HTTP 200，`text/html` | 被 SPA 回退，不能作为 JSON 版本证据 |
| `/api-docs/openapi.json` | HTTP 200，`text/html` | 被 SPA 回退，不能作为 OpenAPI 真源 |

生产 OpenAPI 仍包含：`/api/v1/edge-device-jobs`、`/claim`、`/{job_id}/result`，这三条旧设备作业
路径与一期无设备发布主链不一致。生产已有任务、训练、发布模型目录、BM1688 导出、S3 配置和多种
预标注路径；但 `/api/v1/datasets`、`/api/v1/algorithms`、`/api/v1/model-versions`、
`/api/v1/workspace/summary` 未出现在当前生产基线中，不能用本地路由替代线上证据。

## 3. 放行结论

当前生产验收：**不通过**。原因是服务探针内容类型错误、旧设备控制路径残留、目标主链 API 未部署，
且本轮没有执行生产写入。生产不能标记为无项目、无设备发布和完整模型血缘目标版本。

## 4. 部署后必须复核

1. `/healthz`、`/readyz`、`/version` 返回 JSON，且 Nginx 不再把精确探针路径交给 SPA。
2. `/openapi.json` 与本地目标路由一致；旧 `edge-device-jobs` 写/领取/结果路径返回退休门禁或不存在。
3. 管理人员和标注人员分别验证任务、预标注、人工确认、数据冻结、训练、编译、真机验证、发布和下载控制。
4. 真实 S3 回流包核对 `model_set_id`、去重、任务复用、预标注和 Worker 重启接管。
5. 真实 MySQL 迁移、TPU-MLIR、GPU、BM1688、AICM 和 systemd 证据分别归档，不用 HTTP 200 替代。

## 5. 状态

本轮达到生产只读基线记录，不代表生产功能通过；未提交、未推送、未部署。只有用户明确“提交代码”后，
才能按迁移闸门、备份、部署和线上回归流程推进。

