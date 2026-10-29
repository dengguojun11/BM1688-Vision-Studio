# RUN-0218 生产只读路由与探针复核

## 1. 范围和安全边界

本轮只访问生产 `http://113.141.72.175:10000` 的登录接口和 GET 接口，使用管理人员账号完成身份验证；没有创建任务、修改设置、上传文件、触发训练、发布模型、修改 S3 或操作设备。登录仅用于取得只读查询令牌，令牌不写入文件、不出现在证据中。

## 2. 生产实测结果

### 2.1 根探针

| 路径 | 实测结果 | 结论 |
|---|---:|---|
| `/openapi.json` | `200 application/json`，约 881 KB | OpenAPI JSON 可读取 |
| `/healthz` | `200 text/html` | 仍被 SPA 前端接管，不是健康 JSON |
| `/readyz` | `200 text/html` | 仍被 SPA 前端接管，不是就绪 JSON |
| `/version` | `200 text/html` | 仍被 SPA 前端接管，不是版本 JSON |

### 2.2 管理人员只读查询

| 路径 | 实测结果 | 线上含义 |
|---|---:|---|
| `/api/v1/users/me` | `200` | 管理人员账号可登录，`id=1`、`is_task_superuser=true` |
| `/api/v1/tasks` | `200`，`total=128` | 线上已有任务数据 |
| `/api/v1/yolo-training-tasks` | `200`，`total=63` | 训练任务路由已部署 |
| `/api/v1/yolo-bm1688-exports` | `200`，`total=66` | BM1688 编译记录已部署 |
| `/api/v1/yolo-edge-validation/jobs` | `200`，`total=96` | 边端验证记录已部署 |
| `/api/v1/published-models` | `200`，`total=17` | 发布模型目录已部署 |
| `/api/v1/public/models` | `200`，`total=17` | 对外模型目录可读取 |
| `/api/v1/strategy-integration-s3-config` | `200` | S3 配置页面接口可读取 |
| `/api/v1/edge-devices` | `200` | 设备注册/查询接口可读取，需后续核对是否属于一期保留边界 |
| `/api/v1/training-evaluation-datasets` | `200` | 评估数据集接口已部署 |
| `/api/v1/yolo-imported-models` | `200`，`total=3` | 导入模型目录已部署 |
| `/api/v1/yolo-trained-models` | `200`，`total=68` | 训练模型目录已部署 |
| `/api/v1/auto-training-settings` | `200` | 自动训练设置可读取，返回 `schema_ready=false`、`outbox_enabled=false`、`pool_enabled=false` 等状态 |

### 2.3 生产缺失或异常路由

| 路径 | 实测结果 | 阻断原因 |
|---|---:|---|
| `/api/v1/datasets` | `404` | 一期数据集主链路不能按方案验收 |
| `/api/v1/model-versions` | `404` | 模型版本血缘主链路不能按方案验收 |
| `/api/v1/algorithms` | `404` | 算法目录和兼容筛选不能按方案验收 |
| `/api/v1/workspace/summary` | `404` | 工作台汇总接口不能按方案验收 |
| `/api/v1/strategy-integration-s3-config/connections/active` | `404` | 生产实际使用的 S3 配置接口与方案候选路径不一致，需按现有路由对账 |
| `/api/v1/edge-device-jobs` | `404` | 旧边端作业入口当前未暴露，符合退休路径预期；仍需 OpenAPI 与部署探针一致性复核 |

## 3. OpenAPI 与实际 HTTP 的差异

生产 `/openapi.json` 仍列出了 `/api/v1/edge-device-jobs`、`/api/v1/edge-devices` 等路径；其中旧作业入口实际 GET 为 `404`，设备注册接口实际为 `200`。因此不能只看 OpenAPI 判断一期控制面是否启用，必须同时保留真实 HTTP 结果和退休写入口探针。

按当前 `scripts/ops/7gai_openapi_contract.json` 对生产 OpenAPI 做只读集合对账：生产共暴露 `295` 条路径，必需路径缺少 `/api/v1/datasets`、`/api/v1/model-versions`、`/api/v1/algorithms`、`/api/v1/workspace/summary`；退休前缀仍列出 `/api/v1/edge-device-jobs`、`/api/v1/edge-device-jobs/claim` 和 `/api/v1/edge-device-jobs/{job_id}/result`。这进一步证明生产 OpenAPI 与当前一期契约未对齐。

## 4. 验收等级与处理结论

本轮是 **生产 L2 只读证据**，不是生产放行：

- 已证明：线上管理人员登录、现有任务/训练/BM1688/边端/模型目录/S3 配置可以读取；
- 未证明：数据集、模型版本、算法目录和工作台新主链已部署；健康探针 JSON 正确；S3 自动回流、AICM 对账、BM1688 真机和并发门禁已通过；
- 本轮未修改生产数据，未触发任何训练、编译、发布、回流或设备操作。

下一步必须先完成当前代码的提交授权和部署，再重新执行相同只读矩阵；生产部署前不得打开自动训练、S3 回流、BM1688 Worker 或边端验证 Worker。
