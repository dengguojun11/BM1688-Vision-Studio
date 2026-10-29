# RUN-0092 训练子进程退出收尾 fencing

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第 8 章训练中心、第 14.6.3 节后台执行器与租约；训练状态一致性契约 `TRAIN-STATE-FENCE-02`。
- 工作包/角色：WP2 训练与执行器；R04 后端主责，R06 模型算法、R09 测试协作。
- 本轮目标：补齐 YOLO/车牌训练父进程在子进程退出后的生命周期收尾写入 fencing，避免旧 owner 在租约刚被接管的窗口写入 FAILED/终态。

## 审计结论

独立 Worker 已在父进程等待子进程时通过 `lease_check` 终止失去租约的进程，并在 Worker 外层对失败/运行时字段使用条件写入。此前 `yolo_train_queue.py` 的两个兼容子进程入口在检测到子进程退出后直接调用 `_handle_spawn_child_exit`；如果租约在退出与该调用之间被接管，旧 owner 仍可能触发状态收尾写入。训练子进程的旧 Web 路径没有 lease callback，因此必须保持原有无 callback 行为。

## 变更范围

1. 新增 `_handle_spawn_child_exit_with_lease()`，在调用生命周期状态处理器前复核 `lease_check`；复核失败时向 Worker 外层抛出异常，不执行旧 owner 的收尾写入。
2. YOLO 与车牌子进程入口统一使用该 helper；无 `lease_check` 时仍调用既有 `_handle_spawn_child_exit`，兼容旧 Web 训练路径。
3. 新增参数化回归，覆盖 YOLO 正常退出和车牌异常退出两条路径的 stale token 拒绝；保留既有无 callback 成功退出测试。

## 本地证据

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings --tb=short
35 passed, 112 warnings

python -m py_compile labelu/internal/application/service/yolo_train_queue.py
通过

git diff --check -- labelu/internal/application/service/yolo_train_queue.py labelu/tests/internal/application/service/test_yolo_train_queue.py
通过（仅存在 Git 的 LF/CRLF 转换提示）
```

## 真实性边界

- 本轮只保护父进程在子进程退出后的状态收尾窗口；训练子进程内部的实时进度、权重、评估和完成状态写入仍未全部接入 owner/token 条件更新。
- 未打开 `YOLO_TRAIN_DEDICATED_WORKER_ENABLED`，未改变旧 Web 消费路径，未连接生产 MySQL/GPU、未执行真实训练、未提交、未推送、未部署。
- 由于工作树含有其他并行改动，本轮未宣称全量回归；专项测试仅验证本文件列出的子进程收尾契约。

## 后续验收

1. 将 token/owner 传入训练子进程内部的状态、进度、权重与评估写入点，并补跨进程接管测试。
2. 在同版本 MySQL 快照上验证租约接管、旧进程终止和状态不回退。
3. 取得用户明确“提交代码”后再提交、部署和做生产在线验证。
