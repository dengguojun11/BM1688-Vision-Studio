# RUN-0149 AICM 发布、模型目录下载与第三方 S3 回流证据审计

## 1. 轮次信息

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色：R05 外部平台、R03 架构、R04 后端、R09 测试、R10 运维。
- 工作包：WP3 BM1688 编译验证与发布、WP4 第三方 S3 回流与自动进化。
- 需求编号：`PUBLISH-AICM-ID-01`、`PUBLISH-DOWNLOAD-CONTROL-01`、`S3-REFLOW-MODEL-ALIGN-01`、`PUBLISH-CONCURRENCY-01`。
- 交付级别：本地 L1 只读审计；未提交、未推送、未部署，未连接或修改生产。

## 2. 本轮结论

### 2.1 AICM 发布和本平台模型目录已经形成一条可追踪链

当前代码链为：

`BM1688 导出记录(export_id)` → `AICM 后台发布任务(JSON job)` → `AICM model_set_id` → `published_model_catalog` → `LabelU 本地模型包`。

发布任务上传成功后，不直接相信不完整的上传响应，而是通过上传响应中的 ID、上传前后模型集差异和临时关联名称复核唯一的 AICM 模型集；无法唯一确认时停止 `edit_name`。成功后使用 AICM 的 `model_set_id` 作为平台 `public_model_id`，并把同一个 ID 写回导出记录和本地目录。这个 ID 是后续第三方回流 `target.model_id` 对齐的唯一业务标识。

成功发布还会把 ZIP 复制到 `published_model_packages/<AICM模型ID>/model.zip`，记录大小、SHA256、MD5 和发布任务 ID。下载优先读取 LabelU 的长期保留包，AICM 仅作为一次性补缓存来源；因此 AICM 暂时不可达时，已保留的本平台包仍可下载。

### 2.2 下载控制符合“问题模型人工禁止下载”，没有引入回滚

- `/api/v1/published-models` 是登录后的模型目录；标注人员和管理人员按既有任务权限使用。
- `/api/v1/public/models` 只展示 `active + is_public + download_enabled + 有可用包` 的记录，未授权用户不会看到被隐藏或禁止下载的模型。
- `download_enabled` 使用“携带当前值后原子更新”的方式修改；并发修改时返回 `409`，不会覆盖他人的最新决定。
- 下载前再次检查开关，关闭后返回 `409`，且不会触发 AICM 补缓存请求。
- 本平台不召回已经被其他平台下载的文件，也没有模型回滚；有问题时管理人员关闭下载并发布新的导出/模型版本。

因此本轮不改下载接口的 `status/is_public` 语义。`is_public` 控制匿名公开目录，`download_enabled` 才是人工禁止下载开关；把“隐藏目录”误当成“禁止已登录用户下载”会破坏当前生产约定。

### 2.3 第三方 S3 回流已经有自动处理骨架，但真实外部闭环证据缺失

生产页面 `/strategy-integration-s3-config` 只维护第三方 S3/MinIO 连接地址；访问密钥仍来自受保护的运行配置。外部平台把包含 `metadata.json` 和图片前缀的包提交到 `/internal/v1/experiment-datasets`（旧 `/integrations/strategy-tasks` 仅是兼容适配），Web 请求只写入 `PENDING`，独立 Worker 再领取和处理。

回流 Worker 已实现：

1. `package_id` 唯一约束和请求内容摘要校验，重复通知不会重复建包；
2. `aggregation_key` 聚合同一模型、事件、日期和时间窗的包，复用正式任务或草稿任务；
3. 校验 metadata、图片对象和框坐标，异常样本进入部分完成/失败状态；
4. 通过发布模型目录按 AICM ID 找到源导出、训练作业、标签和权重；模型未登记或标签不一致时进入 `WAITING_FOR_MODEL`，不使用导出记录 ID 猜测模型；
5. 写入第三方 metadata 预标注，并继续触发本平台自动预标注；
6. 使用 Worker owner、租约和 fencing token，支持过期接管，旧 Worker 不能覆盖新 Worker 终态。

“自动”在一期的含义是“第三方推送后自动入队并由独立 Worker 完成导入”，不是当前代码主动轮询第三方桶。如果生产要求平台自己扫描桶，需要另立 S3 watcher 工作包，不能把当前 API 入队链误写成已具备桶扫描。

