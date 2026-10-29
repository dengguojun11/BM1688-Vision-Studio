# RUN-0104 训练结果写入统一 fencing

## 1. 本轮目标

训练状态写入不只发生在独立 Worker：旧 Web/兼容进程仍可能在启动、失败、取消或完成收尾阶段写数据库。只检查带 owner/token 的新路径不够；当作业已被独立 Worker 持有有效租约时，无上下文旧路径也必须拒绝，且检查要在同一事务中靠近最终更新，避免“检查通过后被接管”的窗口。

## 2. 实现内容

- 新增复用的 `_assert_train_result_write_fence()`：
  - 带 owner/token 时，要求 owner、递增 token 和未过期租约全部匹配。
  - 无上下文时，仅在不存在有效执行租约时允许历史路径写入。
  - 无法确认数据库租约时，对真实会话失败关闭；隔离测试使用的非数据库模拟会话保持历史兼容。
- 训练启动、子进程异常失败、用户取消、最终完成和异常失败收尾统一使用该检查。
- 失败收尾在数据库事务内再次检查，避免首次检查后租约接管造成旧状态覆盖。
- 不改变 PT、ONNX、指标内容或模型制品格式，也不新增菜单、用户角色、项目维度、API 或数据库字段。

## 3. 验收

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_stability.py labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings --tb=short
```

结果：`64 passed`，零失败。新增真实隔离 SQLite 行验证：有效租约存在时，旧无上下文结果写入抛出 `TrainExecutionFenceLost`；同时回归嵌套 watchdog、队列、暂停、完成、失败和历史兼容路径。

代码检查：目标文件 `py_compile` 和 `git diff --check` 通过。共享工作树中其他并行改动仍造成 Black 目标文件存在既有格式差异，本轮不做整文件格式化。

## 4. 真实性边界

- 本轮证据为本地 Windows/SQLite，属于 L1；不等于 MySQL 行锁、双进程接管或生产通过。
- 真实 MySQL 双进程、锁等待、systemd 重启、GPU 释放、共享目录、TPU-MLIR、BM1688、S3、AICM、浏览器和生产容量仍未验收。
- 独立 YOLO/车牌 Worker 继续保持 fail-closed，未打开生产消费开关。

## 5. 角色验收

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R04 后端 | 统一训练状态写入 fencing，并在事务内复核 | 本地完成 |
| R06 模型算法 | 确认状态门禁不改变 PT/ONNX/指标契约 | 本地静态确认 |
| R07 边端 | 确认错误终态不会误触发后续 BM1688 编译 | 未连接真实工具链，保留前置项 |
| R09 测试 | 覆盖旧路径拒绝、接管窗口、失败/取消/完成回归 | `64 passed` |
| R03/R10 | 复核数据库与部署边界 | 未执行生产环境操作 |

## 6. 交付状态

本轮为本地 L1，未提交、未推送、未部署。下一依赖仍是同版本 MySQL 双进程接管、真实 GPU/systemd 重启和生产观察证据。
