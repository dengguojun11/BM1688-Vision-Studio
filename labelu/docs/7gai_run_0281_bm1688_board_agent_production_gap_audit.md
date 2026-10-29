# RUN-0281 BM1688、Board Agent/SSH、三精度与 AICM 生产差距审计

> 日期：2026-09-20（Asia/Shanghai）  
> 责任角色：R03 架构、R06 模型算法、R07 BM1688 边端、R09 测试、R10 运维  
> 工作包：WP3（编译、真机验证与发布）、WP5（测试放行）、OPS  ️  
> 性质：只读审计与下一工作包定义；本轮不修改业务代码、数据库或生产配置，不提交、不推送、不部署。

## 1. 审计范围与结论

本轮按当前工作树、已有专项测试、RUN 文档和生产 GET-only 证据核对以下链路：

```text
PT -> 静态 ONNX -> FP32/FP16/INT8 BModel
   -> BM1688 真机加载/推理 -> 固定评估集 -> AICM model_set_id
   -> LabelU 发布目录与后续下载控制
```

结论：**本地 L1 契约较完整，生产尚未具备可放量证据。** 当前代码已经有 Profile 摘要、制品清单、三精度文件检查、Ed25519/摘要门禁、统一任务租约和 AICM 本地目录；但真实 TPU-MLIR、真实 BM1688、systemd/独立 Worker、生产 MySQL、AICM/S3 三方对账均没有本轮可用证据。

生产最新只读证据为 `.tmp/7gai-production-readonly-route-audit-0279.json`（2026-09-20 17:01）：仅发送 GET、未认证、未执行退休写探针；OpenAPI 仍为 295 条旧路径，健康接口返回 SPA HTML，四个一期目标路由缺失，旧 `edge-device-jobs` 写路径仍登记，整体 `passed=false`。

## 2. 代码与证据核对

| 核对项 | 当前本地证据 | 当前生产/真实证据 | 判断 |
|---|---|---|---|
| PT -> ONNX | `build_onnx_export_contract()` 固定 Sophon 分支 `opset=17`、静态 batch=1；测试覆盖 | 未取得真实模型导出文件和 ONNX shape 对账 | 本地契约通过，真实链路未验收 |
| 三精度 | `evaluate_bm1688_compile_contract()` 在 `full` 模式检查 FP32/FP16/INT8；Board Runner 缺任一精度失败 | 没有真实三精度同批编译和文件 SHA256 证据 | 不能宣称 BModel 已生产生成 |
| BM1688 Profile | 清单和边端报告传递 `profile_digest`；摘要不一致阻断发布 | 没有真实 TPU-MLIR 版本、输出节点/shape、运行时 ABI 证据 | Profile 仍是本地快照，未完成真实标定 |
| 固定评估集 | 包 manifest 摘要和 `fixed_eval_signature` 有门禁 | 没有真实封存评估集与三精度同批结果 | 不能把本地模拟评估当真机结果 |
| Board Agent/SSH | 统一路径有设备 ID、短期令牌、租约、fencing；SSH 共用 Profile/清单门禁 | `BOARD_AGENT_PRIMARY_ENABLED=false`、独立 Worker 默认关闭；无真实 Agent 拉取、重启接管和断网证据 | 两条路径均未达 L3 |
| 设备身份/租约 | `board-agent:{agent_id}` owner、fencing token 和旧令牌拒绝有专项测试 | 未在同版本 MySQL/真实设备执行双消费者、锁等待、掉电重领 | 仅本地不变量通过 |
| AICM 与本地目录 | 使用 AICM `model_set_id` 作为本平台模型 ID；本地包 SHA256/下载开关已有门禁 | 未用真实 AICM 测试账号完成上传回执、目录、S3 metadata 三方对账 | 不能宣称模型发布闭环 |

## 3. 关键发现

### 3.1 Board Agent 与 SSH 的主次在代码上可切换，但生产尚未真正收口

- [config.py](/D:/PycharmProjects/labelU/labelu/internal/common/config.py:625) 中 `BOARD_AGENT_PRIMARY_ENABLED` 默认 `false`；[edge_device_registry.py](/D:/PycharmProjects/labelU/labelu/internal/common/edge_device_registry.py:25) 中设备默认通道为 `ssh`。
- 调度器只有在统一开关打开且设备明确 `validation_channel=board_agent` 时，才跳过 Web 进程内 SSH 线程（[yolo_edge_validation_scheduler.py](/D:/PycharmProjects/labelU/labelu/internal/application/service/yolo_edge_validation_scheduler.py:498)）。否则仍会走旧 SSH 兼容链。
- 当前 `_configured_devices()` 无论通道都要求 host、user、password（[yolo_edge_validation_scheduler.py](/D:/PycharmProjects/labelU/labelu/internal/application/service/yolo_edge_validation_scheduler.py:61)），并且在线探测默认调用 SSH。由此可见，Board Agent 目前不是完全独立于 SSH 的设备就绪链；若生产只配置板端 Agent 而未配置 SSH 凭据，调度器可能不会把设备视为可分配。
- Board Agent 路由本身只提供领取、下载、进度和回报，没有已落库的板端心跳/在线租约读模型；因此当前没有足够证据证明“板端主动拉取主路径”已经能在无 SSH 访问时稳定调度。

这不是说 Board Agent 设计不能用，而是说明：**当前生产应明确保持失败关闭，不能把 `validation_channel=board_agent` 的配置开关当成真实主路径已验收。** SSH 只能作为受控兼容/排障通道，不能与 Board Agent 同时消费同一任务。

### 3.2 Profile 结构已有，但实际工具链身份还没有成为硬证据

