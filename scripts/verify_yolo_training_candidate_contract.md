# YOLO 新建训练候选任务链路收口记录

## 接口废弃说明

- 旧路径：`GET /api/v1/yolo-train/remote-nodes?env_ready_only=true`
- 现状：该路径不再作为“新建训练”前端候选节点/候选任务来源。
- 正式替代：`GET /api/v1/yolo-train/eligible-tasks`
- 约束：后续如需调整“新建训练”候选链路，应继续围绕 `eligible-tasks` 演进，不要把 `remote-nodes?env_ready_only=true` 重新接回创建弹窗。

## 回归门禁入口

- 目标测试：
  - `labelu/tests/e2e/test_training_ui_engineering_hardening_frontend.py`
  - `labelu/tests/e2e/test_yolo_training_eligible_tasks_frontend.py`
  - `labelu/tests/internal/adapter/routers/test_yolo_train_eligible_tasks.py`
- 固定复跑入口：
  - `powershell -File scripts/verify_yolo_training_candidate_contract.ps1`
- 说明：
  - 这组测试当前不是 CI 专属门禁，而是仓库内的固定手动回归入口。
  - 任何改动到“新建训练候选任务链路 / eligible-tasks 契约 / 前端创建 payload”时，应至少复跑这一个脚本。

## 生产验证痕迹

- 验证目的：确认“新建训练”已完全切换到 `eligible-tasks`，且创建 payload 固定为本地执行，不再携带 `train_node_id`。
- 验证对象：
  - 标注任务：`#77 华润占道经营`
  - 真实训练作业：`#213`
  - 控制参数：`epochs=1`
- 关键结果：
  - 创建弹窗未再请求 `remote-nodes?env_ready_only=true`
  - 创建弹窗请求了 `GET /api/v1/yolo-train/eligible-tasks`
  - 真实提交成功，后端实际接收 `execution_mode=local`
  - 真实详情记录中 `train_node_id=null`
- 风险控制：
  - 选用小样本任务验证
  - 将训练轮数降到 `1`
  - 该记录为受控生产验证痕迹，不应视为异常脏数据
