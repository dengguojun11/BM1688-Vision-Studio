# RUN-0280 第三方 S3 回流副作用租约守卫收口

日期：2026-09-20（Asia/Shanghai）  
责任角色：R03 架构、R04 后端、R05 外部平台、R09 测试、R10 运维  
工作包：WP2（并发与状态真源）、WP4（第三方 S3 回流）、WP5（测试与生产放行）  
验收等级：本地 L1；未提交、未推送、未部署；未写生产

## 1. 触发原因与需求编号

并发审计发现第三方 S3 回流 Worker 的 fencing 只约束
`ExperimentDatasetImport` 最终状态写入。Worker 在下载或处理一个聚合组期间如果租约
失效，仍可能继续创建任务、附件、样本、预标注或自动预标注作业；租约已经自然过期但
尚未被新 Worker 接管时，旧 owner/token 也仍可通过原条件更新写入状态。该行为不符合
一期“失租立即停止副作用、旧执行者不能覆盖新代次”的失败关闭要求。

需求编号：`S3-LEASE-07`（失租副作用停止）、`S3-LEASE-08`（过期写拒绝）、
`S3-CONC-03`（并发接管后旧代隔离）。

## 2. 本轮交付

| 角色 | 工作包 | 交付内容 |
|---|---|---|
| R03 架构 | WP2 | 定义回流 Worker 守卫：每次检查必须同时满足 owner、fencing token 和 `worker_lease_expires_at > 当前时间`；失租异常不能进入失败终态。 |
| R04 后端 | WP4 | 新增 `worker_lease_is_valid`；`update_by_worker_lease` 增加未过期条件；group/row 处理接收 `lease_guard`，在每包、每样本及任务、附件、样本、预标注、自动预标注和旧投影副作用前检查。 |
| R05 外部平台 | WP4 | S3 下载、metadata 解析、模型 ID 对齐路径继续由独立 Worker 执行；失租时不继续消费第三方包，也不伪造完成或失败状态。 |
| R09 测试 | WP5 | 增加“租约过期但未接管时旧代写入拒绝”和“第一样本完成后失租，第二样本不再创建附件”的回归测试。 |
| R10 运维 | OPS | 保持 Worker 开关、正式迁移、生产写入和部署失败关闭；本轮只使用本地 SQLite/测试夹具。 |

## 3. 修改文件与行为影响

- `labelu/internal/adapter/persistence/crud_experiment_dataset_import.py`
  - `update_by_worker_lease` 增加非空且未过期条件；过期 owner/token 即使尚未被接管也不能写入。
  - 新增 `worker_lease_is_valid` 供处理过程读取当前数据库租约。
- `labelu/internal/application/service/experiment_dataset_import.py`
  - 新增 `ImportWorkerLeaseLost`、`LeaseGuard` 和统一守卫函数。
  - `_process_import_group`、`_process_import_row` 接收守卫。
  - 在读取/下载每个包、处理每个样本、创建或复用任务、写附件、挂载样本、写预标注、
    创建自动预标注作业和更新旧兼容投影前检查。
  - 失租不调用 `_mark_group_failed`，不写 `FAILED` 或完成状态；由新代次 Worker 接管后
    继续处理。
- `labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py`
  - 覆盖过期未接管写拒绝。
- `labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py`
  - 覆盖中途失租后停止后续样本副作用。

本轮不新增表、不新增迁移、不改变菜单/API 请求格式、不恢复项目/业务空间，不改变已发布
模型只能禁止后续下载而不能外部回滚的规则。

## 4. 并发与失败关闭语义

1. Worker 领取后每个副作用边界重新读取数据库租约，不依赖处理开始时的 ORM 快照。
2. owner/token 不匹配或租约已过期时抛出 `ImportWorkerLeaseLost`，当前代次立即退出。
   租约查询本身发生数据库异常也按失租处理，不写入错误终态。
3. 失租异常不会把聚合组或包标成 `FAILED`，不会发送完成/失败的错误终态；过期租约由新 Worker
   按 fencing token 接管。
4. 最终状态更新仍使用带 owner/token/未过期条件的条件更新；旧代次的迟到写入返回未匹配，
   不覆盖新代次。
5. 本轮只解决 Worker 状态与导入副作用的本地门禁；同名图片跨包内容 SHA256 去重、真实
   MySQL 锁等待和 S3/AICM 三方对账仍是独立遗留项。

## 5. 本地验证

已完成：

```text
python -m pytest labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q --disable-warnings --tb=short
11 passed, 170.96s

python -m pytest labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py::TestExperimentDatasetImportRouter::test_row_processing_stops_before_late_side_effect_after_lease_loss labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py::test_expired_worker_generation_cannot_write_before_takeover -q --disable-warnings --tb=short
2 passed, 113 warnings, 3.32s
```

其中第一条由 R09/R10 独立复核，覆盖回流领取、租约接管和 2/10/20 并发竞争；第二条覆盖
本轮新增的过期写拒绝和失租中途副作用守卫。

## 6. 真实环境证据与风险

- 未连接生产 MySQL、第三方 S3、AICM、真实 Worker、systemd、GPU、TPU-MLIR 或 BM1688。
- 生产只读证据 `.tmp/7gai-production-readonly-route-audit-0275.json` 显示未登录访问
  `/api/v1/strategy-integration-s3-config` 为 `401`，无法在未授权情况下核实当前连接、积压和
  Worker 状态；本轮不把生产配置猜测为已验证。
- SQLite 测试不能替代同版本 MySQL 的行锁、事务隔离、长下载、进程崩溃接管和磁盘配额演练。
- 真实放行仍需在预生产/生产使用脱敏 S3 包完成：重复通知、重复图片、模型 ID、任务复用、
  失租接管、失败重试、旧入口统一和 AICM `model_set_id` 三方对账。

## 7. 状态与下一依赖

本轮代码与文档仅在本地工作树，未提交、未推送、未部署，等待用户明确“提交代码”。下一依赖：

1. 在同版本 MySQL 沙箱完成过期写、双 Worker 接管和 `2/10/20/50` 并发证据；
2. 由 R10 取得授权管理账号后，对生产 S3 配置、Worker 开关和历史积压执行只读回放；
3. 用一个真实第三方问题包完成对象、metadata、AICM `model_set_id`、任务/样本/预标注和状态
   的逐项对账；
4. 生产部署前保持 `EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED=false` 或等价失败关闭配置。
