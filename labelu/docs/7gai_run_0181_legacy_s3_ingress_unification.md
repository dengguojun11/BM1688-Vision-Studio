# RUN-0181 第三方 S3 旧入口统一回流 Worker 门禁

日期：2026-09-20  
状态：本地 L1；未提交、未推送、未部署；未写生产数据

## 1. 目标

核对生产已存在的旧入口 `/api/v1/integrations/strategy-tasks` 与新入口
`/internal/v1/experiment-datasets`，保证第三方问题图片仍通过
`strategy-integration-s3-config` 的 S3 连接读取，但实际下载、去重、按业务聚合、
复用任务、预标注和状态写入全部由第三方 S3 独立 Worker 执行。管理人员只处理
模型缺失、数据异常、下载失败和少量抽查。

## 2. 审计结论

### 2.1 新入口

新入口的 HTTP 请求只调用 `experiment_dataset_import.accept()`，写入
`experiment_dataset_import` 和 `experiment_dataset_group` 的待处理记录。S3
对象列表、`metadata.json` 下载、样本去重、模型 ID 解析、聚合组冲突检查、正式
任务复用、预标注及状态收口均在 `experiment_dataset_import_worker` ->
`run_import_job_now()` 中执行，并受 Worker 租约和 fencing token 保护。

### 2.2 旧入口

旧入口现在是兼容适配器：

1. 校验旧请求幂等性；
2. 将旧字段转换为 `CreateExperimentDatasetImportCommand`；
3. 创建或复用 `StrategyTaskIntegration` 投影记录；
4. 调用同一个 `experiment_dataset_import.accept()`；
5. 返回新包的状态地址 `/internal/v1/experiment-datasets/{package_id}`。

旧入口不再在 HTTP 请求中读取 S3、创建 LabelU 任务、写附件/样本或写预标注。
旧请求携带的 `target.model_id` 原样进入新导入记录，后续由 Worker 按已发布模型
目录中的 AICM `model_set_id` 做解析；模型不存在、被禁用或同一聚合组模型 ID
冲突时进入 `WAITING_FOR_MODEL`，不猜测、不建混合任务。

### 2.3 旧同步实现

文件 `strategy_task_integration.py` 中原 `_legacy_direct_create()` 曾包含完整的
同步 S3 导入链。该实现已改为显式 `410` 失败关闭，防止未来内部误调用绕过：

- 独立 Worker readiness；
- `experiment_dataset_import` 租约/fencing；
- S3 对象和样本去重；
- AICM `model_set_id` 校验；
- 聚合组模型冲突门禁；
- 任务复用与自动预标注。

历史已经完成的旧投影只允许兼容查询，不会因查询再次下载 S3 或创建任务。

## 3. 本轮改动

- `labelu/internal/application/service/strategy_task_integration.py`
  - 删除可执行的旧同步副作用链，替换为 `410` fail-closed 兼容函数。
  - 保留旧入口到统一 `accept()` 的字段转译和投影同步。
- `labelu/tests/internal/application/service/test_legacy_s3_adapter.py`
  - 增加旧同步函数失败关闭测试。
- `labelu/tests/internal/adapter/routers/test_strategy_task_integration.py`
  - 增加旧 HTTP 入口不读 S3、不建任务、只入新 Worker 队列测试。

## 4. 本地证据

```text
python -m pytest \
  labelu/tests/internal/application/service/test_legacy_s3_adapter.py \
  labelu/tests/internal/adapter/routers/test_strategy_task_integration.py \
  -q --disable-warnings --tb=short
16 passed
```

专项覆盖：旧字段转译、标签归一化、旧入口幂等、旧入口状态地址、新旧记录关联、
旧入口不触发 S3，以及旧同步实现 `410` 失败关闭。

## 5. 生产边界和风险

- 尚未连接 `http://113.141.72.175:10000` 的生产 MySQL、S3、AICM 或 systemd。
- 尚未用生产 `strategy-integration-s3-config` 中的真实对象包进行下载、SHA256
  去重、样本数、AICM `model_set_id`、任务 ID 和预标注数量对账。
- 尚未部署，因此生产 OpenAPI 仍可能同时暴露旧入口；旧入口只有在本轮代码部署
  后才具备本记录描述的统一行为。
- Worker 开关、数据库租约列、生产安全配置或 AICM 模型目录未就绪时，Worker
  必须失败关闭；不能通过旧入口改为同步处理。

## 6. 下一依赖

1. 用户明确“提交代码”后，执行代码提交、推送和部署。
2. 部署前完成同版本 MySQL 迁移、20 并发领取/过期接管和 systemd Worker 重启
   接管验证。
3. 用真实 S3 问题包做一次只读/小范围回流对账：连接、对象、重复对象、模型 ID、
   聚合组、复用任务、预标注和异常状态一一对应。
4. 生产验证旧入口重复通知返回同一 `package_id`，且无 Web 进程 S3 读取或任务
   写入；新入口与旧入口最终落在同一导入表和 Worker 状态机。