- `validate_bm1688_profile_snapshot()` 会检查 Profile 结构、芯片、输入、ONNX、前后处理、工具链和校准区块是否存在（[bm1688_compile_contract.py](/D:/PycharmProjects/labelU/labelu/internal/common/bm1688_compile_contract.py:247)）。
- 编译合同另外检查顶层 `compiler_version`、校准图片数量、三精度文件、清单执行签名和固定评估证据（[bm1688_compile_contract.py](/D:/PycharmProjects/labelU/labelu/internal/common/bm1688_compile_contract.py:425)）。
- 但当前 Profile 的 `toolchain`/`calibration` 只要是对象即可通过结构校验；qtable 摘要、TPU-MLIR/Docker digest、运行时 ABI 和真实权重 SHA256 仍可能为空。编译流程找不到 qtable 时记录 warning 而不是立即阻断（`yolo_bm1688_export.py` 的 qtable 分支）。

因此，本地“Profile 存在”不能替代真实工具链身份证明。生产放行必须把实际工具链版本、镜像摘要、qtable/校准集摘要和运行时 ABI 写进同一不可变清单，并在板端回报中复核。

### 3.3 三精度规则合理，但当前阈值仍是工程起点

FP32、FP16、INT8 必须同时存在，并属于同一个模型版本；发布精度执行完整性能、精度和短稳门禁，其余精度至少加载和精度烟测。现有 `0.005/0.010/0.020` 等规则已经版本化，但 RUN-0164 已明确这些数值尚未用生产代表模型标定，不能作为永久算法标准。真实校准前不得放宽门禁，也不得将缺指标按 0 处理。

### 3.4 AICM 编号与本地目录逻辑正确，但缺真实回执对账

本地目录以 AICM `model_set_id` 作为公开模型 ID，下载包保留在 LabelU，管理员关闭 `download_enabled` 只影响本平台后续下载，不宣称召回外部平台文件。这符合“不可回滚”的已冻结边界。缺口在于生产尚未证明 AICM 上传回执、平台目录记录、发布包摘要和第三方 S3 metadata 能以同一个 `model_set_id` 唯一对齐；未知或冲突 ID 必须进入 `WAITING_FOR_MODEL`，不能猜测匹配。

## 4. 推荐的最小高价值工作包

### WP3-HIL-01：一台真实 BM1688、一个真实 YOLO11 代表模型的边端放行包

该工作包不扩菜单、不增加角色、不引入设备发布/OTA/回滚，也不改业务数据真源，目标是先取得可审计的 L3 证据：

1. 在预生产编译节点用一个真实 YOLO11 代表模型执行 PT -> 静态 ONNX -> FP32/FP16/INT8 BModel；记录真实 TPU-MLIR 版本、Docker 镜像摘要、运行时 ABI、qtable 摘要、校准集摘要和三种 BModel SHA256。
2. 固定一份有标注评估集，生成 `fixed_eval_signature`；三种 BModel 和 PT/ONNX 必须读取同一份图片与标签，报告有效推理数、mAP50、关键类别召回、误检/漏检、P95、错误率和短稳结果。
3. 先选择一种执行通道完成闭环。按当前代码可执行性，预生产先使用受控 SSH 兼容通道；Board Agent 只有在设备身份、Agent 拉取/在线证据、任务令牌、公钥验签和断网重领验收完成后才切为主路径。两条通道不得同时消费同一任务。
4. 将服务器清单 `profile_digest`、板端回报 digest、设备序列号、执行 fencing token 与结果摘要做逐字段对账；任一缺失/不一致进入 `PARTIAL_COMPLETED` 或失败关闭。
5. 使用 AICM 测试账号完成一次上传，核对 AICM `model_set_id`、LabelU 本地模型目录、保留包大小/SHA256 和回流 metadata；再验证关闭下载只阻断本平台下载。

**工作包验收通过条件**：一份可复核的编译包、一份固定评估集 manifest、一份 BM1688 三精度报告、一份设备/租约/fencing 报告、一份 AICM/LabelU/S3 对账表；缺任一项不得开启生产 Worker 或自动回流。

## 5. 生产阻断与下一依赖

当前必须保持：

- `BOARD_AGENT_ENABLED`、`BOARD_AGENT_PRIMARY_ENABLED`、独立边端验证 Worker 和自动回流在生产失败关闭，直到 WP3-HIL-01 的真实证据完成；
- 不执行正式 Alembic 迁移、不向生产发送写请求、不把 HTTP 200、模拟器、SQLite 或本地假 BModel 当生产验收；
- 不再次向生产发送旧 `edge-device-jobs` 写探针；部署后先修复 JSON 健康探针和 OpenAPI 退休路径，再做两类真实账号只读/业务回放；
- 已发布到外部平台的模型不回滚，只能通过本地目录关闭后续下载并发布替代模型。

下一依赖按顺序为：

```text
生产版本部署前的 OpenAPI/健康探针修复
 -> 真实 TPU-MLIR + BM1688 WP3-HIL-01
 -> Board Agent 通道一致性/在线证据闭环
 -> AICM/LabelU/S3 三方对账
 -> 同版本 MySQL 并发、重启接管和生产小流量验收
```

## 6. 验收等级与状态

| 项目 | 等级 | 状态 |
|---|---|---|
| 代码/测试契约 | L1 | 已有专项通过；仅证明本地逻辑 |
| 真实 TPU-MLIR、BM1688、systemd | 未达 L2/L3 | 缺真实环境证据 |
| AICM、S3、生产 MySQL | 未达 L2/L3 | 未做真实联调/写入 |
| 生产 HTTP/OpenAPI | L2 失败关闭 | 证据见 `.tmp/7gai-production-readonly-route-audit-0279.json` |
| 本轮提交/部署 | 未执行 | 等待用户明确“提交代码” |

