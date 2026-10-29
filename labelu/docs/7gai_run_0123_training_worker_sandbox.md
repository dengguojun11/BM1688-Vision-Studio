# RUN-0123 训练 Worker 生产沙箱门禁（本地 L1）

## 1. 轮次与目标

- 日期和时区：2026-09-19，Asia/Shanghai。
- 工作包：WP2 训练执行器、WP5 测试验收。
- 角色：R04 后端主责，R03 架构、R06 模型算法、R09 测试、R10 运维协作。
- 目标：把训练 Worker 从“代码存在”核对到“可以安全进入受控沙箱”的最低门禁，明确哪些条件不足时必须保持 Web 旧路径或完全不消费。

## 2. 生产沙箱定义

本轮的“沙箱”不是生产训练，也不是模拟 GPU 已通过，而是隔离数据库、模拟训练 Runner 和显式开关下的执行契约演练。它只允许验证：

1. 两个 Worker 竞争同一 `PENDING` 作业时只有一个 queue lease；过期后可以接管。
2. queue lease 成功后必须再领取 execution lease/fencing token；字段或迁移缺失时失败关闭。
3. 作业进入 `PROCESSING` 前要完成 GPU 槽位检查；资源不足释放租约并回到可重试状态。
4. 训练 Runner 接收 `lease_check`、运行进程号、owner 和 fencing token；旧 owner 不能覆盖新 owner 的终态。
5. 训练异常、取消、失租和正常退出都释放 GPU/租约；失败写入受当前 token 条件保护。

## 3. 现有代码与页面边界

- 页面入口仍是“任务与标注 → 任务详情 → 新建训练”弹窗；本轮不新增菜单、按钮或登录角色。
- `labelu/scripts/yolo_train_worker.py` 只作为受控执行入口，默认配置关闭。
- `dedicated_worker_ready()` 同时检查开关、训练总开关、ORM/数据库执行租约字段和批量上限；即使字段齐全，当前仍返回“训练子进程状态写入尚未接入执行租约，独立切换不可用”。
- Web 进程不会在开关关闭时启动独立 Worker；独立 Worker 未达到就绪条件时不领取任何作业，不产生训练副作用。
- 训练 Worker 使用短期 queue lease 做派发交接，使用 execution lease/fencing token 保护实际训练期间的状态、阶段文件和制品写入，两者不能合并。

## 4. 权限、并发和失败处理

- 标注人员和管理人员都可以从其有权任务创建训练；Worker 不绕过任务可见性和数据版本权限。
- 同一作业的领取使用数据库条件更新；不能用先查询再更新的内存锁替代。
- 旧 Worker 失租后只能停止后续副作用，不能写失败、完成、权重或进程号；新 Worker 接管时 fencing token 必须递增。
- 未完成正式迁移、无法确认 GPU、无法确认执行租约或 Runner 签名不兼容时均 fail-closed；不自动回退到未加 fencing 的独立路径。
- 日志至少记录作业编号、queue owner、execution owner、fencing token、节点、GPU 槽位、阶段和失败原因；不记录密钥和完整标注内容。

## 5. 本地验证

执行命令：

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py -q --disable-warnings --tb=short
python -m pytest labelu/tests/internal/application/service/test_training_worker_runtime.py labelu/tests/internal/application/service/test_training_worker_persistence.py -q --disable-warnings --tb=short
python -m compileall -q labelu/internal labelu/scripts/yolo_train_worker.py
git diff --check
```

本轮验收结果：训练 Worker 领取/过期接管、默认关闭、Runner 上下文透传和旧 token 拒绝 `10 passed`；通用 Worker Outbox 运行时与持久化 `25 passed`；编译和差异检查通过。命令结果只作为本地 L1 证据，不等价于真实 GPU 或 MySQL。

## 6. 尚未完成的生产证据

- 没有同版本 MySQL 脱敏快照的双进程锁等待、20 并发和执行计划证据。
- 没有真实 GPU/远程训练节点、训练长任务、节点掉电或 systemd 重启接管证据。
- YOLO/车牌训练子进程的全部状态、进度、权重、评估和复合组件制品尚未在真实执行器上逐项核对 fencing。
- 因此 `YOLO_TRAIN_DEDICATED_WORKER_ENABLED` 必须保持关闭，不能部署为生产独立消费者；当前生产仍以旧 Web 路径为准，直到 RUN-0124 迁移和 RUN-0125 工具链门禁完成。

## 7. 恢复办法与交付状态

- 沙箱演练失败只清理隔离 SQLite 和临时工作目录，不触碰生产库、生产媒体或已发布模型。
- 训练 Worker 切换失败时保持开关关闭；不需要模型回滚。若已发布模型发现问题，沿用人工关闭本平台下载并发布替代版本。
- 未提交、未推送、未部署，等待用户明确说“提交代码”。本轮达到本地 L1，L2/L3/L4 未完成。
