# RUN-0264 BM1688 三精度制品、设备验证与下载链只读审查

## 1. 轮次信息

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R06 模型算法、R07 BM1688 边端、R03 架构、R04 后端、R09 测试、R10 运维。
- 工作包：WP3 BM1688 编译、真机验证与发布。
- 审查性质：本地只读审查；未连接生产、未写数据库、未修改业务代码、未提交、未部署。
- 交付级别：本地 L1，不能写成生产已验收。

## 2. 总结结论

本地代码已经具备三精度制品清单、固定评估集、BM1688 验证包、Ed25519 清单验签、统一板端任务令牌和执行租约等主要设计件。上一轮发布门禁也已将普通模型收口为 `COMPLETED + FP32/FP16/INT8`，车牌专用模型保留显式 FP16 特例。

但当前仍有三个放行阻断：

1. 普通 BModel 下载接口的 `filename` 参数没有在解析后限制于当前导出 `models/` 目录，也没有限定只能下载 artifact manifest 中登记的文件；存在绝对路径或 `../` 读取媒体根目录其他文件的风险。
2. 生产是否真正启用统一 Board Agent、是否预置公钥和签名密钥、是否安装真实 `sophon.sail`/BMCV/BMRuntime，当前没有生产证据。配置默认仍是 `BOARD_AGENT_PRIMARY_ENABLED=false`，旧兼容链仍可存在。
3. 本地测试只证明代码契约，不能证明真实 BM1688 的三精度加载、性能、重启接管、断网续租、旧执行器 fencing、systemd worker 和发布包消费闭环。

## 3. 三精度制品链审查

### 3.1 本地已有能力

- `yolo_bm1688_export.py` 按模型前缀扫描 `models/BM1688`、`BM1684X`、`CV186X`，普通模型支持 FP32、FP16、INT8；车牌检测/识别使用专用 FP16 文件名。
- `is_bmodel_artifact_ready()` 仍是历史/下游轻量判断，可接受带真实 BModel 的 `ONNX_READY`；`is_bmodel_release_ready()` 才是发布门禁，要求 `COMPLETED` 和所需精度齐全。
- 导出完成时写入 artifact manifest，包含数据集/制品路径、摘要、编译配置、Profile 和执行世代；Board Agent 的 `run_config.json` 带 `required_bmodel_precisions`、manifest、`profile_digest` 和执行签名。
- 边端 runner 会在加载模型前校验清单签名、key id、导出编号、执行世代、Profile 摘要、文件路径/SHA256 和三精度完整性；缺证据时报告为 `PARTIAL_COMPLETED`，不能直接成为发布资格。

### 3.2 仍需补齐

- 前端和所有下游必须明确区分“有一个可运行 BModel”和“可发布的三精度制品包”，不能把 `ONNX_READY` 或单个 INT8 显示为可发布。
- 每个下载文件、验证包内文件、AICM 发布包文件都应来源于同一个不可变 manifest，并在下载/打包前逐项校验路径、大小、SHA256、精度、Profile 和执行世代。
- 必须在与生产同版本的 TPU-MLIR 环境中完成 PT -> ONNX -> FP32/FP16/INT8 全套 BModel，并记录编译器版本、芯片平台、量化校准集和 qtable。本方案一期不把三精度制品降级为可选项；本地代码不能替代这些运行证据。

## 4. 设备注册和真机验证链审查

### 4.1 本地已有能力

- 设备注册复用 `system_config` 的 `edge_device_registry` 组，保存设备 ID、host、端口、用户、远端目录、验证通道、启用状态、探测状态、环境报告和最后验证状态；密码沿用加密存储。
- 统一 Board Agent 主路径使用 `yolo_edge_validation_job`，领取时绑定 `X-Board-Agent-Id`、短期任务令牌、租约 owner、过期时间和 fencing token；下载、进度、回报均做身份和租约校验，并支持过期后重领及同报告幂等。
- `submit_report()` 会调用边端证据门禁，证据不完整时写入 `PARTIAL_COMPLETED`；报告包含设备身份、模型身份、制品摘要、Profile、固定评估集、精度、P95、错误率、稳定性和 fencing 证据。
- `run_config.json` 明确服务器编译、设备只加载推理，设备端不编译；生产环境会把 `edge_signature_required` 置为 true，但只有真正配置私钥并将公钥预置到设备后才有实际验签意义。

### 4.2 兼容链和生产缺口

- `BOARD_AGENT_PRIMARY_ENABLED` 默认关闭；RUN-0149 已记录生产开关和边端 Worker 保持关闭，当前不能声称统一主动拉取已在生产运行。
- 旧兼容 `/board-agent/jobs/*` 链只使用静态板端令牌，旧 `get_validation_for_agent()` 不按设备 ID、任务令牌和租约校验；在统一链未切换前，需限制其仅用于受控兼容，并记录退出时间。
- 设备注册记录目前没有本地可验证的 BM1688 芯片序列号、固件版本、BMRuntime/sophon.sail/BMCV 版本与验证报告的强绑定。报告中的 host 或 agent id 不能替代真实设备身份证明。
- 仍缺真实生产证据：板卡在线、SSH/Board Agent 网络策略、systemd worker、真三精度加载、真实固定评估集结果、P95/错误率、断网/重启恢复、租约过期后旧进程写入被拒绝、20 并发和失败重试。
- 生产必须预置 Ed25519 公钥、签名 key id、任务令牌密钥和 Board Agent enrollment token，并完成密钥轮换、撤销和审计；缺密钥时应保持 fail-closed。

## 5. 发布包和下载链审查

### 5.1 普通 ONNX/BModel 下载

