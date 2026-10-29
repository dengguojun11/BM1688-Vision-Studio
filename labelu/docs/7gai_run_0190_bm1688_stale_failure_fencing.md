# RUN-0190 BM1688 预检失败回写执行期 fencing

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试；WP3/WP5。
- 本轮范围：只修复通用 `_run_bm1688_export_sync` 在“训练作业未完成/缺少权重”和“缺少 ultralytics”两个预检失败分支的状态回写；不改边端回流回调、不改迁移、不连接生产。
- 缺口：这两个分支原先无条件调用 `crud_yolo_bm1688_export.update_row(status=FAILED)`。执行期租约被其他 Worker 接管后，旧 Worker 仍可能覆盖接管者状态。
- 修复：有 `execution_monitor` 时统一通过 `execution_monitor.conditional_update(..., db=db, manage_transaction=True)` 写入失败状态；只有迁移字段不可用的历史兼容路径才使用原有事务更新。旧 token 已失效时条件写入抛出 `ExecutionFencingLost`，由外层停止旧 Worker 的后续写回。
- 测试：`python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short`，`43 passed`；关联 BM1688 Worker、车牌检测/识别编译和边端业务回归 `19 passed`；`python -m py_compile labelu/internal/application/service/yolo_bm1688_export.py` 通过；目标文件 `git diff --check` 通过。
- 并发边界：本地测试验证代码契约和已有执行期条件更新；未执行真实双进程 BM1688、MySQL 锁等待、TPU-MLIR 或真机编译，不能替代 L2/L3 验收。
- 生产边界：未连接生产数据库、S3、AICM、BM1688 或 systemd；未写生产任务、制品和发布记录。
- 提交/部署：未提交、未推送、未部署；等待用户明确“提交代码”。
- 状态：本地 L1；建议后续继续审计执行完成后自动创建边端验证和质量报告的回调门禁。
