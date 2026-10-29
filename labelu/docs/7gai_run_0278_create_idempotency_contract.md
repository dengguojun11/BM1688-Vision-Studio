# RUN-0278 创建任务与训练幂等契约收口（2026-09-20）

## 1. 触发原因

并发专项发现：训练 Worker 的租约只能防止同一个作业被重复执行，不能防止用户在页面重复点击、网络超时后重试，导致重复创建任务或训练作业。真实前端使用的是统一训练入口 `/v1/yolo-training-tasks` 和 `/v1/training/jobs`，因此只修复旧的 `/tasks/{task_id}/yolo-train-jobs` 不足以覆盖生产主流程。

## 2. 本轮交付

| 责任角色 | 工作包 | 交付内容 |
|---|---|---|
| R01 产品 | WP0 | 固化可选 `Idempotency-Key` 语义：不带键保持原有主动新建；同键同请求复用原对象；同键不同请求返回 `409`。 |
| R03 架构 | WP2 | 以“创建者 + 幂等键”作为数据库唯一边界；用请求规范化摘要识别错误复用；保留旧接口兼容。 |
| R04 后端 | WP1/WP2 | 任务创建、旧 YOLO 创建、统一 `/training/jobs`、标签维度 `/yolo-training-tasks` 接入请求头；训练数据版本生成前优先重放；数据库唯一约束和保存点处理并发竞态。 |
| R08 前端/UI | WP1/WP4 | 新建任务和训练弹窗生成一次创建键；同一次网络重试复用；重新打开弹窗或切换训练维度生成新键；请求头使用中文页面不可见的 `Idempotency-Key`。 |
| R09 测试 | WP5 | 增加后端重放、冲突、迁移重复执行和前端请求头契约；回归统一训练页面。 |
| R10 运维 | OPS | 记录生产放行前置：需在同版本 MySQL、真实训练节点、S3、BM1688 和两类账号环境补做并发与对账；本轮不连接生产。 |

## 3. 代码与数据影响

- 新增公共工具：`labelu/internal/common/idempotency.py`，统一键规范化和请求摘要计算。
- `Task`、`YoloTrainJob` 增加 `idempotency_key`、`idempotency_request_hash`，并增加创建者范围唯一约束。
- 新增 Alembic 迁移：`za0b1c2d3e4f_add_create_idempotency.py`；当前本地唯一 head 为 `za0b1c2d3e4f`。
- 后端通过 `Header(..., alias="Idempotency-Key")` 接收请求键，不把键混入业务请求体。
- 前端新增 `src/api/idempotency.ts`、训练创建服务和任务创建服务适配；不带键时仍保持旧的 Axios 两参数调用。
- 没有新增项目/业务空间维度；没有改变已发布模型的外部回滚语义。

## 4. 失败关闭和并发语义

1. 请求键超过 256 个字符返回参数错误。
2. 同一创建者使用相同键但请求摘要不同，返回 `409`，不创建新对象。
3. 两个请求同时首次使用同一键时，数据库唯一约束只允许一个赢家；输家在保存点回滚临时训练任务/作业，不再次派发 Worker，并返回赢家对象。
4. 标签维度训练的训练任务名称唯一冲突与作业唯一冲突均在保存点内处理；无幂等键的用户主动创建仍允许创建相同配置的新作业。
5. 幂等重放不会重新生成 `DatasetVersion`，不会重复冻结样本，不会重复写入训练 Outbox。
6. `AUTO_TRAINING_PLAN_WRITES_ENABLED=False` 时，创建普通任务不会写入 `task.created.v1` Outbox；开启计划写入后才发布该事件，保持一期只读配置的失败关闭语义。

## 5. 验证证据

后端专项：

```text
python -m pytest labelu/tests/internal/application/service/test_training_task.py labelu/tests/internal/adapter/routers/test_yolo_training_task_job_list.py labelu/tests/internal/adapter/routers/test_training.py labelu/tests/internal/application/service/test_yolo_train_create.py labelu/tests/alembic/test_create_idempotency_migration.py -q --disable-warnings --tb=short
32 passed, 160 warnings
```

任务创建与自动训练只读回归：

```text
python -m pytest labelu/tests/internal/adapter/routers/test_task.py labelu/tests/internal/application/test_task_training_ready.py -q --disable-warnings --tb=short
39 passed, 129 warnings
```

角色团队专项汇总：后端统一训练相关测试 `36 passed`；`compileall` 通过；`git diff --check` 通过。

前端专项：

```text
npx vitest run src/api/idempotency.test.ts src/api/services/task.idempotency.test.ts src/api/services/trainingCreate.test.ts
3 个测试文件、5 个测试通过

npx vitest run src/pages/platform/pages/training.test.tsx
30 个测试通过
```

前端 `npm run type-check` 仍只剩仓库既有 `src/pages/404.tsx:29 TS2589`，未发现本轮新增错误。首次误用了不受 Vitest 支持的 `--runInBand` 参数，随后已用正确命令完成验证。

后端全量回归：

```text
python -m pytest labelu/tests -q --disable-warnings --tb=short
2666 passed, 56 skipped, 419 warnings
```

前端生产构建：`npm run build` 成功，4096 个模块转换完成；仅有依赖 `use client`、`eval` 和大包体积警告。

## 6. 真实环境边界与下一依赖

- 本轮仅本地代码、SQLite 测试库和前端测试；未执行生产写请求、正式 Alembic 迁移、AICM/S3 调用、TPU-MLIR 编译或 BM1688 真机验证。
- 生产当前仍是旧部署；不能把本轮本地结果当作生产验收。
- 放行前必须在同版本 MySQL 沙箱完成迁移重复执行、备份恢复、真实锁等待和 `2/10/20/50` 并发；在真实训练节点验证重复点击不重复派发；在生产只读回放中验证两类账号和旧入口退休状态。
- 代码、迁移和文档均未提交、未推送、未部署，等待用户明确“提交代码”。
