# RUN-0289 板端主动拉取设备通道与配置页

## 1. 目标与菜单按钮

- 一级菜单：模型中心 → 真机验证 → 设备配置。
- 相关按钮：新增设备、编辑设备、查看设备、设为当前、刷新状态、体检并预安装（仅 SSH 通道）。
- 用户：标注人员只读设备状态；管理人员新增、编辑、切换、删除和体检。
- 主责：R04 后端、R07 BM1688、R08 前端/UI；协作 R03 架构、R09 测试、R10 运维。

## 2. 本轮交付

### 后端

- `validation_channel=ssh`：必须有主机和 SSH 密码。
- `validation_channel=board_agent`：不要求主机、端口、账号和密码；调度依据设备 ID、鉴权令牌、心跳和板端环境状态。
- 从板端主动拉取切换回 SSH 时，如果没有新密码直接失败关闭；切换到板端主动拉取会清理历史 SSH 密码。
- Board Agent 不调用 SSH 探测，不触发 SSH 环境体检轮询。

### 前端

- `frontend/extensions/pages/edge_device_registry.html` 增加“验证通道”选择。
- 板端主动拉取模式隐藏/禁用 SSH 字段，显示板端在线、最近心跳和环境就绪状态。
- “探测”按通道显示为“SSH 探测”或“刷新状态”；板端刷新只读取已上报心跳，不伪造主动心跳。
- 详情和编辑均为居中弹窗，无抽屉；窄屏不横向溢出。

## 3. 幂等、并发与失败关闭

- 设备状态来自统一设备注册表；同一设备 ID 更新覆盖同一记录，不创建重复设备。
- 调度只选择启用、在线、环境就绪且未占用的设备；心跳过期自动视为离线。
- Board Agent 与 SSH 为互斥验证通道；通道未就绪时不进入边端任务调度。
- `BOARD_AGENT_ENABLED` 与 `BOARD_AGENT_PRIMARY_ENABLED` 未同时开启时，统一主链保持失败关闭。

## 4. 验证证据

- `node --test frontend/extensions/tests/edge_device_registry.test.mjs frontend/extensions/tests/*.test.mjs`：23/23 通过。
- 内嵌 JavaScript `new Function` 语法检查通过。
- `python -m pytest -q labelu/tests/internal/common/test_edge_device_registry.py labelu/tests/internal/application/service/test_board_agent_device_channel.py`：9 项通过（合并执行时含设备注册和板端调度专项）。
- `git diff --check` 通过。

## 5. 环境边界与验收等级

- 本地：L1，通过代码和专项测试。
- 预生产：未完成；没有同版本 MySQL、真实 Board Agent 心跳和 BM1688 设备证据。
- 生产：未连接、未写入、未提交、未推送、未部署；RUN-0288 已证明生产仍是旧部署。
- 因此不能宣称 Board Agent 已在生产接管主路径；相关生产开关继续失败关闭。

## 6. 下一依赖

1. 在同版本 MySQL 保存真实设备注册表并验证并发心跳更新。
2. 在一台真实 BM1688 上完成 Agent 身份、签名清单、断网、重启和任务接管验证。
3. 后续把固定评估集、标签清单和三精度质量门禁接入 Board Agent 包。

