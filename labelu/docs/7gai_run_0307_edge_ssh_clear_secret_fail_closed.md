# RUN-0307 SSH 兼容通道清空密码的失败关闭修复

## 1. 本轮目标

修复边端设备兼容配置中“清空已加密 SSH 密码”无法保存的问题，同时保持两条
通道的生产语义：

- `board_agent`（板端主动拉取）不需要 SSH 凭据；
- `ssh` 是兼容验证通道，清空密码后可以保存为不可运行状态，但不能继续自动
  执行；
- 从板端主动拉取切换回 SSH 时，如果没有新密码，仍然必须拒绝切换。

本轮只做本地代码和测试，不连接生产、不执行迁移、不提交代码。

## 2. 根因

`patch_edge_ssh_config(password_secret_action="clear")` 先通过设备注册表清除
加密密码，随后兼容配置再次调用 `save_active_device_config`。第二次保存未携带
“明确清空”的意图，默认按 `keep` 更新 SSH 设备，触发“SSH 通道必须配置 SSH
密码”的门禁。

## 3. 修改内容

### 3.1 明确传递清空意图

`edge_ssh_config` 服务在清空动作后写入仅供进程内使用的
`_password_secret_action=clear` 标记。兼容配置保存函数消费并移除该标记，
不会写入设备注册表或接口响应。

### 3.2 保持 SSH 凭据门禁

`update_device_record` 的默认 `keep` 行为和“板端主动拉取切回 SSH 必须填写新
密码”规则不变。只有明确的清空动作允许保存无密码 SSH 配置；运行时
`is_edge_ssh_configured`、自动执行就绪判断和调度器继续将其视为不可用，确保
失败关闭。

## 4. 验证证据

```text
python -m pytest \
  labelu/tests/internal/application/test_edge_ssh_config_service.py \
  labelu/tests/internal/common/test_edge_device_registry.py \
  -q --disable-warnings --tb=short
10 passed

python -m pytest \
  labelu/tests/internal/application/test_edge_ssh_config_service.py::test_patch_edge_ssh_clear_encrypted_password \
  labelu/tests/internal/common/test_edge_device_registry.py::test_switching_board_agent_to_ssh_requires_a_new_password \
  -q --disable-warnings --tb=short
2 passed

python -m compileall -q \
  labelu/internal/application/service/edge_ssh_config.py \
  labelu/internal/common/edge_ssh_config.py \
  labelu/internal/common/edge_device_registry.py

git diff --check -- \
  labelu/internal/application/service/edge_ssh_config.py \
  labelu/internal/common/edge_device_registry.py \
  labelu/tests/internal/application/test_edge_ssh_config_service.py
```

专项断言覆盖：清空后密码配置和运行时密码均不可用、自动执行未就绪、通道仍为
SSH；板端主动拉取切回 SSH 无密码仍抛出凭据错误。测试使用本地 SQLite/测试
设备注册表，不代表真实 MySQL、systemd、SSH 或 BM1688 生产验收。

## 5. 影响和后续

- 修改文件：
  `labelu/internal/application/service/edge_ssh_config.py`、
  `labelu/internal/common/edge_device_registry.py`、
  `labelu/tests/internal/application/test_edge_ssh_config_service.py`。
- Board Agent 主通道不受影响；外部已下发模型和生产设备未触碰。
- 发布前仍需在预生产验证 MySQL 注册表、两类通道切换、systemd 调度和真实
  BM1688；在用户明确“提交代码”前保持本地 L1。
