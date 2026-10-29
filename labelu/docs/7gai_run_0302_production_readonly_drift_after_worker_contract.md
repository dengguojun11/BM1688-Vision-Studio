# RUN-0302 生产只读现状与本地 Worker 契约漂移记录

## 1. 本轮目的

按 R03 架构、R04 后端、R09 测试、R10 运维的生产核验职责，对线上
`http://113.141.72.175:10000` 做 GET-only 复核，并把线上版本与当前本地
Worker 改造的差异记录下来。本轮不登录、不提交业务请求、不写生产数据库，
也不启动任何训练、预标注、编译、边端或回流 Worker。

## 2. 生产只读结果

| 路径 | 结果 | 说明 |
|---|---:|---|
| `/health` | 200 | 返回前端 SPA HTML，不是后端 JSON 健康证明 |
| `/openapi.json` | 200 | 返回 JSON，线上 OpenAPI 共 295 条路径 |
| `/api/v1/health` | 404 | 当前线上没有该后端健康路径 |
| `/api/v1/tasks` | 401 | 未认证访问被拒绝 |
| `/api/v1/published-models` | 401 | 未认证访问被拒绝 |
| `/api/v1/tasks/1/yolo_preannotate_job/active` | 401 | 未认证访问被拒绝 |
| `/api/v1/integrations/strategy-tasks` | 404 | 旧路径不存在 |
| `/internal/v1/experiment-datasets` | 200 | 未认证即可返回真实数据，和当前本地内部服务鉴权契约不一致 |
| `/api/v1/strategy-integration-s3-config` | 401 | 未认证访问被拒绝 |

线上 OpenAPI 已登记一期的实验数据集、YOLO 预标注、发布模型和 S3 配置
路由，但仍保留以下历史边端写路径：

```text
/api/v1/edge-device-jobs
/api/v1/edge-device-jobs/claim
/api/v1/edge-device-jobs/{job_id}/result
```

因此“OpenAPI 有路由”“返回 200”不能作为生产完成依据。尤其是
`/internal/v1/experiment-datasets` 未认证可读，必须在部署前确认生产版本、
反向代理和内部服务令牌配置，不能直接把当前本地鉴权代码当成线上已生效。

## 3. 与本地角色工作包的关系

当前本地已完成或正在收口：

- `RUN-0298`：自动样本预标注独立 Worker；
- `RUN-0299`：手工 YOLO 批量预标注独立 Worker；
- `RUN-0300`：TagTool、LocateAnything、VLX-Seek、Rex-Omni 统一预标注 Worker（进行中）；
- 训练、BM1688 编译、真机验证、AICM 发布和第三方 S3 回流仍需分别在同一套
  MySQL、systemd、真实工具链和真实设备上形成 L2/L3 证据。

这些本地改造全部保持生产开关关闭；本记录不授权提交、推送或部署。

## 4. 验收结论

- 本轮生产只读证据：L0，完成现状确认但不代表功能放行。
- 本地 Worker 代码：按各自 RUN 文档单独评为 L1，不能由本轮生产探针升级等级。
- 生产放行前置：用户明确“提交代码”后，先备份和同版本 MySQL 迁移演练，
  再部署；部署后用标注人员和管理人员账号回放菜单、预标注、训练、模型下载
  禁止、S3 回流异常和审计链路。

## 5. 交付状态

本轮只有 GET-only 证据和文档记录；未改生产、未提交、未推送、未部署。
