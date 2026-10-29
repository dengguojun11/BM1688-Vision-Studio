# RUN-0305 BM1688 编译与边端验证 Worker 审计（2026-09-20，本地 L1）

## 1. 本轮目标

按角色团队方案审计模型中心的 BM1688 编译、Board Agent 主通道、SSH 兼容通道和独立边端验证 Worker，核对以下真实业务链是否能被系统可靠记录和恢复：

```text
训练完成 PT -> 静态 ONNX -> FP32/FP16/INT8 BModel -> 固定评估集 -> BM1688 真机推理 -> 发布前质量门禁
```

本轮只做本地代码路径、数据库契约和隔离测试，不连接生产、不运行真实 TPU-MLIR、不宣称 BM1688 真机通过。

## 2. 代码路径结论

### 2.1 编译 Worker

- `labelu/internal/application/service/bm1688_worker_runtime.py` 消费 `yolo_bm1688_export` 的 `PENDING` 记录，支持 `export`（PT 导出 ONNX 后编译）和 `recompile`（已有 ONNX 重新编译）。
- Worker 先使用 `queue_lease_owner/queue_lease_expires_at` 领取，再由 `_run_bm1688_export_sync` 或 `_run_bm1688_recompile_sync` 获取独立执行租约和 fencing token；旧 Worker 的进度、产物和终态写入会被条件更新拒绝。
- `yolo_bm1688_export.py` 的常规 YOLO 链实际执行静态 ONNX 导出、TPU-MLIR 编译、校准集/量化表处理和制品清单生成。编译契约要求 BM1688 目标、静态 batch=1、固定工具链记录，以及 `fp32/fp16/int8` 文件齐全后才具备完整制品条件。
- 编译完成不会直接视为发布通过。固定评估集证据在后续边端验证阶段产生，缺失时编译契约明确返回 `fixed_eval_missing`，发布门禁继续阻断。
- 车牌检测和历史复合车牌专用链复用执行租约，但当前专用脚本的业务制品主要是 `plate_detection_fp16_1b.bmodel` 或 `plate_recognition_fp16_1b.bmodel`。它们不能被误标成一期 YOLO 三精度发布包；若要把车牌专用模型纳入同一发布门禁，必须另行补齐对应精度策略和真机证据。

### 2.2 边端验证 Worker 和设备并发

- `edge_validation_worker_runtime.py` 默认关闭；开启后 Web 进程不启动 SSH 执行线程，独立 Worker 接管执行。
- `yolo_edge_validation_scheduler.py` 负责 FIFO 入队和设备分配，按设备 ID 避免同一设备同时执行两个任务；Board Agent 设备要求心跳在线且环境就绪，SSH 设备要求探针通过。
- `yolo_edge_validation.py` 在上传、执行、结果回传和终态写入前检查执行租约；断网或进程退出后，租约过期任务可以被 Worker 接管。
- `board_agent.py` 的 `claim_next_unified_job` 以设备身份领取已分配任务，并将执行租约 token 绑定到任务令牌。Board Agent 是主通道；SSH 仅作为兼容和排障通道，不应在生产同时为同一设备启动两套执行者。
- Board Agent 下发包包含模型制品、固定评估集、运行配置、阈值契约和清单签名；服务端只持有私钥，板端验证公钥，缺少签名或清单摘要不应继续加载。

## 3. 已确认的不足与处理边界

1. **真实编译证据缺失**：本地只能确认命令、Profile、清单和失败关闭逻辑；没有 TPU-MLIR 版本、容器摘要、真实 PT、静态 ONNX、三种 BModel 的可追溯产物，因此不能标记“编译通过”。
2. **真实 BM1688 证据缺失**：没有真实设备加载三种 BModel、固定评估集推理数、精度、P95、短稳、断网、重启接管和资源释放证据；Board Agent/SSH 开关必须继续保持关闭或仅在隔离环境开启。
3. **车牌专用精度边界**：车牌专用编译当前以 FP16 复合制品为主，不应套用 YOLO 三精度发布结论。产品页面应显示“专用链精度策略”，缺少明确策略时阻断发布，不把 FP16 文件伪装成 FP32/INT8 已完成。
4. **设备槽位容量未实测**：代码按设备 ID做单设备占用保护，但真实 BM1688 的内存、模型并发、USB/网络带宽和多设备吞吐尚未测量。生产放量前必须取得 `1/2/10` 任务并发、锁等待、平均/P95 时延和设备资源曲线。
5. **生产部署漂移风险**：此前生产只读审计已显示线上版本与当前本地路由/鉴权存在漂移。本轮没有重新部署或写入生产；不能用本地测试结果替代生产版本确认。

## 4. 本地验证

执行：

```text
python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/application/service/test_edge_validation_worker.py labelu/tests/internal/application/service/test_board_agent_unified.py labelu/tests/internal/application/service/test_plate_detection_bm1688.py labelu/tests/internal/application/service/test_plate_recognition_bm1688.py labelu/tests/internal/common/test_bm1688_compile_contract.py -q --disable-warnings --tb=short
```

结果：`30 passed, 114 warnings`。

覆盖内容：

- 编译 Worker 默认关闭、数据库租约字段缺失失败关闭；
- 双进程领取竞争、过期租约接管、有效执行租约拒绝重复执行；
- 编译/重编译统一入口、三精度契约、固定评估集缺失阻断；
- Board Agent 令牌、设备身份、任务租约和结果回传 fencing；
- SSH/Board Agent 调度分流与设备槽位保护；
- 车牌检测/复合车牌专用产物路径和失败状态。

另行确认：`python -m compileall -q labelu/internal` 和 `git diff --check` 在父工作包收口时执行；本轮没有提交、推送或部署。

## 5. 生产放行前置

以下证据全部完成前，不允许把相关 Worker 当作生产主路径：

- 同版本 MySQL/InnoDB 迁移、索引执行计划、双 Worker 竞争、锁等待和租约接管；
- 一台真实 BM1688 与一组真实 YOLO 代表模型完成 PT→静态 ONNX→FP32/FP16/INT8 BModel；
- Board Agent 在线、身份认证、清单验签、三精度加载、固定评估集推理、P95 和短稳；
- 断网、服务重启、Worker 重启后旧 token 写入被拒绝且任务可安全接管；
- 真实车牌专用模型的精度策略和发布门禁单独验收；
- systemd、日志告警、设备槽位容量和生产版本对账；
- AICM 与本平台模型下载目录的发布前门禁、禁止后续下载和问题处置演练。

## 6. 验收等级

本轮为 **L1 本地完成**。真实 TPU-MLIR、BM1688、MySQL、systemd、AICM 和生产浏览器证据均为“未完成”，不是通过。相关开关保持默认关闭；外部平台已下载的模型不执行回滚，只能禁止本平台后续下载并发布替代版本。
