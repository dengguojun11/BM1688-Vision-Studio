# RUN-0142 旧策略 S3 入口兼容转译只读分析

## 1. 本轮边界

- 责任角色：R03 架构、R04 后端、R05 外部平台（主分析）；R09 测试、R10 运维提供验收依赖。
- 工作包：第三方 S3 回流入口统一（分析阶段）。
- 本轮只读检查，不修改业务代码、数据库、迁移、前端或部署文件。
- 目标：确认旧 `/api/v1/integrations/strategy-tasks` 的真实调用链、字段和副作用，并定义迁移到 `/internal/v1/experiment-datasets` 的最小兼容边界。

## 2. 代码路径证据

### 2.1 旧入口

入口文件：`labelu/internal/adapter/routers/strategy_task_integration.py`

1. `POST /integrations/strategy-tasks`（挂在 `/api/v1` 下）。
2. 根据 `schema_version` 判断是否要求第三方 HMAC；签名仍按旧请求路径校验。
3. 直接调用 `strategy_task_integration.create`，当前返回 HTTP 201。

服务文件：`labelu/internal/application/service/strategy_task_integration.py:671-967`

旧服务在 HTTP 请求内完成：

1. 按 `external_task_id` 查询并校验请求摘要幂等。
2. 解析 `dataset.s3_uri`，拼接 `image_prefix`、`metadata_key`。
3. 复用或创建 S3 `DataSource`。
4. 创建或复用 `Task`，写入附件和样本。
5. 同步列出 S3 图片、读取 `metadata.json`。
6. 读取 `target_boxes`，写入 `TaskPreAnnotation`。
7. 另外排入自动预标注。
8. 成功写 `StrategyTaskIntegration.import_status=COMPLETED`，失败写 `FAILED`。

旧业务真源表为 `strategy_task_integration`，唯一约束是 `external_task_id`。旧服务没有调用发布模型目录解析，也没有使用新回流 Worker 的队列租约、聚合等待或 fencing。

### 2.2 新入口

入口文件：`labelu/internal/adapter/routers/experiment_dataset_import.py`

- `POST /internal/v1/experiment-datasets`（注意：该路由不带 `/api/v1` 前缀）。
- 验签后调用 `experiment_dataset_import.accept`，只锁定并写入 `ExperimentDatasetImport` 和 `ExperimentDatasetGroup`，返回 202 受理结果。
- 图片、metadata、任务和预标注由独立 `experiment_dataset_import_worker` 领取处理。

新服务具备：

- `package_id` 幂等和聚合键锁定；
- AICM `target.modelId/model_id` 发布目录解析；
- 待模型、待样本、待分类等等待状态；
- 独立 Worker 的 owner、fencing token、过期接管；
- 任务、附件、样本和预标注的可恢复处理。

## 3. 请求字段差异

旧命令：`labelu/internal/application/command/strategy_task_integration.py`

```text
schema_version
external_task_id
task_name
task_type
dataset{s3_uri,image_prefix,metadata_key}
labeling{required,IMAGE,bbox,labels,...}
target{device_type,event_type,model_id,scene_type,strategy_id}
experiment
source_system
dataset_category_key
```

新命令：`labelu/internal/application/command/experiment_dataset_import.py`

```text
package_id
source_type/source_system/source_external_task_id
dataset_type/dataset_date/window_start/window_end
dataset{s3_uri,bucket,prefix,image_prefix,metadata_key,...}
labeling/target
sample_count/target_boxes_required
dataset_category_key/aggregation_key
```

兼容转换的必要规则：

| 旧字段 | 新字段 | 规则 |
|---|---|---|
| `external_task_id` | `source_external_task_id` | 原值保留，不改名丢失 |
| 无 `package_id` | `package_id` | 生成稳定的 `legacy:{source_system}:{external_task_id}` 形式并做长度/哈希处理；不得使用每次变化的当前时间或请求摘要作为主键 |
| 旧 `source_system` | 新 `source_system` | 原值保留，`source_type=third_party` |
| `dataset.s3_uri` | 新 `s3_uri/bucket/prefix` | 结构化解析；解析失败拒绝，不补空值 |
| 旧相对 `image_prefix` | 新完整 `image_prefix` | 与 S3 前缀规范化后只拼一次，禁止双前缀 |
| 旧 `metadata_key` | 新完整 `metadata_key` | 与 S3 前缀规范化后只拼一次 |
| `task_type` | `dataset_type` | 使用稳定兼容映射并保留原始 task_type |
| `target.model_id` | `target.modelId` | 原值作为 AICM ID，必须走发布目录解析 |
| `labeling` | `labeling` | 先沿用旧标签首字母归一化、去空、去重规则 |
| `dataset_category_key` | 同名字段 | 保留并校验与模型血缘分类一致 |
| `experiment` | 新请求原始载荷 | 不丢弃；日期窗口只在其中存在且可验证时映射 |

旧协议没有 `dataset_date/window_start/window_end`。不能用当前时间伪造业务窗口；应采用明确的“未提供/legacy”兼容值或调整新命令使 legacy 模式可空，并将该缺口显示在审计中。默认聚合键应至少包含来源系统、外部任务、AICM ID 和 S3 前缀，避免无意合包。

## 4. metadata 兼容风险

旧解析器允许：

- `samples` 数组、`images` 数组、根数组、单样本对象；
- 图片名字段 `image_name/file_name/filename/image/image_path/image_key`；
- 框字段 `x/y/width/height/label`。

新 Worker 当前主要接受：

- 字典中的 `samples` 数组；
- `image.file_name` 或少数扁平图片名字段；
- 框字段 `x1/y1/x2/y2/class_name`，并进行尺寸/百分比换算。

