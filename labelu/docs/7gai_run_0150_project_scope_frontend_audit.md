# RUN-0150 数据回流页面项目维度只读审计

## 1. 审计结论

当前“进化中心/数据回流”页面还不能按一期“无项目、无业务空间”方案运行。页面先读取可访问项目，再默认取第一个项目；后续难例审核、主动学习聚类、批次生成、数据集版本查询和工作流查询全部携带该项目编号。后端同样把项目编号作为必填参数、外键和权限判断依据。

这不是前端删除一个查询参数就能解决的问题。若直接删除参数，后端会返回 422；若前端填入默认项目，会把数据错误归到第一个项目，产生跨任务越权和错误训练血缘。本轮只完成审计和最小修改清单，没有修改业务代码。

## 2. 前端调用链证据

### 2.1 项目选择来源

| 文件 | 位置 | 当前行为 | 去项目化影响 |
|---|---:|---|---|
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/evolution.tsx` | 8、442 | 引入 `usePlatformProject()`，把 `projectId`传给两个子面板 | 页面入口必须移除项目状态和项目提示 |
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/project.ts` | 8-19 | 请求 `/v1/projects?page=0&size=100`；URL 没有项目参数时取第一个可访问项目 | 不能继续作为进化中心前置条件；该 Hook 仍可供尚未迁移的其他旧页面使用 |
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/routing.ts` | 3-6、53-68 | `projectQueryKey`把项目编号放入缓存键；`resolveProjectId`默认回退第一个项目 | 进化中心改用普通资源缓存键；不要全局删除，避免影响其他旧页面 |

### 2.2 页面请求与交互

`evolution.tsx` 的实际调用如下：

| 页面区域 | 当前请求 | 当前参数/前置 | 最小目标 |
|---|---|---|---|
| 难例审核列表 | `GET /v1/edge-feedback/hard-examples` | `project_id`、页码、状态；没有项目时不请求 | 改为按当前用户可见任务/回流来源查询，不传项目 |
| 难例详情/审核 | `GET/POST /v1/edge-feedback/hard-examples/{id}` | 详情按行 ID，审核按钮仍检查 `projectId` | 保留 ID 操作，后端按任务/创建者权限判断；去掉前端项目禁用条件 |
| 聚类 | `GET /v1/active-learning/clusters`、`POST /clusters/rebuild` | 查询和重建 body 都有 `project_id` | 按可见难例集合重建，范围由后端权限确定 |
| 主动学习批次 | `POST /v1/active-learning/batches`、`GET /batches/{id}`、`POST /batches/{id}/accept` | 创建 body 有 `project_id`；批次详情只用批次 ID | 创建按创建者和可见难例归属；详情按批次权限 |
| 数据集版本 | `GET /v1/datasets?project_id=...` 后逐个请求 `/v1/datasets/{id}/versions` | 页面只展示带 `active_learning_batch` 的版本 | 直接复用现有无项目的 `GET /v1/datasets`，按任务/创建者权限返回版本 |
| 重训工作流 | `GET /v1/workflows?project_id=...`，每 10 秒轮询 | 需要项目编号，后端还要求系统管理员 | 改为按关联数据集版本/回流批次查询；普通标注人员只能看自己有任务权限的工作流，管理人员看异常全局 |

页面中的缓存键也带项目编号，例如 `projectQueryKey('edge-hard-examples', projectId, status)`、`projectQueryKey('active-learning-clusters', projectId)`。去项目化后必须改成资源、状态、页码和批次 ID 组成的稳定键，否则会把历史项目缓存继续混入新结果。

## 3. 后端阻断证据

### 3.1 难例接口

- `labelu/internal/adapter/routers/edge_feedback.py:24-27`：列表接口的 `project_id: int = Query(gt=0)` 为必填。
- `labelu/internal/application/service/edge_feedback.py:35-52`：`_project()` 查询项目、项目所有者和项目成员；`list_by()`、`get_by_id()`、`review()`均调用该权限函数。
- `labelu/internal/application/service/edge_feedback.py:75-178`：接收推理事件时校验 `cmd.project_id`，按“项目 + event_key/fingerprint”做幂等，并写入项目外键。
- `labelu/internal/domain/models/edge_feedback.py:11-36`：`InferenceEvent`、`EdgeHardExample`的项目列非空；唯一键和队列索引都以项目开头。

### 3.2 主动学习接口

- `labelu/internal/application/command/active_learning.py:4-12`：重建和批次创建命令都要求 `project_id`。
- `labelu/internal/adapter/routers/active_learning.py:19-42`：聚类列表、重建、批次创建把命令直接交给服务层，没有无项目替代入口。
- `labelu/internal/application/service/active_learning.py:161-180、423-520`：`_project()`是所有操作的权限入口；查询、批次唯一键、已消费样本和跨项目校验均按项目过滤。
- `labelu/internal/application/service/active_learning.py:250-336`：接受批次物化清单、对象存储路径、`Dataset`、`DatasetVersion`和元数据都写入项目编号。
- `labelu/internal/domain/models/active_learning.py:12-38`：聚类和批次的唯一约束、热查询索引均以项目编号开头。

### 3.3 可复用但尚未直接接入的链路

- `labelu/internal/adapter/routers/dataset.py:45-55、134-218` 和对应 `dataset` 服务已经按当前用户、数据集所有者、任务创建者/协作者查询，不接收项目查询参数；这是数据集版本列表的首选复用链。
- `labelu/internal/adapter/routers/experiment_dataset_import.py:26-90` 和 `ExperimentDatasetImport` 模型已有 `source_system`、外部任务/包编号、`source_integration_id`、`model_id`、正式任务 ID、草稿任务 ID、创建者和 Worker 租约字段，不依赖项目；它适合作为第三方 S3 自动回流的来源和模型血缘真源。
- `labelu/docs/7gai_run_0127_strategy_evolution_no_project.md` 已明确新策略回流不接收项目字段，按外部任务、来源集成和任务权限建立血缘；进化中心应复用该原则，不能再创建项目工作流投影。
- `labelu/internal/adapter/routers/workflow.py:38-55` 的列表接口仍要求 `project_id` 且只允许系统管理员；它不能直接作为一期页面列表接口，需要增加按“数据集版本/来源集成/创建者”查询的受保护读接口，或改用回流导入详情中的关联工作流编号。

## 4. 最小去项目化修改清单

### 4.1 前端（R08，依赖后端契约先行）

1. `evolution.tsx` 移除 `usePlatformProject`、`projectId` 属性、项目选择提示、`enabled: Boolean(projectId)`和按钮上的项目禁用条件。
2. 难例列表、聚类、批次、数据集、工作流分别使用不含项目的缓存键；缓存键必须包含状态、页码、批次 ID 等真正影响结果的条件。
3. 请求不再发送 `project_id`；审核、接受批次、冻结版本仍以资源 ID 操作，失败时展示“无任务权限/状态已变化”，不提示切换项目。
4. 数据集版本列表直接调用无项目 `GET /v1/datasets`及其版本接口；不再先按项目取数据集。
5. 工作流列表改用新受保护的回流查询接口，或展示批次详情返回的工作流编号并按编号读取；禁止继续轮询 `/v1/workflows?project_id=...`。
6. 不修改 `project.ts` 和 `projectQueryKey` 的全局行为，直到其他旧页面完成各自迁移；只在进化中心建立普通资源查询键。

### 4.2 后端与数据（R03/R04，R09 验收）

1. 新请求不再接受 `project_id/projectId`；显式携带时返回 422，防止旧调用方误以为项目仍是业务归属。
2. `InferenceEvent`、`EdgeHardExample`、`FeedbackCluster`、`ActiveLearningBatch` 的项目列先改为可空历史兼容，新增真正的权限血缘字段：`created_by`、`task_id` 或 `source_integration_id`、`published_model_id/model_id`。新写入不得写项目编号。
3. 难例列表和详情按“管理人员全局异常权限；标注人员为本人创建/被分配任务”过滤。审核权限与现有两类角色一致：标注人员处理自己任务范围，管理人员处理全局异常，不再调用项目成员表。
4. 幂等和索引改为真实业务组合：事件使用 `source_integration_id + event_key` 或 `created_by + event_key`；难例使用来源事件/任务与指纹；聚类使用模型、设备、难例类型和来源范围；批次使用创建者、模型和批次键。迁移前必须在生产脱敏快照中检查重复，重复即失败关闭，不自动合并。
5. 聚类重建只扫描当前用户可见的未消费难例；批次接受只生成一次 DatasetVersion 和回流工作流，并保留任务、模型 ID、来源包和数据版本血缘。
6. 为工作流增加无项目的只读查询（按 `dataset_version_id`、`source_integration_id`、`created_by` 或任务可见性），不要复用当前仅系统管理员且强制项目的列表接口。
7. 第三方 S3 接收继续复用 `ExperimentDatasetImport` 的包级幂等、AICM/模型 ID解析、任务创建/复用和独立 Worker；进化中心页面消费其状态和关联任务，不在页面重复下载 S3 或创建第二套项目工作流。

## 5. 测试建议

### 前端

- `evolution.test.tsx`：页面加载不请求 `/v1/projects`；所有请求 URL 和 body 不含 `project_id/projectId`；没有项目选择提示；标注人员和管理人员都能看到有权限的回流任务。
- 回流列表、详情、审核、聚类、批次和冻结的 React Query 缓存键不含项目值，且状态/批次变化不会读到旧缓存。
- 错误状态覆盖 401、403、404、409、离线；提示应是任务权限、来源模型未登记或状态冲突，不再提示切换项目。

### 后端/API

- OpenAPI 契约：难例、聚类、批次和新增工作流查询的请求/响应不出现项目字段；旧调用显式携带项目字段返回 422。
- 权限：标注人员只能访问本人创建或被分配任务关联的样本；管理人员可以查看全局异常；任意用户不能通过资源 ID读取他人任务。
- 血缘：回流包 `package_id`、第三方来源、AICM 模型 ID、任务 ID、样本 ID、DatasetVersion 和训练作业能逐级对账；历史项目列只读，不参与新写入。
- 幂等/并发：同一事件、指纹、批次在 2/10/20 并发下只产生一条记录；批次接受重试只生成一个数据集版本和一个工作流；锁等待和失租 Worker 均失败关闭。
- 迁移：脱敏生产快照执行项目列可空、唯一键重建、索引命中和备份恢复；不能用空库 SQLite 通过代替生产迁移验收。

## 6. 本轮边界

本轮为本地只读代码审计，未连接生产、S3、AICM、MySQL、TPU-MLIR 或 BM1688，未修改业务代码，未提交、未推送、未部署。上述清单是下一工作包的实施基线，不代表进化中心已经去项目化或生产闭环已经通过。
