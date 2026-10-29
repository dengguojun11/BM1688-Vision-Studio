# RUN-0131 训练独立 Worker 未就绪时的消费路径失败关闭

## 1. 本轮目标

按生产执行器隔离原则，消除“独立训练 Worker 开关已打开但前置条件不满足时，Web 进程继续启动旧线程”的双消费者风险。训练请求仍然可以创建并进入持久化队列，但只能由独立 Worker 领取；Worker 未就绪时不得由 Web 进程兜底执行。

本轮不新增登录角色、一级菜单、项目/业务空间、设备发布或模型回滚，不改变远程训练路径。

## 2. 代码变更

- `labelu/internal/application/service/yolo_train_queue.py`
  - 本地 YOLO/车牌训练在 `YOLO_TRAIN_DEDICATED_WORKER_ENABLED=true` 时统一检查独立 Worker 就绪状态。
  - Worker 已就绪：请求只写入持久队列，等待租约领取。
  - Worker 未就绪：请求仍保持队列状态并记录运维修复原因，直接返回，不启动 Web 线程。
  - 远程训练 `execution_mode=remote` 保持原有远程节点路径，不被本地 Worker 开关拦截。
  - 队列派发、服务重启数据库恢复和 Web 训练看门狗在独立 Worker 开关打开时全部停止消费；队列记录保留，待 Worker 恢复后继续处理。

## 3. 业务与运维含义

“开关打开”代表生产已经选择独立 Worker 作为唯一消费者，不再允许自动降级为 Web 线程。这样即使 Worker 部署失败、迁移未完成、租约字段缺失或数据库不可用，也只会表现为作业排队和明确告警，不会出现同一作业被两个进程同时训练、GPU 争抢或旧进程覆盖新状态。

管理人员的处理动作是恢复 Worker、完成数据库/配置/节点检查后再重试；不是在页面上手动切换第二套训练路径。

## 4. 本地验证证据

- `python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_queue.py -q`
  - `53 passed`
- 新增覆盖：Worker 未就绪时训练请求不启动 Web 训练、内存队列保留、数据库恢复停止、Web 看门狗不启动。
- `python -m py_compile labelu/internal/application/service/yolo_train_queue.py` 通过。
- `git diff --check` 通过（工作区其他历史改动仍会产生换行提示，不代表本轮失败）。

## 5. 验收等级与未完成项

- 当前：本地 L1。
- 尚未证明：Ubuntu systemd 实际启停、同版本 MySQL 双进程/锁等待、真实 GPU 训练、Worker 重启接管、生产部署和浏览器验收。
- 因此本轮不得把 `YOLO_TRAIN_DEDICATED_WORKER_ENABLED` 在生产打开，也不得把本地 `53 passed` 写成生产训练完成。

## 6. 角色交付

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R03 架构 | 定义唯一消费者和失败关闭边界 | 完成 |
| R04 后端 | 训练请求、派发、恢复、看门狗统一门禁 | 完成 |
| R06 模型算法 | 确认不改变 PT、标签、数据版本和训练语义 | 完成 |
| R09 测试 | 队列、失败关闭和既有训练回归 | `53 passed` |
| R10 运维 | 明确 Worker 未就绪时的恢复动作和生产开关要求 | 本地文档完成，真实 systemd 待验收 |

## 7. 提交与部署状态

本轮未提交、未推送、未部署，未修改生产数据库，等待用户明确“提交代码”。
