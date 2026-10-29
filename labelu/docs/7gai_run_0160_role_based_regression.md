# RUN-0160 角色化交付联合回归与放行边界

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R06 模型算法、R07 BM1688 边端、R09 测试主责，R10 运维协作；对应 WP2/WP3/WP5。
- 本轮目标：按照角色交付计划，对主动学习全局范围、服务探针、训练 Worker、BM1688 编译 Worker 和执行租约 fencing 做联合回归；只记录真实执行结果，不把健康检查或 SQLite 结果扩大为生产通过。

## 1. 实际执行命令与结果

| 验收范围 | 命令 | 结果 |
|---|---|---|
| 主动学习、健康探针、全局范围迁移 | `python -m pytest labelu/tests/internal/adapter/routers/test_active_learning.py labelu/tests/internal/adapter/routers/test_active_learning_global_scope.py labelu/tests/internal/adapter/routers/test_health.py labelu/tests/alembic/test_active_learning_scope_migration.py -q --disable-warnings --tb=short` | `16 passed` |
| 训练 Worker | `python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py -q --disable-warnings --tb=short` | `11 passed` |
| BM1688 编译、Worker、执行租约 | `python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short` | `51 passed` |
| 模型中心、训练中心前端 | `pnpm exec vitest run src/pages/platform/pages/models.test.tsx src/pages/platform/pages/training.test.tsx` | `2` 个测试文件、`41 passed` |
| 前端类型检查 | `pnpm run type-check` | 通过 |
| Python 编译 | `python -m compileall -q labelu/internal labelu/tests` | 通过 |
| 差异空白检查 | `git diff --check` | 通过；仅有既有换行格式提示 |

## 2. 并行启动现象及结论

第一次把三组测试同时启动时，BM1688 多进程专项出现 4 个 `ready_queue` 20 秒超时；同一失败测试单独运行 `1 passed`，随后完整 BM1688 联合套件串行运行 `51 passed`。该现象目前只能判定为 Windows 测试机并行启动/子进程导入竞争窗口，不能作为生产 Worker 已通过的证据，也不能直接修改业务代码掩盖。

R07/R09 需要继续确认测试夹具是否应把固定 20 秒改为带进程退出诊断的可配置启动等待；如果修改，只增强测试诊断和等待策略，不改变 Worker 的领取、租约或 fencing 语义。

## 3. 本轮角色验收结论

| 角色 | 本轮确认 | 结论 |
|---|---|---|
| R03 架构 | 全局作用域、队列租约与执行租约分层，旧 Worker 不能覆盖新 Worker | 本地 L1 通过 |
| R04 后端 | 训练和 BM1688 领取均有条件更新，失败/失租不写错误终态 | 本地 L1 通过 |
| R06 模型算法 | 训练冻结清单和模型制品链未被本轮并发门禁改变 | 本地静态通过，真实训练未验收 |
| R07 BM1688 边端 | Worker 在字段不完整时失败关闭；编译任务保留 PT→静态 ONNX→FP32/FP16/INT8 BModel 关系 | 本地 L1 通过，真实 TPU-MLIR/BM1688 未验收 |
| R08 前端 | 模型中心/训练中心 `41 passed`，类型检查通过；保留既有 React/Ant Design/jsdom 警告 | 本地 L1 通过 |
| R09 测试 | 三组后端/边端专项串行联合回归通过；并行启动现象待专项增强诊断 | 本地 L1 通过 |
| R10 运维 | 未执行 MySQL、systemd、GPU、板卡或生产 Worker | 生产验收未通过，不得开启开关 |

## 4. 未完成与下一工作包

1. 在同版本 MySQL 沙箱执行 2/10/20 并发领取、锁等待、过期接管和旧 token 迟到写入；保存执行计划和数据库证据。
2. 在脱敏生产快照上完成正式八段迁移演练、唯一 `alembic_version`、表/列/索引/外键/行数对账及备份恢复。
3. 在真实 Ubuntu/systemd、训练节点和 BM1688 上验证重启接管、子进程失租终止、GPU/板卡释放和三精度 BModel 加载推理。
4. 用真实第三方 S3 包核对 `metadata.json`、图片对象、AICM `model_set_id`、本平台模型目录和回流任务样本血缘。
5. 用户明确“提交代码”前，本轮及前序变更不提交、不推送、不部署；生产 Worker 与自动训练计划继续保持关闭。

## 5. 交付等级

本轮交付等级为本地 L1。它证明代码路径和本地专项回归具备继续演练的条件，不代表预生产或生产闭环已经完成。
