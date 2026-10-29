# RUN-0274 生产只读路由与旧写入口复核（2026-09-20）

## 范围

R03/R04/R09/R10 对生产 `http://113.141.72.175:10000` 只发送 `GET` 请求，核对当前 OpenAPI 和一期主链路由。没有登录、没有创建任务、没有上传、没有调用 POST/PUT/PATCH/DELETE，也没有查询或修改生产数据库。

## 结果

| 检查项 | 结果 |
|---|---|
| `/openapi.json` | `200`，JSON，OpenAPI `3.1.0`，标题“LabelU 中文 API 接口文档”，版本 `1.4.5` |
| `/api/v1/algorithms` | GET `404`，OpenAPI 不存在 |
| `/api/v1/datasets` | GET `404`，OpenAPI 不存在 |
| `/api/v1/model-versions` | GET `404`，OpenAPI 不存在 |
| `/api/v1/workspace/summary` | GET `404`，OpenAPI 不存在 |
| `/api/v1/edge-device-jobs` | GET `404`；但 OpenAPI 仍登记 `POST` 写方法 |
| `/api/v1/yolo-bm1688-exports` | OpenAPI 仍存在 GET |

OpenAPI 约有 160 个路径；本次只读取目标路径和方法元数据。旧 `edge-device-jobs` 写入口仍被文档暴露，不能因为 GET 404 就宣称退休写接口安全；必须由部署版本核对和运维日志/数据库审计确认，禁止再次向生产发送探针 POST。

## 结论

- 生产仍是旧部署，未加载当前无项目菜单/API、迁移闸门、制品清单摘要校验和只读探针修复。
- 生产验收等级保持失败关闭；不能进行正式迁移、Worker 开关、BM1688/AICM/S3 业务回放或生产发布。
- 下一依赖：用户明确“提交代码”后，才能提交、推送、备份、部署；部署后先做只读 OpenAPI/退休写入口复核，再按两类账号执行业务验收。