因此旧 S3 包直接转到新 Worker 可能出现“图片导入成功、所有框被静默丢弃、预标注数为 0”。统一入口前必须在新解析层兼容两类格式并保存原始框；metadata 有框但归一化后全部无效时应进入异常/失败，不得标记成功。

## 5. 兼容投影边界

`ExperimentDatasetImport` 必须是图片、样本、预标注和 Worker 状态的唯一真源。若既有策略自动化、训练和页面仍需要 `StrategyTaskIntegration`，旧表只能保留为策略/回调兼容投影：

1. 新入口先按 `package_id` 受理。
2. 再创建或复用 `StrategyTaskIntegration`，保存旧外部任务和请求摘要、`source_package_id`，不在旧服务中导入图片。
3. 回写 `ExperimentDatasetImport.source_integration_id`，任务完成后通过 `source_external_package_id` 或该关联查询。
4. 历史旧行只读迁移；相同 `external_task_id` 但请求摘要、S3 或模型身份冲突时返回冲突，不覆盖历史数据。

`StrategyTaskIntegration` 的非空 S3 字段必须写入真实解析值，不能用空值伪造完成。旧响应可以暂时保持字段形状，`labelu_task_id/datasource_id` 在后台完成前为空，`import_status=PENDING`；不得返回同步 `COMPLETED` 假象。

## 6. 同步/异步决定

兼容入口必须复用新 `accept` 和独立 Worker，不能在 Web 请求内读 S3、创建全部样本或写完成状态。由于生产调用方是否接受 202 尚未在仓库中找到证据，默认兼容策略是保留 HTTP 201 表示“兼容记录已创建/已入队”，响应明确返回 `PENDING`、`package_id` 和 `status_url`；只有 R05 用真实调用方确认支持后才改为 202。旧入口不应再调用 `strategy_task_integration.create`。

## 7. 生产和页面证据

2026-09-19 对 `http://113.141.72.175:10000/openapi.json` 做只读检查：

- 同时列出 `/api/v1/integrations/strategy-tasks` 和 `/internal/v1/experiment-datasets`；
- 旧入口 OpenAPI 为 POST、201、无需 Bearer（v2 请求仍按 HMAC 规则）；
- 新入口为 `/internal/v1/experiment-datasets`，不是 `/api/v1/internal/v1/...`；
- `/strategy-integration-s3-config`、`/third-party-calls`、`/yolo-training` 返回页面 HTML。

仓库前端没有调用旧 POST。S3 配置页只维护 `/api/v1/strategy-integration-s3-config*` 连接，不上传图片；第三方调用页只读展示链路；训练页读取策略自动化状态。旧 POST 的真实调用方应为生产策略/第三方系统，必须由 R05 提供脱敏请求、metadata 和响应处理证据。

## 8. 已发现的项目维度残留

当前 `StrategyTaskIntegration` ORM 已没有 `project_id`，但未提交改动中的以下代码仍访问它：

- `labelu/internal/common/third_party_call_audit.py:204-227`；
- `labelu/internal/application/service/third_party_calls.py:191` 及其项目过滤分支。

审计持久化会捕获异常并丢弃该条审计；第三方链路列表/详情在命中策略记录时可能直接 `AttributeError`。应由 R03/R04 删除项目过滤和输出，不能为了兼容旧入口恢复项目/业务空间。

## 9. 实现前置验收

- 旧 payload 转换快照：字段、S3 前缀、AICM ID、标签和 aggregation_key 可复现。
- 同一 `external_task_id` 重复通知不重复创建 package、投影、任务、附件、样本或预标注。
- 相同外部 ID 载荷变化维持旧冲突语义，或明确生成新版本，不能静默覆盖。
- 未发布或不存在的 AICM ID 进入 `WAITING_FOR_MODEL`，不创建可训练任务。
- 旧 v2 签名仍按旧 URL 验证；nonce 重放和限流保持拒绝。
- 旧入口请求立即返回，后台 Worker 执行；过期租约可接管，旧 token 不得写终态。
- `samples/images`、旧图片名字段、`x/y/width/height/label` 和新框格式均有回归用例。
- 真实 S3 包、真实 metadata、AICM 目录对账、生产 Worker 和外部响应处理由 R05/R09/R10 在提交部署后验收。

## 10. 本轮结论

旧入口不能继续作为第二条同步导入链路。最小安全改造是“保留旧 URL 和签名协议，转换为新回流命令，统一由 ExperimentDatasetImport + 独立 Worker 处理，StrategyTaskIntegration 仅作兼容投影”，同时补齐 metadata 格式归一化、AICM 发布门禁、项目残留清理和真实第三方契约证据。

状态：本地只读分析，未提交、未推送、未部署；不能宣称生产自动回流已完成。

## 11. 后续实现轮次补充

本轮分析后已在本地加入兼容转译实现：旧入口只创建兼容投影并调用统一 `accept`，新增旧状态查询；新 Worker 兼容旧图片字段和 `x/y/width/height/label` 框，并回写旧投影的首个任务 ID。`EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false` 时明确失败关闭，不再由 Web 线程回退执行。

本地专项 `test_legacy_s3_adapter.py` 为 `6 passed`，统一回流 Worker 专项为 `6 passed`，Python 编译和差异空白检查通过。旧路由历史测试仍使用未升级的 SQLite 表结构（缺少 Worker 租约列），且断言请求内同步 `COMPLETED`，需在迁移演练后改为“受理 PENDING、显式运行 Worker、再检查完成”的新契约；这不是生产链路通过证据。
