# RUN-0133 BM1688 恢复扫描改为独立 Worker 唯一消费

## 1. 本轮目标

修复 BM1688 编译恢复扫描绕过独立 Worker 的路径。手工导出和重编译入口已经只入队，本轮补齐部署重启后的自动恢复入口，确保 `LABELU_BM1688_DEDICATED_WORKER_ENABLED=true` 时 Web 进程不直接运行编译函数。

## 2. 代码变更

- `labelu/internal/application/service/yolo_bm1688_export_auto_resume.py`
  - 增加独立 BM1688 Worker 开关判断。
  - `PENDING` 导出任务保持原状态，等待 Worker 领取。
  - 已经是 `PROCESSING` 且执行租约已失效的任务，在事务内回到 `PENDING`，保留 `worker_action`（`export` 或 `recompile`），之后由 Worker 重新领取。
  - 独立 Worker 开启时不再创建 Web 编译线程。
  - 有效 execution lease 的任务仍由原有租约门禁保护，不会被恢复扫描改写。

## 3. 本地验证证据

- `python -u -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py::test_maybe_auto_resume_returns_to_pending_for_dedicated_worker labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py::test_start_export_for_job_only_enqueues_when_dedicated_worker_is_enabled -q`
  - `2 passed`
- 合并回归：训练 Worker/队列、边端调度/Worker、BM1688 恢复/入口共 `100 passed`。
- `python -m py_compile labelu/internal/application/service/yolo_bm1688_export_auto_resume.py` 通过。
- 合并回归中的既有 BM1688 Worker、自动恢复和导出路由用例全部通过。

## 4. 验收等级与边界

- 当前：本地 L1。
- 尚未证明：Ubuntu systemd、真实 TPU-MLIR、MySQL 锁等待、编译节点重启接管、真实 BModel 制品和生产部署。
- 本轮只收口 BM1688 编译恢复，不改变边端真机验证；自动创建边端验证任务的直接执行入口仍待单独审计。

## 5. 角色交付

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R03 架构 | 统一恢复扫描与 BM1688 Worker 的唯一消费者关系 | 完成 |
| R04 后端 | 失租状态回队和事务条件更新 | 本地完成 |
| R07 BM1688 边端 | 保留 `worker_action`，不改变编译制品契约 | 已复用 |
| R09 测试 | 恢复不启动 Web 编译线程、手工入口不回退 | `2 passed` |
| R10 运维 | 明确重启恢复依赖 BM1688 Worker | 本地完成，systemd 待验收 |

## 6. 提交与部署状态

本轮未提交、未推送、未部署，未修改生产数据库，等待用户明确“提交代码”。
