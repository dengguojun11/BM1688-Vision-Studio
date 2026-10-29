# RUN-0227 BM1688 边端、Board Agent 与验证 Worker 回归

## 1. 目标

验证模型中心进入 BM1688 后的边端执行链：服务端签发并签名制品清单，Board Agent/SSH runner 验签并加载三精度 BModel，边端验证 Worker 记录推理、P95、短稳和执行代次；旧设备作业控制面保持退休，设备端不进行编译。

## 2. 角色与交付

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP2/WP3 | 设备状态、执行世代、签名清单和失败关闭边界 |
| R04 后端 | WP3 | 边端验证路由、Worker 派发、设备注册和证据持久化 |
| R06 模型算法 | WP3 | 三精度边端质量证据、车牌检测包和性能指标 |
| R07 BM1688 | WP3/WP5 | Board Agent/SSH 验签、加载、推理、短稳和资源释放契约 |
| R09 测试 | WP5 | 边端路由、Worker、脚本、设备协议和性能探针回归 |
| R10 运维 | OPS | 真机、systemd、设备目录权限和重启接管待后续执行 |

## 3. 本轮验证

```text
python -m pytest [边端路由/Worker/Board Agent/设备协议/性能探针专项文件] -q --disable-warnings --tb=short
197 passed, 1 skipped, 121 warnings in 11.52s
```

覆盖签名清单、三精度制品、设备注册、执行世代、Board Agent/SSH 验签、边端验证 Worker、短稳指标和旧入口失败关闭。

## 4. 关键边界

- 服务器编译 PT 导出的 ONNX，生成 FP32/FP16/INT8 BModel；BM1688 只加载和推理；
- 清单签名、制品 SHA256、执行世代和固定评估集摘要任一不一致时拒绝加载；
- 设备状态文件原子更新、同代幂等、旧代拒绝，避免重启或并发验证重复副作用；
- 旧 `edge-device-jobs` 写入口保持失败关闭，不重新启用旧控制面；
- SSH 与 Board Agent 复用同一验签和证据门禁，不能通过不同路径绕过质量规则。

## 5. 环境边界与验收等级

本轮为本地 Runner、脚本和模拟设备证据，达到 **L1**。未连接真实 BM1688、TPU-MLIR、生产 AICM/S3、systemd 或生产 MySQL；未验证真机 P95、短稳、断网、资源释放和重启接管；未提交、未推送、未部署。

