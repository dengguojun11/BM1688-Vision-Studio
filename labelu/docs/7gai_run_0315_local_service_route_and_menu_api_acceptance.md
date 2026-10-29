# RUN-0315 隔离本地服务路由与菜单 API 验收（2026-09-20）

## 1. 目标和范围

由 R01 产品、R04 后端、R08 前端、R09 测试和 R10 运维在隔离本地 SQLite 服务中验证：健康探针、OpenAPI、两类主链路入口以及管理人员可见的菜单 API 是否与《7GAI 进化平台菜单级产品与业务闭环方案》一致。

本轮服务使用 `.tmp/local0315` 独立数据目录和 `127.0.0.1:9899`，关闭后台 Watcher/训练开关；只执行登录和 GET 查询，未连接生产、S3、AICM、GPU、TPU-MLIR 或 BM1688。

## 2. 执行过程

1. 启动：`python -m labelu.main dev --host 127.0.0.1 --port 9899 --static-only --skip-install --media-host http://127.0.0.1:9899`。
2. 未登录只读审计：

   ```text
   python scripts/staging_acceptance.py --readonly-route-audit --base-url http://127.0.0.1:9899 --timeout 10 --evidence .tmp/evidence/run0315/local-service-route-audit.json
   ```

3. 管理账号只读主链路：

   ```text
   python scripts/staging_acceptance.py --base-url http://127.0.0.1:9899 --username admin@123.com --password Admin@123 --timeout 10 --evidence .tmp/evidence/run0315/local-admin-readonly.json
   ```

4. 验证完成后发送 Ctrl+C 停止服务，进程退出码为 1（由终端中断），没有遗留监听服务。

## 3. 结果

| 验收层 | 证据 | 结果 |
|---|---|---|
| 未登录路由/OpenAPI | `.tmp/evidence/run0315/local-service-route-audit.json` | 16 项通过；健康探针均为 JSON；退休 `edge-device-jobs` 返回 404；OpenAPI 契约通过 |
| 管理人员菜单 API | `.tmp/evidence/run0315/local-admin-readonly.json` | 20 项通过；登录、当前用户和主链路 GET 全部通过 |
| 数据库隔离 | `BASE_DATA_DIR=.tmp/local0315` | 使用隔离 SQLite，未接生产库 |
| 后台任务 | `LABELU_DISABLE_BACKGROUND_WATCHDOGS=true`、`YOLO_TRAIN_ENABLED=false` | 未启动训练、编译、边端、回流 Worker |

管理账号 GET 覆盖：任务、数据集、算法目录、训练作业、模型版本、已发布模型目录、BM1688 编译作业、边端验证作业、第三方 S3 配置和工作台汇总。

## 4. 结论

- 当前工作树后端和菜单主链路在隔离本地服务中可以启动并通过认证只读验收。
- `RUN-0314` 发现的生产缺口是旧部署/反向代理版本漂移；本地代码并非缺少这些路由。
- 本轮只证明 L1 本地服务/API 级可用，不证明生产部署、两类真实账号、浏览器点击、MySQL、AICM、S3、GPU、TPU-MLIR 或 BM1688 已通过。

## 5. 状态

- 代码变更：无业务代码变更，仅生成本地证据文件。
- 数据库/生产：无生产写入、无迁移、无部署。
- 本轮等级：**L1 本地服务/API 验收**。
- 下一依赖：在用户授权提交后，先部署到隔离预生产，使用同一脚本完成 JSON 探针、OpenAPI、两类账号和浏览器验收，再申请生产。
