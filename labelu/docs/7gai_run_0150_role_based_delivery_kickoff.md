# RUN-0150 按角色开发落地启动与交付记录

- 日期：2026-09-19（Asia/Shanghai）
- 轮次目标：按照 7GAI 角色方案开始可执行开发；每个工作包必须同时产出代码、专项测试、验收证据和独立文档，并回写交付台账。
- 当前等级：本地 L1；未提交、未推送、未部署、未写入生产。
- 当前生产边界：生产仍未证明真实 AICM 发布、第三方 S3 metadata/图片对账、同版本 MySQL 并发和 Worker 重启接管，因此所有执行器开关保持失败关闭。

## 1. 角色不是登录角色

系统登录角色仍只有“标注人员”和“管理人员”。下面的 R01-R10 是研发交付责任，不增加产品权限：

| 角色 | 研发责任 | 本轮交付必须包含 |
|---|---|---|
| R01 产品与业务 | 菜单、主流程、权限、按钮语义和验收口径 | 页面/接口行为说明、异常状态和验收清单 |
| R02 标注与数据契约 | 标签契约、矩形框、预标注、冻结训练数据 | 数据来源、类别顺序、快照可复现证据 |
| R03 架构与迁移 | 数据血缘、事务、租约、fencing、迁移链 | 结构迁移、并发模型、回退边界和锁证据 |
| R04 后端业务与权限 | 路由、服务、持久化、幂等、审计 | 正常/失败/重试/权限专项测试和请求编号 |
| R05 外部平台与回流 | AICM、S3/MinIO、metadata、第三方回流 | 外部编号对账、重复通知和真实包证据 |
| R06 模型算法 | PT、ONNX、FP32/FP16/INT8、冻结评估集和阈值 | 同一评估集对比、掉点/性能判定依据 |
| R07 BM1688 边端 | TPU-MLIR 编译节点、真机只推理、设备通道 | 运行包、加载/推理/P95/错误率和设备租约证据 |
| R08 前端与体验 | 中文菜单、居中弹窗、任务/训练/模型页面 | 桌面/窄屏、按钮状态、请求只发一次证据 |
| R09 测试与验收 | 功能、并发、故障、迁移、前端和生产证据 | 可重跑命令、原始日志、对象 ID 和结论 |
| R10 运维与安全 | Worker、systemd、连接池、监控、备份、密钥 | 启停/重启、资源、告警、恢复演练和放行结论 |

## 2. 本轮开发顺序

1. **P0 发布并发一致性**：R03/R04 主责，R09 协作。对同一 `yolo_bm1688_export` 增加 AICM 发布领取租约和 fencing，确保 2/10/20/50 个并发请求最多创建一个发布任务；已完成的发布不能重复发起，失败任务可重试。
2. **P0 边端语法与专项回归**：R07/R09 主责。先修复 `board_agent.py` 当前语法错误，恢复 AICM、模型目录、S3 回流专项测试的可执行性。
3. **P0 外部证据联调准备**：R05/R10 主责。整理真实或脱敏的 `metadata.json`、图片清单、AICM `model_set_id`、LabelU 任务/样本 ID 对账表；没有证据时不打开生产 Worker。
4. **P1 菜单和按钮收口**：R01/R08 主责。每个按钮明确前置条件、成功/失败/处理中/空数据状态；统一使用中文居中弹窗，不恢复抽屉。

## 3. 每个工作包的“完成”定义

工作包只有同时满足以下条件才可在台账标记“本地完成”：

- 代码路径已完成，且没有绕过权限、租约或发布门禁；
- 对应专项测试通过，失败路径、重复请求、权限和并发至少有一项可重跑证据；
- 独立 RUN 文档写明改动文件、命令、结果、未覆盖范围和下一依赖；
- 交付台账记录角色、工作包、对象 ID/请求编号、测试日志路径和当前 L 等级；
- 未经用户明确说“提交代码”，不得 commit、push、部署或修改生产数据。

## 4. 本轮当前状态

