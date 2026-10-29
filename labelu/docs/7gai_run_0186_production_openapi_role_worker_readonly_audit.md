# RUN-0186 生产 OpenAPI、本地主链、角色与 Worker 部署后核验清单

日期：2026-09-20  
状态：只读审计；本地 L1；未访问生产、未写数据、未提交、未推送、未部署

## 1. 审计边界

本轮只对照工作区已保存的生产 OpenAPI 证据（RUN-0134、RUN-0119、RUN-0174）和当前本地 `app.openapi()` 输出，不重新连接 `http://113.141.72.175:10000`。生产证据显示 `/openapi.json` 是唯一可解析的 OpenAPI 真源；`/healthz`、`/readyz`、`/version` 和 `/api-docs/openapi.json` 曾返回 SPA HTML，不能把 HTTP 200 当作健康或版本通过。

当前本地生成 OpenAPI 有 347 条路径；本地已经隐藏 `edge-device-jobs`、`board-agent` 和 `edge-agents` 等退休/设备控制面路径。生产快照仍有 295 条路径，仍暴露 `edge-device-jobs` 三条路径，且缺少数据集、算法、模型版本和工作台规范路由，说明部署后必须重新对账，不能用本地路径存在替代生产证据。

## 2. 部署后必须核验的路由

### 2.1 基础探针和文档

| 路径 | 预期 | 失败判定 |
|---|---|---|
| `/healthz` | HTTP 200、`application/json`、包含服务状态和时间信息 | 返回 SPA HTML、空正文或非 JSON |
| `/readyz` | HTTP 200、`application/json`、数据库/必要依赖就绪 | 200 HTML、依赖未就绪仍返回通过 |
| `/version` | HTTP 200、`application/json`、版本与构建信息 | HTML 或版本为空 |
| `/openapi.json` | HTTP 200、可解析 JSON | 不能解析或不是当前部署版本 |
| `/api-docs/openapi.json` | 应与文档分流策略一致；若保留必须 JSON | SPA 回退 HTML |

### 2.2 一期业务主链

部署后至少检查这些路径在 `/openapi.json` 出现，并用管理员和标注人员各做一遍允许/拒绝验证：

- 任务和样本：`/api/v1/tasks`、`/api/v1/tasks/{task_id}/samples`、任务协作者和预标注相关路径；
- 数据集冻结：`/api/v1/datasets`、`/api/v1/datasets/{dataset_id}/versions`、`/api/v1/dataset-versions/{version_id}/freeze`；
- 训练：`/api/v1/yolo-train-jobs`、`/api/v1/tasks/{task_id}/yolo-train-jobs`、训练作业详情/评估/下载路径；
- BM1688 编译：`/api/v1/yolo-bm1688-exports`、`/{export_id}/onnx`、`/{export_id}/bmodel`、`/{export_id}/verify`、`/{export_id}/board-validation`；
- 边端验证：`/api/v1/yolo-edge-validation/jobs`、`/{job_id}/run`、`/{job_id}/visual-evidence`；
- 模型目录：`/api/v1/published-models`、`/{identifier}`、`/{identifier}/download`、`/{identifier}/download-url`；模型被禁止下载后，下载和下载地址必须拒绝，但目录仍保留记录；
- 第三方 S3 配置：页面 `/strategy-integration-s3-config`，API `/api/v1/strategy-integration-s3-config` 及连接增删改和启用路径；
- 第三方回流：`/internal/v1/experiment-datasets`、`/created-tasks`、`/{package_id}`，以及兼容入口 `/api/v1/integrations/strategy-tasks`。

### 2.3 旧路径禁止继续作为主链

生产部署后必须确认以下路径不在 OpenAPI 中，或者访问时明确失败关闭，不得有 200/201/422 的写入成功：

- `/api/v1/edge-device-jobs`；
- `/api/v1/edge-device-jobs/claim`；
- `/api/v1/edge-device-jobs/{job_id}/result`。

这些路径不能替代 BM1688 编译/验证主链，也不能被旧前端或第三方回流调用。

## 3. 两类用户角色核验

系统只有标注人员和管理人员。服务账号、S3 第三方签名和 Board Agent 令牌属于机器身份，不是第三类人工角色。

