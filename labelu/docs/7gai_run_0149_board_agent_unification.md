# RUN-0149 板端主动拉取统一任务主路径收口

## 1. 轮次信息

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试；R10 运维协作。
- 工作包：WP3 BM1688 编译、真机验证与发布。
- 需求编号：`EDGE-BOARD-UNIFIED-01`、`EDGE-BOARD-FENCING-01`、`EDGE-DEVICE-CHANNEL-01`。
- 交付级别：本地 L1；未提交、未推送、未部署。

## 2. 真实代码审计结论

此前 `/board-agent/jobs/*` 只读写历史 `yolo_board_validation`，而统一的
`yolo_edge_validation_job` 由调度器和 SSH 执行器处理，导致设备身份、执行租约、
短期令牌和重复回报无法在同一任务上闭环。本轮按方案收口，但保留旧历史表和旧接口
作为兼容链，不把旧表重新变成业务真源。

## 3. 本轮实现

### 3.1 可切换主路径

- 新增 `BOARD_AGENT_PRIMARY_ENABLED`，默认 `false`。
- 开关关闭：`/board-agent/jobs/*` 继续使用旧 `yolo_board_validation` 兼容路径，已有设备不受影响。
- 开关开启：只有设备配置 `validation_channel=board_agent` 的设备走统一主动拉取；配置为
  `ssh` 的设备仍由现有受控 SSH 兼容链执行。
- 调度器在统一主动拉取设备上不启动历史 SSH 线程，避免同一任务双消费者；SSH 设备仍可启动旧兼容执行器。

### 3.2 统一任务、设备身份和执行租约

- 板端领取条件是统一 `yolo_edge_validation_job` 已由调度器分配、状态为 `PROCESSING`，且
  `edge_device_id` 与请求头 `X-Board-Agent-Id` 完全匹配。
- 领取时复用 `execution_lease_owner`、`execution_lease_expires_at`、递增
  `execution_fencing_token`；owner 格式为 `board-agent:{设备ID}`。
- 返回只绑定本次任务和 fencing token 的 HMAC 短期任务令牌；下载、进度和回报均必须携带
  `X-Board-Agent-Id` 与 `X-Board-Agent-Task-Token`。
- 进度回报续租并带 owner/token 条件写入；任务断网或租约过期后，旧令牌不能继续落盘，后续可由新执行器重新领取。

### 3.3 回报幂等

- 回报写入统一任务 `result_json` 的 `board_agent_report`，并保存令牌摘要、报告摘要、设备身份和 fencing token。
- `COMPLETED` 仍必须经过边端证据门禁；证据不齐只进入 `PARTIAL_COMPLETED`，不能成为发布资格。
- 同一任务的同一令牌、同一报告重复提交返回现有终态，不重复覆盖；不同报告或旧令牌返回冲突/未授权。

### 3.4 设备配置复用

- 设备注册表新增 `validation_channel`，取值为 `ssh` 或 `board_agent`，默认 `ssh`，不新增项目/业务空间。
- 设备详情/API 返回当前通道，页面可复用现有设备配置弹窗；凭据仍由原加密存储链管理。

## 4. 修改文件

- `labelu/internal/common/config.py`
- `labelu/internal/common/edge_device_registry.py`
- `labelu/internal/application/command/edge_device_registry.py`
- `labelu/internal/application/response/edge_device_registry.py`
- `labelu/internal/application/service/edge_device_registry.py`
- `labelu/internal/application/service/yolo_edge_validation_scheduler.py`
- `labelu/internal/application/service/board_agent.py`
- `labelu/internal/adapter/routers/board_agent.py`
- `labelu/internal/application/response/board_agent.py`
- `labelu/tests/internal/application/service/test_board_agent_unified.py`

## 5. 本地验收证据

- 板端统一领取、设备绑定、执行租约、进度续租、过期令牌、重复回报和旧路径保留：`6 passed`；
- 设备注册表、边端 Worker 和环境相关回归：合计 `17 passed`；
- `python -m compileall -q`（本轮涉及模块）通过；
- `git diff --check` 通过。

## 6. 生产边界与未完成项

- 未连接生产数据库，未执行正式 Alembic 迁移、MySQL 锁等待或双 Worker 竞争；
- 未连接真实 BM1688、Board Agent、第三方 S3、AICM、TPU-MLIR 或 Ubuntu/systemd；
- 生产 `BOARD_AGENT_PRIMARY_ENABLED` 和边端 Worker 保持关闭；
- 尚未证明真实板端客户端能按响应刷新续租令牌，也未完成掉电/断网重领和 20 并发实机证据；
- 旧 `yolo_board_validation` 仅兼容读取/回传链，后续应在生产历史数据核对后逐步只读化，不删除历史记录。

因此本轮只证明统一主路径具备本地代码契约和开关边界，不能写成生产已切换。

## 7. 下一依赖

- R07/R10 提供真实板端客户端、设备 ID、网络策略和 systemd 服务；
- R09 在同版本 MySQL 沙箱执行双设备领取、断网接管、重复回报和 20 并发；
- R03/R04 完成正式迁移快照、短期令牌密钥配置和生产开关切换清单；
- 用户明确“提交代码”前不执行 commit、push、部署或生产写入。