## 3. 只读代码/API证据

### 3.1 AICM 和模型目录

- `labelu/internal/application/service/yolo_aicm_publish.py`：发布任务、上传后 ID 复核、`model_set_id` 写回导出记录。
- `labelu/internal/application/service/published_model_catalog.py`：本地包保留、AICM 远端补缓存、模型解析、匿名/登录下载门禁和原子下载开关。
- `labelu/internal/domain/models/published_model_catalog.py`：`public_model_id` 唯一、`aicm_model_set_id`、本地包摘要、下载开关和 AICM 别名。
- `labelu/internal/adapter/routers/published_model_catalog.py`：模型目录、详情、登录下载、匿名公开目录和下载接口。
- `labelu/internal/adapter/routers/yolo_aicm_publish.py`：发布候选、发布任务和历史接口。

### 3.2 S3 回流和模型 ID 对齐

- `labelu/internal/application/service/experiment_dataset_import.py`：统一受理、模型解析、聚合、metadata/图片校验、预标注和状态机。
- `labelu/internal/application/service/strategy_task_integration.py`：旧策略入口兼容桥，只负责翻译到统一导入链，不应继续拥有另一套同步真源。
- `labelu/internal/application/service/experiment_dataset_import_worker.py`：独立领取、租约续期、fencing 和失败隔离。
- `labelu/internal/domain/models/experiment_dataset_import.py`：包唯一键、模型 ID、聚合键和 Worker 租约字段。
- `labelu/internal/application/service/yolo_preannotate.py` 与 `strategy_evolution.py`：优先按发布模型目录/AICM ID解析，明确拒绝 `direct_export_id` 伪装成发布模型。

## 4. 已确认的关键缺口

### 4.1 P0：同一导出记录没有跨进程发布领取租约

`start_publish_job` 每次请求都会创建新的 JSON 发布任务；`list_publishable` 仍会展示已有 `published_model_id` 的导出记录。当前没有数据库级的“同一 `export_id` 只能有一个进行中的 AICM 发布”条件更新。因此两个 Web 进程、两个浏览器或自动进化重试同时点击时，可能同时上传两个 AICM 模型集，最终产生两个外部 ID，虽然本地目录仍不会覆盖同一个旧 ZIP。

这不是下载开关问题，也不能用进程内互斥锁解决。必须在正式 MySQL 上有跨进程的领取条件、租约续期、fencing 和明确的失败重试语义。用户已经明确没有模型回滚，因此同一导出版本发布成功后应改为“已发布，不再重复发布”；需要替代模型时创建新的导出/模型版本。

### 4.2 P1：本地模型包补缓存可能重复请求 AICM

不同请求首次下载同一个远端模型且本地包尚不存在时，会各自创建临时文件并下载；最终文件通过临时文件和原子替换保持完整，但会浪费 AICM 带宽和 Web 线程。生产压测前应增加按模型 ID 的缓存租约或单飞机制，并测量 5/10/20/50 并发下载的外部请求数、磁盘增长和 P95。

### 4.3 P1：真实 S3 metadata 与 AICM 对账样本尚未提供

当前本地代码支持 `model_id` 字符串/数字形式和等待模型状态，但尚无生产真实包证明以下字段完全一致：第三方 metadata 的模型 ID 字段、AICM `model_set_id`、LabelU 目录 `public_model_id`、样本文件名、时间窗和标签集合。未有这组证据前，不能把“代码能解析”写成“第三方回流已生产通过”。

### 4.4 P1：一期没有 S3 桶主动扫描器

生产页面是连接配置，不是数据包列表或扫描任务页面。若第三方只把文件放入桶而不调用统一受理接口，当前系统不会自动发现该包；这属于外部协议边界。需要业务确认继续采用“第三方推送 API”，还是在二期新增只读扫描器、对象事件通知和扫描游标。

## 5. P0 发布租约的落地选择

### 5.1 方案 A：复用 `yolo_bm1688_export` 增加发布租约字段（一期推荐）

在现有导出表增加：`publish_status`、`publish_job_id`、`publish_lease_owner`、`publish_lease_expires_at`、`publish_fencing_token`。领取时使用带状态和过期时间条件的原子更新；执行期按 owner/token 续租；成功时写入 `published_model_id`，失败时释放租约并保留 JSON 历史。`start_publish_job` 和自动进化入口共享同一个领取函数，`list_publishable` 排除已成功或正在发布的导出记录。

