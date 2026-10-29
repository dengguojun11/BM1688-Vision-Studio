# RUN-0120 第三方 S3 回流去项目化与 Provider 兼容收口

## 1. 本轮定位

- 日期和时区：2026-09-19，Asia/Shanghai。
- 方案依据：《7GAI进化平台菜单级产品与业务闭环方案》阶段 0/3、P4 回流与自动进化、无项目/业务空间约束。
- 需求编号：`DATA-S3-SCOPE-01`、`DATA-S3-IDEMPOTENCY-01`、`PROVIDER-LINEAGE-COMPAT-01`、`MIG-INGEST-01`。
- 主责角色：R04 后端业务与权限、R03 架构与迁移。
- 协作角色：R01 产品、R05 外部平台与回流、R06 模型算法、R09 测试与验收、R10 运维与安全。
- 工作包：WP0 基线收口、WP1 任务与数据、WP4 回流与自动进化、WP5 生产放行。

本轮解决的是第三方 S3 问题图片进入平台后的第一层数据边界。系统用户仍只有标注人员和管理人员；接入作业和媒体资产不再按项目/业务空间隔离。历史数据库中的 `project_id` 保留为可空兼容字段，不再作为一期 API 入参、响应字段、唯一幂等键或新写入值。

## 2. 业务与代码路径结论

### 2.1 新的接入主链

1. 标注人员或管理人员创建接入作业，使用请求头 `Idempotency-Key`；服务按 `created_by + idempotency_key` 幂等。
2. S3/第三方对象登记媒体资产，使用 `created_by + sha256 + media_type` 去重；重复请求返回既有资产，不重复增加作业计数。
3. 作业详情、资产列表、取消和上传会话桥接按创建者所有权和任务可见性校验；管理人员可以查看全局异常，普通标注人员只能看到自己的接入结果。
4. 通过扫描的资产才能进入 Provider 预标注；Provider Worker 的媒体资产血缘保留对象地址、摘要、类型和大小，后续与任务、预标注和 DatasetVersion 关联。

### 2.2 接口影响

- 保持：`POST/GET /api/v1/ingestion-jobs`、作业详情、取消、资产登记、上传会话桥接、`GET /api/v1/media-assets`。
- 移除一期新契约：请求体、查询参数和响应中的 `project_id`、`scene_id`。
- 保持安全：`source_snapshot` 中的密码、密钥、令牌和授权字段仍统一脱敏为 `[REDACTED]`。
- 保持兼容：历史数据库列和历史 Provider/专家回流记录暂不删除，避免线上数据丢失；新代码不向这些列写入值。

## 3. 代码和数据库改动

### 3.1 应用层

- `labelu/internal/application/command/ingestion.py`：接入命令不再定义项目维度字段。
- `labelu/internal/application/response/ingestion.py`：作业和媒体资产响应不再输出项目维度字段。
- `labelu/internal/adapter/routers/ingestion.py`：列表和详情接口只接收作业、摘要、扫描状态和分页条件。
- `labelu/internal/adapter/persistence/crud_ingestion.py`：查询、幂等和去重改为创建者作用域；管理人员查询使用显式全局权限。
- `labelu/internal/application/service/ingestion.py`：新作业、新资产不写 `project_id`；上传桥接复用任务权限；删除未使用的任务查询；并发幂等冲突重新读取获胜记录。
- `labelu/internal/domain/models/ingestion.py`：保留历史 `project_id/scene_id` 可空兼容列，新增创建者复合唯一约束和创建者扫描索引。
- `labelu/internal/application/service/provider_worker.py`：ModelVersion 和 MediaAsset 仅在历史对象实际有 `project_id` 时执行兼容校验，去项目化 ModelVersion 不再因访问不存在的旧字段而误失败。

### 3.2 正式迁移

- 新增 `labelu/alembic_labelu/versions/z5a6b7c8d9e0_remove_ingestion_project_scope.py`，父版本为正式 head `z4a5b6c7d8e9`。
- `ingestion_job.project_id` 和 `media_asset.project_id` 改为可空，不删除历史列和历史数据。
- 删除旧项目唯一约束和项目热查询索引。
- 新增 `uq_ingestion_job_creator_idempotency`、`uq_media_asset_creator_sha_media`。
- 新增 `idx_ingestion_job_creator_status`、`idx_media_asset_creator_scan`。
- 迁移执行前检查新唯一键冲突；发现同一创建者下重复幂等键或同摘要媒体时直接失败关闭，不自动合并生产数据。
- SQLite 使用批量重建验证；MySQL 在线 DDL、锁等待、索引执行计划和生产备份恢复仍需在同版本环境演练。
- `downgrade()` 明确失败关闭；生产恢复使用迁移前备份，不自动把历史列恢复为非空。

