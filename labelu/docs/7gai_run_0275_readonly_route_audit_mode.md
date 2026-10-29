# RUN-0275 生产只读路由审计模式与旧部署证据（2026-09-20）

## 1. 目标与范围

本轮为生产验收工具增加“只读路由审计”模式，用同一份 `7gai_openapi_contract.json` 核对生产 OpenAPI 和一期路由。该模式只发送 `GET`，不登录、不发送请求体、不调用任何 `POST/PUT/PATCH/DELETE`，也不执行退休边端写接口探针。

涉及菜单和按钮：工作台、任务与标注、训练中心、模型中心、进化中心、系统管理的路由可达性；不执行菜单内创建、上传、训练、发布、回流或下载禁用操作。

主责角色：R03 架构、R04 后端工具、R09 测试、R10 运维。协作角色：R01 产品负责验收范围，R05/R06/R07 负责外部平台、算法和 BM1688 路由边界确认。

## 2. 代码与接口交付

修改文件：

- `scripts/staging_acceptance.py`：新增 `--readonly-route-audit`；固定 GET 方法白名单；不登录、不带请求体；单独记录 OpenAPI 契约、目标路由状态、响应类型和退休写探针执行标记；与 `--mutating`、`--allow-retired-write-probes` 组合时失败关闭。
- `labelu/tests/scripts/test_staging_acceptance.py`：覆盖只读模式的 GET-only、无登录、无请求体、OpenAPI 漂移失败和危险参数互斥。

没有新增数据库表、迁移、业务路由或生产配置；没有修改任务、训练、模型、回流的业务数据。

## 3. 并发与失败关闭

- 审计工具本身不创建租约、不领取任务、不写入 outbox，避免验收请求与 Worker 形成数据库锁竞争。
- OpenAPI 缺少一期必需路径或仍暴露退休路径时整体失败关闭；HTTP `200` 的 SPA HTML 不被当作健康 JSON 通过。
- 生产旧部署即使把退休写入口返回 `404`，只要 OpenAPI 仍登记该写路径，仍判定部署不合格；不得再次用 POST 验证。

## 4. 本地验证

```text
python -m pytest labelu/tests/scripts/test_staging_acceptance.py -q --disable-warnings --tb=short
15 passed, 113 warnings

python -m compileall -q scripts/staging_acceptance.py
通过
```

## 5. 生产只读证据

执行命令：

```text
python scripts/staging_acceptance.py --base-url http://113.141.72.175:10000 --readonly-route-audit --evidence .tmp/7gai-production-readonly-route-audit-0275.json
```

证据文件：`.tmp/7gai-production-readonly-route-audit-0275.json`。

关键结果：

| 项目 | 结果 |
|---|---|
| 请求方法 | 全部 `GET` |
| 是否认证 | `false` |
| 是否执行退休写探针 | `false` |
| OpenAPI 路径数量 | `295` |
| `/api/v1/algorithms`、`/api/v1/datasets`、`/api/v1/model-versions`、`/api/v1/workspace/summary` | 生产 GET `404`，OpenAPI 缺失 |
| `/api/v1/edge-device-jobs` | GET `404`，但 OpenAPI 仍暴露 `POST`、`/claim`、`/result` |
| `/healthz`、`/readyz`、`/version` | HTTP `200`，响应为 SPA HTML，不是 JSON |
| 总体 | `passed=false`，失败关闭 |

## 6. 结论、风险与下一依赖

当前生产仍是旧部署，不能宣称一期六菜单、无项目 API、制品清单校验和只读探针修复已经上线。生产禁止继续发送旧边端写探针；需要在用户明确“提交代码”后，按既定流程提交、推送、备份、部署，再重复本审计并进行两类账号业务验收。

尚未完成且不能用本轮替代的证据：同版本 MySQL 沙箱和备份恢复、真实 YOLO11/TPU-MLIR、FP32/FP16/INT8 BModel、BM1688 真机、Board Agent 重启接管、AICM 发布、本平台下载禁用传播、第三方 S3 自动回流、预生产两类账号、容量与并发矩阵。

验收等级：本地 **L1**；生产 **失败关闭**。本轮未提交、未推送、未部署。