| 能力 | 标注人员 | 管理人员 | 部署后证据 |
|---|---|---|---|
| 创建任务、上传图片、配置标签、邀请协作者、人工标注和确认预标注 | 允许 | 允许 | 两账号各创建/查看自己可访问任务；无项目空间选择 |
| 发起训练、查看训练、评估和下载 PT/ONNX | 允许（按任务成员权限） | 允许 | 非任务成员被拒绝；训练记录与冻结数据集关联 |
| 发起 BM1688 编译和边端验证 | 允许（按任务/模型访问） | 允许 | 三精度清单、固定评估集和设备证据门禁有效 |
| 发布模型到 AICM，并保留本平台模型目录 | 允许，发布前仍必须通过全部质量/清单门禁 | 允许 | 标注人员发布成功后产生同一 AICM `model_set_id` 的目录记录 |
| 禁止问题模型后续下载 | 不允许修改全局下载控制 | 允许 | 管理人员操作后平台下载接口拒绝，历史发布记录不删除 |
| S3 配置新增、修改、删除、切换当前连接 | 不允许 | 允许 | 标注人员返回 403；管理员变更有审计记录 |
| S3 回流包接收、下载、去重、聚合、复用任务、自动预标注 | 系统自动 | 系统自动；管理人员只看异常、改规则、抽查 | 不要求人工逐包建任务；Worker 状态和异常原因可查 |
| 模型目录同步、别名和发布环境等全局治理 | 不允许 | 允许 | 标注人员返回 403；读取仍按账号权限提供 |

## 4. 旧入口和统一 Worker 状态核验

### 4.1 旧入口

`/api/v1/integrations/strategy-tasks` 只允许接收/转译/幂等投影，最终返回
`/internal/v1/experiment-datasets/{package_id}` 状态地址。部署后用重复请求验证：

1. 首次请求只产生待处理导入记录，不产生任务、附件、样本或预标注；
2. 相同 `external_task_id` 和相同请求返回同一 `package_id`；
3. 内容变化返回 409；
4. 请求处理日志中没有 Web 进程 S3 下载和任务写入；
5. Worker 完成后旧入口查询能读到统一导入状态、正式任务 ID 和预标注数量；
6. 原同步函数保持 410 失败关闭，不能被内部调用恢复。

### 4.2 四个独立 Worker 单元

部署脚本定义的 systemd 单元及必须核验状态：

| Worker | systemd 单元 | 主要开关/门禁 | 必须观察 |
|---|---|---|---|
| 训练 | `labelu-training-worker.service` | `YOLO_TRAIN_DEDICATED_WORKER_ENABLED`；训练 Outbox、租约和 GPU 资源 | 单一领取者、过期接管、旧 token 写入拒绝、训练子进程退出和 GPU 释放 |
| BM1688 编译 | `labelu-bm1688-worker.service` | BM1688 Worker 开关、编译节点和三精度制品门禁 | PT→ONNX→FP32/FP16/INT8、失败终态、重启接管、无板端编译 |
| 边端验证 | `labelu-edge-validation-worker.service` | `YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED` | 设备租约、Board Agent/SSH 证据、失租终止、结果摘要一致 |
| 第三方 S3 回流 | `labelu-experiment-dataset-worker.service` | `EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED`、表租约列和生产安全配置；`ExecStartPre --preflight` | 未就绪不领取；S3 下载/去重/聚合/任务复用/预标注；重启接管；异常包不阻塞下一包 |

默认值中四类独立 Worker 仍有关闭项，部署不能只看 systemd 文件存在：必须同时核对 `.env` 开关、`systemctl is-enabled/is-active`、启动日志、租约字段和一条受控测试记录。Web 进程不得因 Worker 关闭而回退到同步执行。

## 5. 本地发现的契约缺口与修复

审计时 `labelu/tests/internal/common/test_internal_service_auth.py` 为 `4 passed, 1 failed`。失败项要求以下三条实验数据集内部读接口在 OpenAPI 声明 `APIKeyHeader`：

- `GET /internal/v1/experiment-datasets`；
- `GET /internal/v1/experiment-datasets/created-tasks`；
- `GET /internal/v1/experiment-datasets/{package_id}`。

该问题已在 RUN-0187 修复：`experiment_dataset_import` 三个 GET 路由均显式依赖 `verify_strategy_evolution_internal_request`。修复后认证与回流专项为 `28 passed`，OpenAPI/S3 回归为 `11 passed`。生产仍需部署后复验真实服务令牌/HMAC、重放拒绝和线上 OpenAPI `security` 字段。

## 6. 放行判断

本轮结论：生产快照与本地目标仍有明确差距。部署后必须先完成探针、OpenAPI、旧路径、角色和 Worker 状态核验，再进入真实 MySQL、S3、AICM、TPU-MLIR、BM1688 和浏览器联调。HTTP 200、systemd 文件存在或本地测试通过均不能单独证明业务闭环完成。