## 4. 页面、权限与并发影响

- 页面不新增“项目”或“业务空间”字段；第三方 S3 配置页面只配置连接和策略，接入作业/媒体资产列表按创建者、扫描状态和时间分页展示。
- 上传桥接仍从任务详情发起，任务权限是最终边界；不是通过隐藏的项目编号绕过任务权限。
- 接入创建和资产登记继续返回任务编号/资产编号，前端提交按钮必须在请求完成前禁用，重复点击由服务端幂等兜底。
- 唯一键承担最终防重，应用层先查只用于减少冲突；并发冲突统一重新读取已提交记录，不在 Web 进程中执行 S3 下载、Provider 推理或训练。
- 创建者和状态复合索引覆盖作业列表；创建者、扫描状态和生命周期复合索引覆盖资产治理列表；大列表必须分页。
- 历史项目字段保留会增加少量存储和兼容查询成本，但不再参与一期热路径；后续待生产快照确认后再制定历史清理窗口。

## 5. 验证证据

### 5.1 本地通过

```text
python -m pytest labelu/tests/internal/adapter/routers/test_ingestion.py -q --disable-warnings --tb=short
5 passed

python -m pytest labelu/tests/alembic/test_ingestion_no_project_migration.py -q --disable-warnings --tb=short
2 passed

python -m pytest labelu/tests/internal/application/service/test_provider_worker.py labelu/tests/internal/application/service/test_provider_http_simulator.py labelu/tests/internal/application/service/test_legacy_provider_bridge.py -q --disable-warnings --tb=short
14 passed
```

另有 `python -m compileall` 和目标文件 `git diff --check` 通过。

覆盖内容：无项目请求可创建接入作业；缺少幂等键返回 422；同一创建者重复请求只创建一个作业；同一媒体摘要只保留一个资产；跨创建者访问被拒绝；上传会话桥接复用任务权限；SQLite 旧项目约束迁移可重复执行并对重复数据失败关闭；Provider Worker 对去项目化 ModelVersion、历史项目媒体资产和确定性回放均通过。

### 5.2 尚未证明

- 未连接真实生产 MySQL、第三方 S3、生产 Provider、AICM、TPU-MLIR 或 BM1688。
- 未执行生产脱敏快照迁移、MySQL 元数据锁/执行计划、20 并发接入压测和 S3 大批量对象导入。
- 未提交、未推送、未部署，生产当前仍是 RUN-0119 记录的旧路由和旧数据库状态。

## 6. 风险、遗留和下一依赖

1. `ProviderJob/PredictionSet` 旧模型仍保留项目列和项目权限路径；本轮只修复 Worker 对新 ModelVersion/MediaAsset 的字段兼容，完整 Provider 业务去项目化需单独工作包，避免一次迁移同时改动预标注审核状态机。
2. 生产历史接入数据可能存在同一创建者的新唯一键冲突；正式迁移前必须在脱敏快照执行冲突报告，由业务确认保留关系后再迁移。
3. 第三方 S3 自动回流的“核验、去重、分流、触发训练”仍需要真实 S3 包、对象计数、失败样例、首轮训练和 DatasetVersion 证据，不能用本地 SQLite 通过替代。
4. 生产上线前需更新无项目路由、隐藏旧设备任务路径、执行正式迁移、备份和实时只读验收；当前生产验收仍不通过。

## 7. 本轮交付状态

- 代码：本地修改完成，未提交。
- 文档：本记录、`7gai_delivery_ledger.md`、`7gai_role_delivery_plan.md` 和主方案同步。
- 部署：未推送、未部署、未写生产。
- 验收等级：本地 L1；平台整体不得超过 L1。
- 下一步：先完成 ProviderJob/PredictionSet 去项目化分析和生产快照冲突审计，再进入真实 S3 自动回流联调；只有用户明确说“提交代码”后，才进入提交、推送、部署和生产验证流程。
