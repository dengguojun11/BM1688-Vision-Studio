# RUN-0121 Provider 预标注作业去项目化收口

## 1. 轮次信息

- 日期和时区：2026-09-19，Asia/Shanghai。
- 工作包：WP1 任务与预标注、WP4 第三方回流。
- 角色：R02 标注与数据契约、R04 后端业务与权限主责，R05 外部平台与回流、R06 模型算法、R09 测试协作。
- 关联方案：主方案第 7 章任务与预标注、第 10 章数据回流、第 15 章验收与并发。
- 前置轮次：RUN-0120 第三方 S3 接入去项目化。

## 2. 本轮目标

把 Provider 预标注作业从“项目作用域”收口为“创建者 + 任务权限”作用域，并保证历史数据库记录仍可读取。Provider 作业、预测结果、审核审计和一次性投影不再要求新请求携带项目编号；历史 `project_id` 只作为可空兼容字段保留。

## 3. 代码与数据变化

### 3.1 接口和权限

- `CreateProviderJobCommand` 删除项目字段；创建接口只接收 Provider、模型版本、媒体资产、契约和幂等键。
- Provider 作业列表、详情、取消和预测结果接口不接收项目查询参数，也不返回项目字段。
- Provider 注册表列表、详情和运行检查同样不接收项目查询参数；Provider 能力是平台级目录，只由管理人员维护，不挂在项目或业务空间下。
- 作业和预测结果按创建者校验；投影到任务预标注前按任务可见性校验；管理人员保留全局处理能力。
- 媒体资产按创建者所有权和扫描通过状态校验；任务样本投影使用任务权限，不把历史项目成员关系当作新权限。

### 3.2 执行器与模型血缘

- Provider Worker 继续复用独立队列、租约和结果幂等写入。
- Worker 对历史 `project_id` 只做兼容读取，不再用 `job.project_id` 与 ModelVersion 比较。
- 新作业没有项目字段时，只要 ModelVersion 存在且请求权限已通过，就能正常执行并把模型编号写入结果血缘。
- 预测结果仍保存 Provider、版本、模型版本、输入资产摘要和结果摘要；人工正式标注与机器预测分开保存。

### 3.3 数据库迁移

- 正式迁移：`z6b7c8d9e0f1_remove_provider_project_scope.py`。
- `provider_job.created_by + request_signature` 成为新幂等唯一键。
- Provider 作业、预测集、审核审计和投影表的历史项目列改为可空；项目索引替换为创建者/审核状态或样本时间索引。
- 迁移发现创建者与幂等键冲突时失败关闭，不自动合并历史结果。
- 迁移降级不伪造已发布模型回滚；恢复必须使用迁移前数据库备份。

## 4. 测试证据

执行命令：

```text
python -m pytest labelu/tests/internal/application/service/test_provider_worker.py labelu/tests/internal/adapter/routers/test_provider_job.py labelu/tests/internal/application/service/test_provider_http_simulator.py labelu/tests/internal/application/service/test_legacy_provider_bridge.py labelu/tests/alembic/test_provider_no_project_migration.py -q
```

结果：Provider 作业、注册表、Worker、HTTP 模拟、历史桥接、适配器和迁移专项 `52 passed`，零失败。新增用例验证作业 `project_id` 为空时仍可使用 ModelVersion，重复版本提交不同结果会返回冲突，队列等待期间资产扫描/生命周期变化会被 Worker 拒绝，注册表携带历史项目查询参数也不会进入项目权限分支。`python -m alembic -c labelu/alembic_labelu/alembic.ini heads` 结果为唯一 head `z6b7c8d9e0f1`；`compileall` 和 `git diff --check` 通过。

## 5. 并发、性能和生产边界

- 创建作业通过数据库唯一键和冲突重查保证重复请求只产生一条作业；Worker 结果通过作业版本唯一键和租约所有者条件写入，避免重复 PredictionSet。
- 本轮没有改变 Web 进程与独立 Provider Worker 的隔离方式，也没有增加同步推理线程。
- 目前只完成本地隔离数据库和代码级回归，未完成真实 MySQL 双进程、真实 Provider 服务、生产 S3、AICM、TPU-MLIR、BM1688 或生产规模并发验证。
- 生产地址 `http://113.141.72.175:10000` 尚未部署 `z6b7c8d9e0f1`，不能把本轮本地通过写成生产通过。

## 6. 验收结论与下一步

- 本轮达到本地 L1：接口、权限、历史兼容、模型血缘和迁移专项闭合。
- R05/R09 下一步在预生产验证一个真实 Provider 预标注批次：重复通知、失败重试、人工审核和任务投影全链路只产生一次有效结果。
- R03/R10 下一步在同版本 MySQL 沙箱演练 `z5a6b7c8d9e0 -> z6b7c8d9e0f1`，核对锁等待、索引、外键和历史数据对账。
- 在真实 S3、AICM、TPU-MLIR、BM1688 和生产证据完成前，平台整体仍保持本地 L1。

## 7. 交付状态

未提交、未推送、未部署，等待用户明确说“提交代码”。

## 8. Provider 注册表残留项目契约收口

### 8.1 边界结论

Provider 注册表是平台级“预标注能力目录”，只描述 Provider 标识、版本、运行时、支持媒体/芯片和证据门禁，不承载任务、样本或数据隔离。因此 `project_id` 不属于注册表业务权限，不能继续作为查询参数或权限检查入口。历史数据库中的项目列（如其他旧业务表仍存在）不影响注册表读取；新注册表 API 不再接受或转发项目编号。

### 8.2 代码与页面收口

- `labelu/internal/adapter/routers/provider_registry.py`：列表和详情移除 `project_id` 查询参数。
- `labelu/internal/application/service/provider_registry.py`：移除 `crud_project` 依赖和 `_project_access` 分支，Provider 列表/详情只按目录状态、能力和版本查询；证据更新仍仅限管理人员。
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/providers.tsx`：Provider 目录请求不再发送 `project_id`，保留分页和状态筛选；`needsProject=false` 与请求参数一致。
- `labelu/tests/internal/adapter/routers/test_provider_registry.py`：旧“项目成员权限”测试改为验证历史 `project_id` 查询不会进入项目权限分支且响应不返回项目字段。

### 8.3 证据

- 后端 Provider 注册表路由回归：`11 passed`。
- 前端 Provider 页面回归：`pnpm --filter @labelu/frontend exec vitest run src/pages/platform/pages/providers.test.tsx --reporter=dot`，`2 passed`；测试输出包含既有 React/Ant Design/jsdom 环境警告，无失败。
- OpenAPI 静态检查：`GET /api/v1/providers` 参数为 `status、capability、page、size`；详情参数为 `provider_key、provider_version`，均无 `project_id`。
- 当前仍未连接生产地址，未修改生产数据库、S3、AICM 或 Provider 运行时；本轮只达到本地 L1。

### 8.4 兼容和风险

- 客户端若继续传递未知 `project_id`，FastAPI 默认会忽略额外查询参数；服务端不会据此做项目鉴权。新客户端必须删除该参数，避免误以为 Provider 目录具有项目隔离。
- 这次只收口 Provider 注册表，未把其他仍有真实业务归属的设备、规则、主动学习和历史流程表一次性去项目化；它们必须按各自工作包和数据权限单独审计。
- 生产部署前必须重新核对前端构建产物和真实 `/openapi.json`；未部署前不能把本地 OpenAPI 结果写成生产已收口。
