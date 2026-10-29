# RUN-0075 边端执行期租约终态事务闭环

## 1. 范围

- 日期：2026-09-18，Asia/Shanghai。
- 目标：使用真实`YoloEdgeValidationJob`执行租约字段完成领取、条件写入、终态提交和释放，不再由测试强制走旧库兼容路径。
- 角色与工作包：WP3边端验证；R03、R04、R07主责，R09验收。
- 不连接生产，不执行真实SSH，不提交、不部署。

## 2. 复现与根因

移除业务流测试中的`ExecutionFencingUnavailable`模拟后，完整任务稳定停留在`PROCESSING`。租约领取本身成功，但以下写入没有显式提交：

1. 启动时间和初始进度；
2. 成功状态、执行结果和最终进度；
3. 异常状态、错误结果和最终进度。

这些写入复用了业务Session，但`session_scope()`只负责关闭，不自动提交，因此退出时被回滚。问题不在BM1688设备、SSH或租约领取算法，而在调用方事务边界。

## 3. 实现决定

- 三处执行租约分支均使用`begin_transaction(db)`。
- 进度与对应状态/结果位于同一事务，避免出现状态已变但证据未写入，或进度已写但终态仍为`PROCESSING`。
- 条件写入继续校验`id + owner + fencing token + 未过期`，失租worker不能覆盖新worker。
- 完成后释放owner，保留过期时间和递增token作为审计证据。
- 旧pytest库补齐execution三列，仅服务跳过迁移的测试环境，不代表生产DDL完成。

## 4. 页面、接口和数据影响

- 页面和按钮：无变化。
- HTTP接口和权限：无变化。
- 业务表结构：本轮不修改正式迁移；生产结构仍等待第08段正式迁移。
- 数据行为：边端任务初始进度、成功/失败终态和结果现在能够真实提交；完成租约不会继续占用。

## 5. 验证

- 失败复现：真实fencing业务流`1 failed`，状态为`PROCESSING`。
- 修复验证：同一业务流`1 passed`。
- 联合专项：使用全新隔离库`run-0075-final-suite.db`执行execution fencing、样本包、调度、自动训练派发、边端路由和完整业务流，`76 passed`、零失败。
- 断言证据：`COMPLETED`、最终`progress.stage=finalize`、`execution_lease_owner=None`、expires存在、token为1。

## 6. 未完成项

- 未验证执行中失租、SSH异常和结果导入异常的完整业务终态。
- 未执行MySQL条件更新、锁等待、双进程接管和20并发。
- 未执行真实TPU-MLIR、BM1688、S3/AICM或生产数据库。
- 当前只能标记本地L1；下一轮从`RUN-0076`继续。
