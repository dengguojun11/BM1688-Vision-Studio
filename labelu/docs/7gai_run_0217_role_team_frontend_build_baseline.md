# RUN-0217 角色团队本地交付与前端构建基线

## 1. 目标与范围

本轮按 R01-R10 角色执行手册复核一期主链的本地交付状态，补齐前端类型检查和生产构建证据，并把“每轮产出必须有独立文档和台账记录”固化为持续交付要求。

涉及菜单和按钮：任务与标注、训练中心、模型中心、进化中心和平台设置中的一期按钮；模型中心包含模型版本、边端编译、真机验证、发布记录和下载目录。

## 2. 角色分工与本轮结果

| 角色 | 本轮交付 | 验收结果 |
|---|---|---|
| R01 产品 | 核对两类角色、无项目维度、标注人员可创建任务/训练/发布模型 | 本地契约通过 |
| R02 数据契约/体验 | 核对标签契约、冻结数据版本、中文状态和普通矩形框边界 | 本地契约已纳入回归 |
| R03 架构/迁移 | 核对租约、fencing、失败关闭和正式迁移边界 | 本地静态/专项通过；真实 MySQL 未验收 |
| R04 后端 | 核对任务、训练、编译、边端、发布、回流接口的权限/幂等/失败关闭 | 内部全量回归通过 |
| R05 外部平台 | 保持 S3 自动回流和 AICM `model_set_id` 对账接口边界 | 真实 S3/AICM 未接入验证 |
| R06 模型算法 | 固定 PT -> ONNX -> TPU-MLIR -> FP32/FP16/INT8 BModel -> BM1688 验证顺序 | 本地规则通过；真实工具链未验收 |
| R07 BM1688 边端 | 核对签名清单、三精度加载和推理门禁 | 本地 Runner 通过；真实设备未验收 |
| R08 前端/UI | 菜单、中文按钮、居中弹窗、权限隐藏和窄屏布局可构建 | 类型检查通过；构建通过 |
| R09 测试 | 执行仓库级/内部全量回归并记录失败关闭、并发和迁移专项 | 仓库级 `2627 passed, 56 skipped, 410 warnings`；内部 `2283 passed, 25 skipped, 0 failed` |
| R10 运维/生产 | 复核生产部署前证据清单和探针缺口 | 生产仍为旧部署，未放行 |

## 3. 验证命令与证据

```text
python -m pytest labelu/tests/internal -q --disable-warnings --tb=short
2283 passed, 25 skipped, 0 failed

python -m pytest labelu/tests -q --disable-warnings --tb=short
2627 passed, 56 skipped, 410 warnings in 393.83s

pnpm --filter @labelu/frontend type-check
通过

pnpm --filter @labelu/frontend build
退出码 0；4094 个模块转换；built in 29.01s
```

后端结果使用进程级新鲜 SQLite 测试库，不能替代生产 MySQL。仓库级回归退出码为 `0`，其中 `56` 项为既有跳过契约。前端构建日志中的 Ant Design `use client`、`eval` 和大包体内容是现有依赖/构建警告，不作为本轮失败；后续前端性能需按真实浏览器指标单独验收。

## 4. 并发、幂等和失败关闭

- 测试库和媒体目录按进程隔离，避免并行测试互相污染；
- 训练、编译、边端验证、回流和发布继续使用租约/fencing，旧执行者不能覆盖新状态；
- 旧项目、旧规则中心、旧 `edge-device-jobs` 和人工计划写入口保持一期失败关闭；
- 生产开关、自动 Worker、外部 S3/AICM、TPU-MLIR 和 BM1688 真机在真实证据前保持关闭。

## 5. 环境边界与未完成项

本轮没有连接或写入生产，也没有提交、推送或部署。以下证据仍是放行阻断：

1. 同版本 MySQL 迁移、备份恢复、`2/10/20/50` 并发、锁等待和 Worker 重启接管；
2. 第三方生产 S3 自动接收、去重、按 AICM `model_set_id` 聚合和异常重放；
3. AICM 发布、本平台模型目录下载和问题模型禁止下载；
4. TPU-MLIR 实际编译与三精度 BModel 产物；
5. BM1688 真机加载、推理、P95、短稳、断网和资源释放；
6. 标注人员/管理人员生产浏览器菜单、弹窗、权限和发布回放；
7. 部署后的 `/healthz`、`/readyz`、`/version` JSON 探针和退休路由核验。

## 6. 验收等级与下一步

本轮达到 **L1（本地代码与专项测试通过）**。不能升级为 L2/L3/L4，也不能宣称方案已在生产完成。

下一轮优先顺序：预生产 MySQL 证据门禁 -> 两类账号浏览器验收 -> S3/AICM 脱敏联调 -> TPU-MLIR/BM1688 真机验收 -> 部署后生产只读/小流量证据。

## 7. 本轮追加的 OpenAPI 契约修复与真实 HTTP 证据

前序验收脚本把 `/api/v1/workflows` 整个前缀误判为退休路径，导致实际仍被自动训练 Outbox 使用的三条内部操作被错误拦截。本轮统一修正了 `scripts/ops/7gai_openapi_contract.json`、`scripts/staging_acceptance.py` 和 `deploy.sh`：

- 保留 `/api/v1/workflows/outbox`；
- 保留 `/api/v1/workflows/outbox/{outbox_id}/retry`；
- 保留 `/api/v1/workflows/outbox/{outbox_id}/dispatch`；
- 普通工作流创建、查询、转移等路径仍按一期退休控制面处理。

新增测试覆盖“只允许 Outbox”“普通工作流仍失败”两种边界。

验证结果：

```text
python -m pytest labelu/tests/scripts/test_staging_acceptance.py -q --disable-warnings --tb=short
11 passed

python -m pytest labelu/tests/internal/adapter/routers/test_workflow.py -q --disable-warnings --tb=short
5 passed

python scripts/staging_acceptance.py --base-url http://127.0.0.1:9889 --username admin@123.com --password Admin@123 --evidence .tmp/7gai_local_acceptance2/staging_evidence_run0217.json
全部必需探针通过，退出码 0
```

该 HTTP 证据来自隔离本地服务和本地 SQLite，不代表生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688 真机已验收。
