# RUN-0245 异步评估跨进程条件领取闸门

- 日期：2026-09-20（Asia/Shanghai）
- 责任角色：R03 架构、R04 后端、R06 模型算法、R09 测试、R10 运维
- 工作包：WP2（训练与执行器）、WP5（测试与生产放行）
- 涉及菜单/按钮：训练中心“查看日志/指标”；模型中心“查看模型版本”和“质量证据”
- 验收等级：本地 L1；未连接生产

## 1. 目标与问题

训练完成后的全量评估和下一轮评估在后台线程中运行。原实现主要依赖单进程内存集合，多个后端进程或重启恢复时可能同时把同一作业、同一评估类型置为运行中，造成重复调用视觉模型和重复报告写入。

本轮使用已有 `yolo_train_job.ai_eval_full_status` / `ai_eval_next_status` 字段增加数据库条件更新，不新增迁移：只有状态仍等于读取到的旧值时，才能转换为 `RUNNING`；条件更新影响行数为 0 时，说明其他进程已领取，当前进程退出该评估阶段。

## 2. 改动范围

| 层级 | 文件 | 影响 |
|---|---|---|
| 持久化 | `labelu/internal/adapter/persistence/crud_yolo_train_job.py` | 新增 `update_eval_status_if_current`，按作业、训练完成状态、评估类型和旧状态执行比较并更新 |
| 服务 | `labelu/internal/application/service/yolo_train_evaluation.py` | 自动评估进入运行态前使用条件领取；竞争失败只记录日志并跳过 |
| 测试 | `labelu/tests/internal/application/test_yolo_auto_eval.py` | 验证第一次领取成功、第二次相同旧值领取失败、运行中完成转换成功 |

没有修改表结构、外部 Provider、S3、AICM、BM1688 或页面权限。

## 3. 并发与失败关闭规则

- 单进程内存锁只用于减少本进程重复调度，数据库条件更新才是跨进程真源。
- 领取条件不满足时不调用视觉 Provider，不创建评估报告。
- 数据库异常不转换为成功；由现有异常处理记录失败并保持后续恢复路径。
- 本轮未新增评估执行代次字段，因此不能证明旧进程在恢复后绝对不能写入同一阶段；该风险保留到生产迁移工作包。
- 自动训练、真实 Provider 和生产评估开关保持原有配置，不因本轮本地测试自动打开。

## 4. 本地证据

```text
python -m pytest labelu/tests/internal/application/test_yolo_auto_eval.py labelu/tests/internal/application/service/test_yolo_train_evaluation_contract.py labelu/tests/internal/application/service/test_yolo_train_evaluation_dataset_evidence.py -q --disable-warnings --tb=short
17 passed, 113 warnings

python -m pytest labelu/tests -q --disable-warnings --tb=short
2641 passed, 56 skipped, 410 warnings in 316.17s

python -m py_compile labelu/internal/adapter/persistence/crud_yolo_train_job.py labelu/internal/application/service/yolo_train_evaluation.py
退出码 0

git diff --check
退出码 0
```

未执行真实 Provider、GPU、TPU-MLIR、BM1688、MySQL 并发、S3、AICM、systemd 或浏览器操作；没有请求编号和生产对象 ID，因为本轮只验证本地 SQLite 测试对象。

## 5. 风险与下一依赖

1. 评估报告表仍没有评估执行代次/唯一幂等键；需要在同版本 MySQL 沙箱中设计迁移、唯一约束或结果代次字段，并完成备份恢复和 `2/10/20/50` 并发证据。
2. 真实训练完成后，必须验证多进程恢复只产生一份有效 Provider 结果，且旧进程不能改变新进程终态。
3. 本轮工作树仍未提交、未推送、未部署；生产 Worker 继续保持 `fail-closed`。

下一轮编号：`RUN-0246`。
