# RUN-0183 角色工作包联合回归与交付记录

**日期**：2026-09-20（Asia/Shanghai）  
**环境级别**：本地 L1；未提交、未推送、未部署  
**责任角色**：R03 架构、R04 后端、R06 模型算法、R07 BM1688 边端、R08 前端/UI、R09 测试、R10 运维  
**关联工作包**：WP0 菜单主链、WP2 迁移与 Worker、WP3 BM1688 边端、WP4 第三方回流、WP5 测试验收

## 1. 本轮交付

1. 收口默认六个中文菜单和无项目主链，历史地址只保留兼容跳转；结果记录见 RUN-0182。
2. 迁移发布闸门保持 fail-closed；候选迁移、备份恢复和 MySQL 锁等待证据不足时不连接正式数据库；结果记录见 RUN-0180。
3. 第三方 S3 旧入口只做字段转译和统一受理，下载、去重、任务复用和预标注由统一 Worker 自动执行；旧同步副作用链返回 410；结果记录见 RUN-0181。
4. SSH 兜底边端包补齐固定评估清单、模型身份、Profile 摘要、三精度要求、Ed25519 签名和执行世代字段，与 Board Agent 共用验签和防重放门禁；结果记录见 RUN-0179。

## 2. 本地验证

| 验收范围 | 命令/范围 | 结果 |
|---|---|---|
| 菜单与无项目 | `test_7gai_visible_menu_no_project_contract.py` | `2 passed` |
| 迁移发布闸门 | `test_migration_release_gate.py`、`test_migration_release_deploy_gate.py` | `10 passed` |
| 旧 S3 入口 | 旧适配器、旧入口路由和配置专项 | `10 passed`；旧入口联合回归由工作包记录为 `16/48 passed` |
| 执行世代 | `test_edge_execution_generation_state.py` | `7 passed` |
| SSH/边端清单 | SSH 验证与车牌边端打包专项 | `36 passed`；世代/运行器组合 `28 passed`；SSH 专项 `34 passed` |
| 边端证据 | 固定评估集、Profile、三精度证据和执行世代 | `8 passed` |
| 主动学习/健康/范围 | 主动学习、健康探针、全局迁移 | `16 passed` |
| 训练 Worker | `test_yolo_train_worker.py` | `11 passed` |
| BM1688 Worker/fencing | 编译自动恢复、Worker 运行时、执行 fencing | `51 passed` |
| 内部认证与回流 | 实验数据集查询接口认证、OpenAPI 安全声明和回流回归 | `28 passed`；中文 OpenAPI/S3 `11 passed` |
| 前端类型 | `pnpm type-check` | 通过 |
| 前端构建 | `pnpm build` | 通过；保留既有 Ant Design、代码分包和 `eval` 警告 |
| Python 编译 | `python -m compileall -q labelu scripts` | 通过 |

测试均在本地串行执行；未把 Windows 测试机通过结果扩大为 MySQL、GPU、TPU-MLIR、BM1688 真机或生产通过。

## 3. 并发、性能与失败关闭结论

- 迁移闸门在 `alembic upgrade head` 前执行，缺少沙箱、备份恢复和锁等待证据直接停止，不连接 `DATABASE_URL`。
- S3 旧入口不在 HTTP 请求内下载对象、建任务或写预标注，避免请求线程阻塞；统一 Worker 负责租约和幂等。
- Board Agent 与 SSH 运行器的清单验签、执行世代比较和状态文件原子写保持一致；状态损坏、旧世代、不可比较世代失败关闭。
- 本地回归未执行真实长列表压测、同版本 MySQL 20 并发、GPU/TPU-MLIR 编译、BM1688 三精度推理和断电恢复，相关开关继续关闭。

## 4. 生产边界与下一步

- 未连接生产 MySQL、S3、AICM、TPU-MLIR、GPU、BM1688 或 systemd；未写生产任务、图片、模型、密钥或设备状态。
- 当前交付等级为本地 L1。进入 L2/L3 前必须完成正式迁移链、同版本 MySQL 并发与备份恢复、真实 S3/AICM 对账、真实 BM1688 三精度加载推理和设备重启接管。
- 在用户明确“提交代码”前，不提交、不推送、不部署；生产 Worker、自动训练和真实设备任务保持关闭。
