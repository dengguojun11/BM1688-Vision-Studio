# RUN-0140 第三方策略 S3 问题图片自动回流审计

## 1. 审计范围

本轮按 R05 外部平台、R04 后端、R09 测试职责，审计生产已存在的第三方策略 S3 问题图片回流链路。审计对象包括：

- `metadata.json` 读取、样本与图片对象对齐及预标注；
- AICM 模型 ID 与平台发布模型、标签分类和后续专家回流的对齐；
- 包级幂等、任务复用、重复图片处理；
- 自动导入、少量人工异常确认和 Worker 租约/失败关闭；
- 已上线旧入口 `/integrations/strategy-tasks` 与新入口 `/internal/v1/experiment-datasets` 的边界。

本轮没有连接生产 S3、生产 MySQL 或 AICM，不把本地测试和生产页面可访问写成真实链路通过。

## 2. 当前本地实现证据

| 能力 | 本地代码证据 | 结论 |
|---|---|---|
| 新入口受理 | `experiment_dataset_import.accept`、`/internal/v1/experiment-datasets` | 以 `package_id` 做唯一幂等；同一包内容变化返回 409；第三方记录由 `open@system` 服务账号写入 |
| metadata 读取 | `experiment_dataset_import._read_metadata_json`、`_metadata_samples` | 从配置的数据源和 `metadata_key` 读取 JSON，按 `samples[].image.file_name` 与 `image_prefix` 对齐图片；缺图片进入异常计数 |
| AICM ID 血缘 | `_published_model_dataset_category`、`strategy_evolution._resolve_export_id_from_model_id` | 请求 `target.model_id` 可解析平台发布目录、导出、训练和数据集分类；找不到发布模型时进入 `WAITING_FOR_MODEL`，不猜测模型 |
| 自动建/复用任务 | `_process_import_group`、`_get_or_create_formal_task`、`_get_or_create_draft_task` | 聚合组按稳定身份复用正式任务；按标签预置复用任务；未知标签进入草稿任务；满足最小样本数后自动处理 |
| 预标注 | `_create_preannotation_if_missing`、`schedule_auto_preannotate_for_new_samples` | metadata 中有效框写入预标注，同时按任务提交自动预标注作业；标注人员只需确认异常/低置信样本 |
| Worker 失败关闭 | `experiment_dataset_import_worker.dedicated_worker_ready`、`claim_pending`、`run_import_job_now` | 租约字段/生产安全配置未就绪时退出码 78；owner、token、过期时间条件写入；单包异常不阻断同轮其他包；旧 Worker 不能覆盖接管者 |
| 任务内重复 | `_get_or_create_attachment`、`_attach_sample_if_missing` | 同一任务同一文件路径/文件名可复用附件和样本；同一包 metadata 重复文件名不会重复建样本 |

## 3. 已确认缺口

### 3.1 两套回流入口行为不一致（P0，生产放行阻断）

旧入口 `/integrations/strategy-tasks` 的历史实现曾直接同步读取 S3、创建任务和预标注。RUN-0181 已将当前本地实现收敛为兼容适配器：按 `external_task_id` 幂等后只转译并调用 `experiment_dataset_import.accept()`，不在请求内读取 S3 或写任务；后续由统一 Worker 执行 AICM `model_set_id` 校验、聚合等待、任务复用和预标注。原同步实现已显式 `410` 失败关闭，防止内部调用绕过新链。

新入口 `/internal/v1/experiment-datasets` 才具备 AICM 血缘解析、聚合任务复用和独立 Worker。生产历史事实显示 `strategy_task_integration` 有 23 条记录，且 S3 契约字段为空、仅 2 条关联 LabelU 任务，因此不能假设旧记录已自动导入。

**处理决定：** 旧入口保留接收/转译兼容层，统一落到新回流记录和 Worker；历史已完成投影只读兼容，不自动重导入。RUN-0181 的本地专项已验证旧入口不读 S3、不建任务；生产部署后仍需真实请求回放和 Worker 状态对账，未对账前不能计入“生产自动回流通过”。

### 3.2 去重只达到包级/任务内路径级（P1）

`package_id` 唯一约束和任务内 `task_id + local path` 复用可以防止重复通知、同名文件重复建样本，但当前没有跨包内容 SHA256、感知哈希或近重复图片索引。不同文件名的同一图片、压缩重编码图片仍可能进入任务。