选择依据：导出记录已经是 PT→ONNX→BModel 的唯一血缘，当前表已经有编译队列/执行租约字段，新增字段最少、页面无需新增菜单、回流仍可通过 `published_model_id` 复用同一主键。代价是对生产大表加列和索引，必须先做同版本 MySQL 在线 DDL、锁等待和快照恢复演练。

### 5.2 方案 B：独立 `aicm_publish_execution` 表（暂不作为一期首选）

独立表保存 `export_id`、环境、尝试次数、状态、租约 owner/过期时间、fencing token、最终 AICM ID 和错误摘要；`export_id` 建唯一活动约束，历史尝试另表保留。优点是发布重试和审计更完整，不增加导出热表字段；缺点是新增表、外键和查询联接，MySQL 没有通用部分唯一索引时还要设计“活动记录”约束，迁移和旧历史回填更复杂。

一期按“简单、规则灵活、复用”原则推荐方案 A；若生产发布量、审计留存或多 AICM 环境隔离超过单表承载，再在二期把尝试历史拆成方案 B。两种方案都不能依赖进程内锁，也不能把 JSON 文件当成跨主机唯一真源。

## 6. 生产只读证据与边界

已保存证据文件：`.tmp/evidence/run0119-production-admin-readonly.json`、`.tmp/evidence/run0125-production-readonly.json`。

- 地址：`http://113.141.72.175:10000`；探针已登录，作用域为任务和数据集权限。
- `/openapi.json` 返回真实 JSON；未登录访问任务接口返回 `401`。
- `/api/v1/published-models` 返回 `200`，`/api/v1/strategy-integration-s3-config` 返回 `200`。
- 生产仍缺少 `/api/v1/datasets`、`/api/v1/algorithms`、`/api/v1/model-versions`、`/api/v1/workspace/summary`，并继续暴露已退休的 `/api/v1/edge-device-jobs*`；整体验收为失败。
- 这些证据只证明路由和认证边界，不证明真实 AICM 上传、本地包下载、第三方 S3 对象、metadata、Worker、MySQL 并发或模型 ID 对账。
- 本轮未连接或写入生产，不改变 S3 配置，不打开 Worker。

## 7. 本地验证

- 目标模块 `python -m py_compile` 通过：AICM 发布、模型目录、S3 导入和发布任务文件。
- `git diff --check` 对目标文件无格式错误。
- 目标 pytest 集合未能启动：共享导入阶段遇到工作区另一轮正在修改的 `labelu/internal/application/service/board_agent.py:634` 语法错误（`renewed = renew_execution_lease(` 未闭合）。这不是本轮 AICM/S3 代码错误；待 R07/R09 修复后必须重跑发布目录、S3 回流和并发专项。

## 8. 下一依赖和验收门槛

1. R03/R04：按方案 A 设计正式 Alembic 迁移和原子发布领取函数；先在空 SQLite、同版本 MySQL 沙箱、脱敏生产快照分别升级，确认字段、索引、唯一约束和降级策略。
2. R05/R06：提供真实 AICM 测试模型、同一 `export_id` 的重复提交样例、AICM 外部请求编号和最终 `model_set_id`；证明失败重试不会产生未登记孤儿模型。
3. R09：执行 2、10、20、50 并发发布领取；验证只产生一个 AICM 上传、旧 token 不能改状态、失败可重试、成功后再次提交被拒绝；补本地目录 5/10/20/50 并发下载测试。
4. R10：在 Ubuntu/systemd 和 MySQL 上验证发布 Worker/回流 Worker 重启接管、AICM/S3 网络超时、磁盘空间和告警；生产开关继续关闭。
5. R05/R04：拿到真实第三方 S3 `metadata.json`、图片对象清单、回流请求 ID、AICM ID、LabelU import/task/sample/preannotation ID 对账表；确认一期采用推送 API还是二期桶扫描器。
6. 生产路由缺口（数据集、算法目录、模型版本、工作台汇总、退休旧路由）解决并部署后，再进行真实 AICM/S3 端到端验收。

## 9. 提交与部署状态

本轮只读审计和文档记录，未修改业务代码、数据库、生产配置或模型制品。代码未提交、未推送、未部署；等待用户明确“提交代码”。

