# RUN-0103 独立训练 Worker 嵌套子进程收尾 fencing

## 1. 本轮目标

补齐独立 YOLO/车牌训练 Worker 的第二层子进程边界。大图或多卡训练会由训练子进程再启动一个训练进程；该进程发生启动卡死、异常退出或被租约接管后，不能使用无 owner/token 的历史失败收尾逻辑覆盖新执行者。

本轮只修改本地代码和测试，不打开独立 YOLO/车牌 Worker 生产开关，不新增菜单、用户角色、项目维度或数据库字段。

## 2. 实现内容

- `_run_spawned_yolo_train_with_watchdog()` 接收并透传 `execution_owner`、`execution_fencing_token`。
- 嵌套训练进程启动卡死时，`_mark_spawn_child_failed()` 先按 owner/token 和未过期租约确认；失租、旧 token 或数据库不可确认时只记录“忽略旧失败回传”，不写阶段和失败状态。
- 嵌套训练进程退出收尾 `_handle_spawn_child_exit()` 透传同一执行上下文；队列层在有上下文时调用带 fencing 参数的生命周期入口，无上下文时保持历史两参数兼容。
- 正常无租约的 Web 历史路径保持原行为，避免改动既有手工作业兼容性。

## 3. 验收

测试命令：

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_stability.py labelu/tests/internal/application/service/test_yolo_train_queue.py -q
```

结果：`54 passed`，零失败。覆盖训练 watchdog 上下文透传、失租失败收尾拒绝、队列子进程退出、暂停、完成、历史无 fencing 调用兼容。

代码检查：目标 Python 文件编译通过，`git diff --check` 通过。共享工作树中的三个目标文件仍有此前其他改动带来的 Black 格式差异；本轮未对整文件执行格式化，避免覆盖并行工作包。

## 4. 真实性与风险边界

- 当前仅为 Windows/SQLite/模拟进程的本地 L1 证据。
- 尚未证明同版本 MySQL 锁等待、真实多卡 GPU、systemd 重启、共享目录残留清理、真实训练节点、TPU-MLIR、BM1688、S3、AICM 或生产浏览器行为。
- 独立 YOLO/车牌 Worker 仍保持 fail-closed；生产继续使用既有兼容路径，直到真实 MySQL 双进程、重启接管、GPU 释放和生产观察完成。

## 5. 角色验收

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R04 后端 | 透传嵌套子进程 owner/token，阻断失租失败写入 | 本地完成 |
| R06 模型算法 | 确认 watchdog/异常收尾不改变 PT、指标和制品契约 | 本地静态确认 |
| R07 边端 | 评估失租训练收尾对后续 ONNX/BModel 输入的影响 | 未连接真实编译节点，保留生产前置项 |
| R09 测试 | 正常、异常、失租和兼容路径专项回归 | `54 passed` |
| R03/R10 | 复核迁移、MySQL、进程和部署边界 | 本轮未执行生产环境操作 |

## 6. 交付状态

本轮为本地 L1，未提交、未推送、未部署。下一依赖是同版本 MySQL 双进程接管、真实多卡训练节点和 systemd 重启恢复证据。