- 已确认 P0 代码入口：`labelu/internal/application/service/yolo_aicm_publish.py:start_publish_job`。
- 已确认复用表：`yolo_bm1688_export`，该表已经承载 PT、ONNX、BModel、边端验证和发布血缘。
- 已确认并发缺口：发布任务目前写入文件目录，缺少跨进程数据库领取租约；两个 Web 进程可能同时向 AICM 上传同一个导出记录。
- R07/R09 已修复 `labelu/internal/application/service/board_agent.py` 的语法阻断；边端统一验证专项已重跑通过，真实设备和 Ubuntu/systemd 仍待验收。
- 本轮不引入项目/业务空间、不增加设备发布/回滚机制；已发布到外部平台的模型出现问题时，只允许人工禁止本平台下载并发布替代模型。

### 4.1 P0-1 已落地的本地实现

独立记录：`labelu/docs/7gai_run_0150_aicm_publish_lease.md`。

- `YoloBm1688Export` 增加 `publish_status`、`publish_job_id`、`publish_lease_owner`、`publish_lease_expires_at`、`publish_fencing_token`。
- `crud_yolo_bm1688_export.claim_publish` 使用行锁加条件更新领取发布任务；已发布模型直接拒绝，处理中且租约未过期时返回忙，过期任务可以递增 fencing token 接管；相同 `job_id + owner` 重复领取保持幂等。
- `renew_publish_claim` 只允许当前 `job_id + owner + fencing_token` 且租约仍有效的执行器续租；后台发布线程使用续租监控，防止长时间 AICM 上传期间被误接管。
- `finish_publish_claim` 只允许当前 `job_id + owner + fencing_token` 写入完成或失败状态，旧任务迟到写入返回失败。
- `start_publish_job` 在创建文件任务和启动线程前先领取数据库租约；后台线程成功登记本平台模型目录后才写入完成状态，非成功结果和异常释放为可重试的失败状态。
- 新增正式迁移 `z9b0c1d2e3f4_add_aicm_publish_claim_lease.py`；迁移降级采用备份恢复，不自动删列。

### 4.2 本地证据

- `python -m compileall -q`：模型、CRUD、发布服务和发布任务通过；`git diff --check` 通过。
- `python -m pytest labelu/tests/internal/application/service/test_yolo_aicm_publish_claim.py labelu/tests/internal/application/service/test_yolo_aicm_publish_helpers.py labelu/tests/internal/application/test_aicm_publish_sop_gate.py -q`：`127 passed`。
- `python -m pytest labelu/tests/internal/application/service/test_yolo_aicm_publish_claim.py labelu/tests/alembic/test_empty_database_upgrade.py labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py -q`：`39 passed`。
- `python -m pytest labelu/tests/internal/adapter/routers/test_yolo_bm1688_exports.py labelu/tests/internal/application/service/test_yolo_aicm_publish_project_scope.py -q`：`39 passed, 1 skipped`。
- 边端统一验证、设备注册和 Worker 专项：`17 passed`，补充报告完整性专项 `3 passed`；平台页面集合最终 `25 files / 135 tests passed`。
- 仍有 3 项第三方策略调用旧测试夹具失败，原因是旧测试仍构造已退休的 `project_id`；该失败进入下一轮去项目化迁移，不能恢复默认项目绕过。

### 4.3 尚未完成

- 迁移尚未在同版本 MySQL 沙箱执行，不能把 SQLite 专项结果视为生产锁等待/20 并发通过。
- 尚未执行 2/10/20/50 并发下真实 AICM 请求验证；外部 AICM 可能已接受的重复副作用仍需通过对账和幂等策略验证。
- 真实第三方 S3 包、`metadata.model_id`、AICM `model_set_id` 与 LabelU 任务/样本 ID 尚未取得对账证据。
- 未提交、未推送、未部署，生产发布 Worker 和自动回流 Worker 保持关闭。

## 5. 交付记录要求

后续每一轮以 `RUN-编号` 建立独立文档，至少包含：目标、责任角色、代码/页面/API范围、生产事实、并发性能边界、测试命令和原始证据、未完成项、放行等级、下一轮依赖。台账和主方案只保留摘要，避免把未验证内容写成生产事实。
