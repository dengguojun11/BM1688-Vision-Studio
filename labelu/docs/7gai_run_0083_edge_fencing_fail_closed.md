# RUN-0083 边端租约不可用时失败关闭复用与状态回队（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章 BM1688 编译与真机验证、第11章并发与任务执行、第15.7节验收分层；执行契约 `EDGE-EXEC-FENCING-FAIL-CLOSED-01`。
- 工作包/角色：WP3 边端验证；R07 边端算法主责，R04 后端、R03 架构、R09 测试协作。
- 本轮目标：当生产数据库尚未完成执行租约字段迁移，或执行租约字段暂不可用时，边端 Worker 必须在 SSH 前失败关闭，并把任务安全退回队列，释放已分配设备，避免无 fencing 的设备副作用。
- 实现内容：在 `yolo_edge_validation.py` 新增 `_requeue_when_execution_fencing_unavailable()` 复用函数；`ExecutionFencingUnavailable` 分支统一调用该函数。任务状态回到 `PENDING`，`queue_reason=execution_fencing_unavailable`，清空设备地址、端口、用户和执行租约字段，保留中文等待迁移说明；未调用 SSH、上传、远端清理或结果回写。
- 同步收口样本包门禁：普通选样包的真实 `skipped`、复制数不足和清单摘要不一致会阻断 `READY`；旧训练 `test/val` 包允许采样截断，但通过 `blocking_skipped_count` 区分提示性未选样本与阻断性跳过，阻断包不会上传或进入 SSH/BM1688 执行。
- 调度器前门同步复用严格清单门禁：显式 `READY` 但清单损坏、摘要不一致或存在阻断性跳过的包，在设备分配前即被拒绝，不会占用边端设备；没有样本包的历史手工任务继续按兼容路径回队。
- 测试命令及结果：
  - `python -m pytest labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short`：`6 passed`。
  - `python -m pytest labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py labelu/tests/internal/application/service/test_yolo_edge_validation_scheduler.py -q --disable-warnings --tb=short`：`13 passed`。
  - 样本包与边端业务流专项：`14 passed`，覆盖普通包跳过拒绝、摘要篡改拒绝、旧评估包采样区分和阻断门禁。
  - 样本包、调度器与边端业务流回归：`22 passed`。
  - `py_compile` 和 `git diff --check` 在本轮收口时执行。
- 权限、并发、失败重试、日志监控：不新增菜单、按钮、角色和接口；失败关闭日志带任务编号；任务可由既有调度器重新领取；释放设备避免同一设备被错误占用；不自动回滚已发布模型。
- 本地、预生产、生产证据：仅本地 SQLite、SQLAlchemy 真实事务和模拟执行路径；未连接生产数据库、未执行真实 SSH/BM1688、未执行 MySQL 锁等待或迁移窗口验证。
- 风险与下一依赖：正式八段迁移仍需基于生产脱敏快照完成；在生产字段、索引和双进程接管证据完成前，边端和独立训练 Worker 继续保持 fail-closed。真实 BM1688、S3、AICM、浏览器和生产小流量仍未验收。
- commit/push/deploy 状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4 结论：失败关闭与状态回队达到本地 L1；不代表生产迁移、真机执行或平台整体放行。