- 路由：`GET /api/v1/yolo-bm1688-exports/{export_id}/onnx` 和 `/bmodel`。
- 已有保护：查询导出记录、校验任务访问权限、阻止 `PENDING/PROCESSING/FAILED`，并检查文件存在。
- 缺口：`resolve_bmodel_download_path()` 对用户传入的 `filename` 只做分隔符替换和路径拼接，没有在 `resolve()` 后验证路径仍位于当前导出的 `models/` 根目录，也没有只允许 manifest 登记的文件。绝对路径或 `../` 在已知文件名下可能读取媒体根目录其他文件。该问题应按 P0/P1 安全缺口优先修复。
- 缺口：普通下载没有统一检查三精度发布资格、artifact manifest SHA256、签名状态、Profile 和当前 release gate，因此“能下载”不应被页面展示为“可发布”。

### 5.2 Board Agent 验证包下载

- 统一链通过任务令牌绑定设备和租约；打包内容含 `run_config.json`、`models/` 和 `board_eval/`，签名/清单校验由边端 runner 执行。
- 旧兼容链通过静态 Board Agent token 和 validation id 获取 ZIP，缺少统一设备身份和租约隔离；只能作为迁移期间兼容路径，不能作为长期生产真源。
- 包下载前仍需确认导出状态、manifest 中三精度文件与 ZIP 内文件逐项一致，且生产端公钥和签名验收完成。

### 5.3 AICM/模型目录发布包

- 本地发布链已有 ModelManifest 校验、ZIP 文件列表校验、AICM `model_set_id` 回填和平台目录关联。
- 平台目录会做包路径 containment 和 ZIP 格式检查；现有下载读取路径未见把磁盘当前 SHA256 与目录记录的 `package_sha256` 强一致比对，若发布目录文件被人工替换，仍可能下载“有效 ZIP 但非登记内容”。应补下载前摘要比对。
- 本轮未连接真实 AICM，尚未证明 AICM ID、平台下载 URL、发布包 SHA256、第三方回流 `metadata.json` 和 LabelU 源导出之间可以三方对账；生产外部编号对账仍是放行前置条件。

## 6. 必须修复与可以删减的内容

### P0/P1 必须修复

1. BModel 文件下载改为 manifest 白名单：拒绝绝对路径、`..`、符号链接和不在当前 export 目录的路径；下载前校验文件 SHA256、精度和执行世代。
2. 统一普通下载、验证包下载、AICM 发布包使用同一 artifact manifest 和 release gate；页面把“可运行”“待真机验证”“可发布”分成三种状态。
3. 生产切换统一 Board Agent 前完成真实板卡注册、签名公钥预置、BMRuntime/驱动版本核对、systemd 和断网/重启/fencing 演练；切换期间旧兼容链只读、限时、可审计。
4. 把设备 ID 与真实板卡序列号/固件/运行时版本绑定，报告中保存设备证明和制品摘要，防止只伪造 host 或 agent id。

### 可删减或不应做成独立一级菜单

- 不需要为 FP32、FP16、INT8 各建一个菜单；它们是同一个模型制品包的精度成员。
- 不需要让标注人员手工维护 manifest、SHA256、数据划分、qtable 或设备签名；这些应由导出、打包和验证服务自动生成并展示结果。
- 不需要把“编译”“真机验证”“发布包下载”拆成互不关联的后台实体菜单；用户应从一次导出详情进入制品、验证、发布状态，管理人员才进入设备和环境配置。
- 不需要保留长期并行的 SSH/旧 Board Agent 两套生产真源；兼容链完成数据核对后应收敛到统一任务、设备身份和租约。

## 7. 本地验证证据

执行命令：

```text
python -m pytest labelu/tests/internal/common/test_edge_device_registry.py labelu/tests/internal/application/service/test_board_agent_pack.py labelu/tests/internal/application/service/test_board_agent_unified.py labelu/tests/internal/application/service/test_edge_board_validate_runner.py labelu/tests/scripts/test_edge_board_validate_runner_script.py labelu/tests/hardware/test_bm1688_hil.py -q
```

结果：`31 passed, 1 skipped`。跳过项为硬件 HIL；测试只证明本地注册、打包、统一任务令牌、证据门禁和 runner 契约，不证明真实 BM1688、生产 MySQL、systemd、AICM、S3 或网络恢复。

## 8. 生产放行清单

- [ ] 生产实际 `APP_ENV`、`BOARD_AGENT_ENABLED`、`BOARD_AGENT_PRIMARY_ENABLED` 和验证通道已核对。
- [ ] 真实 BM1688 板卡序列号、固件、驱动、BMRuntime、sophon.sail、BMCV 已登记并与设备 ID 对账。
- [ ] TPU-MLIR 编译器版本、校准集、qtable 以及 FP32/FP16/INT8 三种文件 SHA256 均已留存并完成对账。
- [ ] Ed25519 公钥/key id、任务令牌密钥、enrollment token 已预置并完成错误签名/篡改包/旧世代重放测试。
- [ ] systemd worker、断网重连、重启接管、租约过期、旧进程迟到回报和并发任务已在同版本环境验证。
- [ ] 下载接口已完成 manifest 白名单和路径 containment 修复，并有绝对路径、`../`、符号链接和旧文件回归测试。
- [ ] AICM `model_set_id`、平台下载 URL、发布 ZIP SHA256、S3 `metadata.json` 与 LabelU export/model/task/sample ID 完成对账。

在上述清单全部完成前，方案只能标记为“本地具备实现、生产待验收”，不能标记为“BM1688 边端发布闭环已完成”。