**处理决定：** 不在本轮无真实样本规模和 MySQL 索引评估的情况下直接新增大表哈希字段。下一轮应先用真实 S3 包抽样确定：原图 SHA256 去重、感知哈希阈值、索引列和迁移成本，再由 R04/R09 增加可审计的 `duplicate_of_sample_id`/去重结果，不把文件名相同宣称为内容去重。

### 3.3 metadata 与 AICM ID 的生产字段契约尚未实证（P0）

新入口的 `target.model_id` 支持 `modelId`/`model_id` 别名并持久化，但 `_read_metadata_json` 不会校验 `metadata.json` 内是否存在 AICM ID、是否与请求目标一致。当前本地测试主要验证请求字段和模型解析，没有真实 `metadata.json` 字段样例。

**处理决定：** 先向第三方确认正式字段名和签名/版本，再增加兼容读取和不一致拒绝；在此之前，生产验收必须记录 metadata 原文摘要、解析出的 AICM ID、请求 ID 和平台目录命中记录。缺少 ID 或不一致的包只能进入异常，不得自动建正式任务。

## 4. 自动处理与人工处理边界

1. 自动：接收通知、读取 `metadata.json`、列举和断点下载图片、包级幂等、任务聚合、发布模型解析、标签/分类校验、预标注、Worker 重试和状态审计。
2. 少量人工：只处理 S3 不可读、metadata 字段冲突、模型 ID 未发布、标签无法映射、低置信/抽检失败和近重复待确认样本；人工确认后回到同一任务，不另建人工数据集链路。
3. 禁止人工：不要求管理人员逐张下载、手工建任务或手工维护 manifest；管理人员只调整规则、处理异常和抽查。

## 5. 并发、租约与性能审计

- 新 Worker 使用 `worker_lease_owner`、`worker_lease_expires_at`、`worker_fencing_token`、`attempt_count`；同一包由一个有效 owner 执行，过期后可接管，旧 token 更新失败。
- 聚合组处理时会检查是否仍存在其他有效包租约，避免旧 Worker 把组标记失败覆盖新 Worker。
- 目前只具备本地 SQLite/静态契约证据；未证明 MySQL `SELECT ... FOR UPDATE`/条件更新在 20 并发、长下载、锁等待和节点重启下的行为。
- 生产必须限制并发包数、单包图片数、单图片大小、下载超时和磁盘配额，并监控 `PENDING/PROCESSING/WAITING/FAILED` 积压；这些值需要用真实 S3 包压测后定标，不能沿用本地默认值。

## 6. 本地验证与真实环境边界

本轮本地 Worker 专项：

```text
python -m pytest labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q
6 passed
```

该结果只证明租约领取、接管、禁用和单包失败隔离测试通过。RUN-0181 另有旧适配器和旧路由专项 `16 passed`，证明旧入口在本地只入统一队列；仍不等于真实 S3/AICM/生产 Worker 已通过。

生产只读已知事实：`/strategy-integration-s3-config` 只维护 S3 连接；生产有 1 个启用连接，`strategy_task_integration` 有 23 条历史记录但 S3 契约字段为空。尚未获得有效生产 `metadata.json`、图片对象、AICM 发布 ID 对账、Worker 日志或 MySQL 并发证据。

## 7. 放行条件与责任人

| 阶段 | 必须证据 | 主责 |
|---|---|---|
| 接口切换 | 旧入口转译/停用方案、真实请求回放、重复通知 409/复用证据 | R03/R04/R05 |
| metadata 契约 | 脱敏字段样例、AICM ID 一致性和签名/版本校验 | R05/R04 |
| S3 导入 | 小包、大包、断点、`.part` 清理、断电接管 | R04/R09/R10 |
| 并发租约 | 同版本 MySQL 双 Worker、20 并发、锁等待、旧 token 拒绝 | R04/R09/R10 |
| 任务与人工闭环 | 自动建/复用任务、异常少量抽查、确认后自动训练候选 | R01/R08/R09 |
| AICM 血缘 | AICM `model_set_id`、平台发布目录、本地下载目录和回流任务一致 | R05/R06/R09 |

本轮结论：**本地 L1 审计完成；第三方 S3 自动回流、AICM 对账和生产 Worker 均未放行。**

提交/推送/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
