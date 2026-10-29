# 7GAI进化平台实施分工与交付台账

## RUN-0343 自动训练看门狗分页扫描与逐批续租（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP1/WP2 | 将训练、BM1688、边端和发布状态扫描改为主键分页，避免一次性物化全部历史记录 | 本地代码通过 |
| R04 后端 | WP2 | 新增 `AUTO_TRAINING_WATCHDOG_SYNC_BATCH_SIZE`，默认每批 20 条；每批和每条同步前续租并检查 fencing | 本地专项通过 |
| R09 测试 | WP5 | 增加真实 SQLAlchemy 会话的 keyset 分页契约，并回归自动训练服务测试 | `183 passed` |
| R10 运维 | OPS | 保持自动训练计划写入和生产 Worker 开关原有失败关闭状态 | 未连接生产 |

- 改动文件：`labelu/internal/common/config.py`、`labelu/internal/application/service/auto_training_watchdog.py`、`labelu/tests/internal/application/service/test_auto_training_watchdog.py`。
- 数据/API影响：不新增业务表、API 或项目维度；仅改变看门狗内部读取方式和租约续期节奏，业务状态、计数和失败处理保持不变。
- 本地验证：自动训练专项 `183 passed`；看门狗分页测试覆盖分批 `[1,2]、[3]`；`py_compile` 和 `git diff --check` 通过。
- 统一本地门禁：`python scripts/verify_staging_and_hil.py --environment local --evidence-root .tmp/evidence/run0343 --run-local --check`，`blocking: 0`；证据文件为 `.tmp/evidence/run0343/local-gates-20260921T094423Z.json`。
- 性能边界：真实 SQLAlchemy 会话使用 `id > last_id`、`ORDER BY id`、`LIMIT batch_size`；轻量测试替身保留单批兼容。默认批大小 20，可通过配置调整到 1-100。
- 真实环境证据：尚无 MySQL 执行计划、锁等待、真实多进程看门狗和长周期租约数据；未连接生产、未执行训练/编译/边端/AICM/S3 写操作。
- 本轮未提交、未推送、未部署、未修改生产数据。

## RUN-0342 第三方 S3 回流 2/10/20/50 并发领取本地证据（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构/R04 后端 | WP2/WP4 | 复核第三方 S3 回流 PENDING 记录的数据库租约领取、过期接管和失租写保护 | 代码路径成立 |
| R05 外部回流 | WP4 | 保持第三方问题数据包仍以生产 S3 策略对象为唯一来源；本轮不伪造对象下载 | 边界符合方案 |
| R09 测试 | WP5 | 同一 SQLite 文件启动 2、10、20、50 个进程争抢同一回流记录 | 预计每组只有 1 个领取者 |
| R10 运维 | OPS | 保持回流 Worker 开关关闭，不访问生产 S3 | 未连接生产 |

- 改动文件：`labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py`，并发参数由 `[2, 10, 20]` 扩展为 `[2, 10, 20, 50]`。
- 测试范围：只验证真实 CRUD 条件领取、唯一租约持有者、fencing token 从 `1` 开始；不执行 S3 下载、样本导入、任务创建或训练。
- 证据命令：`python -m pytest labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q --disable-warnings --tb=short`，结果 `14 passed`，其中 `2/10/20/50` 并发四组均只有一个领取者。
- 统一本地门禁：`python scripts/verify_staging_and_hil.py --environment local --evidence-root .tmp/evidence/run0342 --run-local --check`，`blocking: 0`；证据文件为 `.tmp/evidence/run0342/local-gates-20260921T092528Z.json`。
- 生产边界：本地 SQLite 多进程结果不能替代 MySQL 隔离级别、连接池、锁等待和真实 S3 对账；生产回流开关保持失败关闭。
- 本轮未提交、未推送、未部署、未修改生产数据。

## RUN-0341 AICM 发布 Worker 争抢与失租写入本地契约（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构/R04 后端 | WP3 | 复核独立 Worker 的领取、续租、fencing 和完成写入路径 | 调用链成立；网页入队、Worker 领取 |
| R06 模型算法/R07 BM1688 边端 | WP3 | 确认发布前仍依赖质量门禁、边端证据和三精度制品 | 发布前置条件未放宽 |
| R09 测试 | WP5 | 增加双 Worker 争抢和旧 Worker 接管后写入回归 | `10 passed`（发布租约 + Worker） |
| R10 运维 | OPS | 保持本地独立 Worker 开关和外部平台写入关闭 | 未连接生产 |

- 独立记录：`labelu/docs/7gai_run_0341_aicm_publish_claim_concurrency.md`。
- 关键结论：同一导出记录的活动租约只能由一个 Worker 持有；租约过期接管后，旧 Worker 的 fencing token 不能续租或写入完成状态。
- 证据命令：`python -m pytest labelu/tests/internal/application/service/test_yolo_aicm_publish_claim.py labelu/tests/internal/application/service/test_aicm_publish_worker_runtime.py -q --disable-warnings --tb=short`，结果 `10 passed`。
- 统一本地门禁：`python scripts/verify_staging_and_hil.py --environment local --evidence-root .tmp/evidence/run0341 --run-local --check`，`blocking: 0`；证据文件为 `.tmp/evidence/run0341/local-gates-20260921T090341Z.json`。
- 边界：本地 SQLite 契约不等于 MySQL 多进程容量、真实 AICM 幂等或生产 systemd 证据；外部 AICM 上传一旦发生不能回滚，生产仍只能人工禁止本平台后续下载。
- 本轮未提交、未推送、未部署、未修改生产数据。

# RUN-0321 退休旧设备发布页面与模型回滚代码清理（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP0/WP4 | 固化无设备发布、无模型回滚；历史地址只兼容跳转模型中心 | 规则确认 |
| R03 架构 | WP4 | 核对路由和导入关系，确认旧页面不可达 | 路由契约通过 |
| R08 前端/UI | WP4 | 删除旧 `deployments.tsx`、`workflows.tsx` 及过时测试，保留两个兼容重定向 | 类型检查和构建通过 |
| R09 测试 | WP5 | 更新居中弹窗清单，复验六菜单和无项目契约 | Vitest `7 passed`；全量 Vitest `246 passed`；后端 `10 passed` |
| R10 运维 | OPS | 保持生产版本不变 | 未连接生产 |

- 删除：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/deployments.tsx`、对应 `deployments.test.ts`；`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/workflows.tsx`、对应 `workflows.test.tsx`。
- 更新：`frontend/labelU-Kit/apps/frontend/src/pages/platform/modalCentering.test.ts`。
- 独立记录：`labelu/docs/7gai_run_0321_retire_legacy_device_release_surface.md`。
- 边界：进化中心仍只读汇总 `/v1/workflows` 运行状态；未删除规则配置回退；未修改后端退休接口、数据库和生产部署。

# RUN-0320 固定评估集内容签名与 Board Agent/SSH 一致性回归（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP3 | 新增目录层级无关的 `fixed_eval_signature` 内容签名 | 本地契约通过 |
| R04 后端 | WP3 | SSH 优先复用 Board Agent 固定评估包，签名不一致失败关闭 | 本地专项通过 |
| R06 模型算法 | WP3 | 图片、YOLO TXT/XML 标签、数量和类别顺序统一绑定 | 本地专项通过 |
| R07 BM1688 边端 | WP3 | `run_config`/板端脚本优先内容签名，兼容旧 `manifest_sha256` | 本地专项通过 |
| R09 测试 | WP5 | 固定集签名、布局差异、内容篡改和 SSH 复用回归 | `23 passed`；边端/发布门禁 `30 passed` |
| R10 运维 | OPS | 保持生产 Worker 与自动发布开关关闭，记录生产缺口 | 未连接生产 |

- 代码：`fixed_eval_signature.py`、`board_eval_pack.py`、`board_agent.py`、`edge_ssh_validation.py`、`edge_board_validate_runner.py`。
- 测试：`labelu/tests/internal/application/service/test_edge_ssh_fixed_eval.py` 及固定评估集专项。
- 独立记录：`labelu/docs/7gai_run_0320_fixed_eval_content_signature.md`。
- 边界：未连接生产、未执行生产写入、未提交、未推送、未部署；生产历史 32/50 张评估集差异需用统一 `DatasetVersion` 重新验收。

# RUN-0319 真实 BM1688 制品与边端验证审计（2026-09-20）

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R06 模型算法 | WP3-HIL-01 | 只读核对 `export_id=226` 的 PT、ONNX、FP32/FP16/INT8 BModel、同批评估和掉点报告 | 三精度真实制品存在；mAP50 掉点约 `0.014`，均为人工复核，不得发布 |
| R07 BM1688 边端 | WP3-HIL-01 | 核对 BM1688 设备身份、`bmrt_test`、BMCV 三精度执行和制品摘要 | 三精度加载/执行成功；真实设备证据成立，P95/短稳未完成 |
| R09 测试 | WP5 | 对比固定集、同批评估、阈值和质量报告口径 | 发现编译固定集 32 张与边端同批 50 张不一致，发布证据不完整 |
| R10 运维 | OPS | 通过 SSH `12043` 对生产目录只读取证 | 未改生产开关、未重启、未部署；生产版本漂移仍存在 |

- 关键证据：`/data/labeIU/labeIuMedia/yolo_bm1688_exports/226/manual_edge_validation/job-12688/edge_ssh_pull/output/{summary.json,board_report.json,bmodel_sop_eval.json}`；同批对比 `manual_edge_validation/job-12688/server_eval/same_batch_pt_bmodel_comparison.json`。
- 真实硬件执行结论：BM1688 主机名 `bm1688`，设备序列号 `9b010059325030333200b2ce56a37a00`，三种 BModel 的 `bmrt_test` 均返回 0。
- 放行结论：真实硬件执行已证实，但质量门禁为 `review`，固定评估集口径和 P95/短稳未完成，不能进入 AICM 发布或本平台公开下载。
- 独立记录：`labelu/docs/7gai_run_0319_real_bm1688_export_226_audit.md`。
- 状态：生产 L3 部分完成；发布门禁阻断；未提交、未推送、未部署。

# RUN-0318 生产只读路由复核（2026-09-20）

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构/R04 后端 | WP-生产版本对齐 | 对账生产探针、一期主链路和退休边端 OpenAPI 路径 | 生产仍为旧版本，脚本退出码 `1` |
| R09 测试 | WP5 | 执行 GET-only 生产路由审计并保存 JSON 证据 | `/healthz`、`/readyz`、`/version` 非 JSON；四个一期路由 404 |
| R10 运维 | OPS | 核对部署和反向代理边界，并通过 SSH `12043` 只读定位实际运行目录 | Nginx `10000→127.0.0.1:10001` 正常；`labelu.service` 仍加载旧源码，需正式提交后切换 |

- 证据：`.tmp/evidence/run0318/production-readonly-route-audit.json`。
- SSH 只读证据：主机 `ecm-9baa`；服务工作目录 `/data/labeIU/labeIuMedia`；实际源码 `/data/labeIU/labeIuproject/labeIU`；服务启动时间 `2026-09-17 22:28:21 CST`。
- 独立记录：`labelu/docs/7gai_run_0318_production_readonly_recheck.md`。
- 状态：生产 L0 阻断；未提交、未推送、未部署。

# RUN-0317 一期剩余缺口优先级审计（2026-09-20，只读）

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R04/R05/R06/R07/R08/R09/R10 | WP0-WP5/OPS | 对照六菜单按钮、自动回流、PT→ONNX→三精度 BModel、BM1688 前置、并发和生产漂移进行优先级审计 | 本地专项 `32 passed, 9 skipped, 114 warnings`；P0/P1/P2 缺口和下一工作包已固化 |

- 独立记录：`labelu/docs/7gai_run_0317_remaining_gap_priority_audit.md`。
- P0：生产版本/反向代理漂移；真实训练、TPU-MLIR、BM1688 证据缺失。
- P1：真实 S3/AICM 回流对账、跨进程 MySQL 并发容量、BM1688 设备短稳与故障恢复。
- P2：历史 `deployments.tsx` 兼容代码仍有 `project_id`/回滚文案，但路由已重定向、后端写操作已退休，安排后续清理。
- 状态：未修改代码、未连接生产、未提交、未推送、未部署。

# RUN-0316 前端静态构建与真实浏览器菜单验收（2026-09-20）

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R08 前端/UI | WP4 | 重新执行前端生产构建，将源码同步到 `labelu/internal/statics` | 构建完成，73 个 gzip 静态资源同步 |
| R09 测试 | WP5 | 执行六菜单、弹窗、兼容跳转、中文和登录后全路由浏览器验收，并运行统一本地门禁 | 平台壳 `9 passed`；登录后全路由/文档 `2 passed`；统一门禁 `blocking=0` |
| R01/R03 | WP0/WP2 | 将过时的项目接口测试契约改为数据集/模型版本契约 | 符合一期无项目/业务空间方案 |
| R10 运维 | OPS | 隔离 SQLite 服务和外部依赖边界记录 | 未连接生产、MySQL、AICM、S3、TPU-MLIR、BM1688；未部署 |

- 独立记录：`labelu/docs/7gai_run_0316_frontend_static_build_browser_acceptance.md`。
- 证据命令：`python -m labelu.main frontend-build`；`test_7gai_platform_shell_frontend.py`；`test_7gai_platform_local_live_frontend.py`；`verify_staging_and_hil.py --environment local --run-local --check`。
- 统一门禁证据：`.tmp/evidence/run0316/local-gates-20260920T151241Z.json`。
- 当前等级：L1 本地浏览器验收；生产旧部署/反向代理版本漂移仍由 `RUN-0314` 阻断。

## RUN-0315 隔离本地服务路由与菜单 API 验收（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R04/R08/R09/R10 | WP0/WP1/WP5/OPS | 独立 SQLite 启动服务，执行未登录路由审计和管理账号主链路 GET，验证菜单/API 对齐 | L1；未登录 16 项、管理账号 20 项全部通过 |

- 服务：`127.0.0.1:9899`，`BASE_DATA_DIR=.tmp/local0315`；后台 Watcher/训练关闭。
- 证据：`.tmp/evidence/run0315/local-service-route-audit.json`、`.tmp/evidence/run0315/local-admin-readonly.json`。
- 覆盖：健康/就绪/版本、OpenAPI、任务、数据集、算法目录、训练作业、模型版本、发布模型、BM1688、边端验证、第三方 S3 配置、工作台。
- 生产未连接、未写入、未迁移、未部署；真实 MySQL/GPU/TPU-MLIR/BM1688/AICM/S3 未验收。
- 独立记录：`labelu/docs/7gai_run_0315_local_service_route_and_menu_api_acceptance.md`。
- 状态：未提交、未推送、未部署；等待预生产入口和用户明确“提交代码”。

## RUN-0314 生产只读路由与 OpenAPI 版本漂移复核（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R03/R04/R08/R09/R10 | WP0/WP1/WP5/OPS | 使用只读脚本核验生产 JSON 探针、一期主链路路由和退休设备写入口 | 生产 L0 阻断；健康探针为 HTML，主链路缺路由，退休写路径仍登记 |

- 命令：`python scripts/staging_acceptance.py --readonly-route-audit --base-url http://113.141.72.175:10000 --timeout 10 --evidence .tmp/evidence/run0314/production-readonly-route-audit.json`。
- 证据：`/healthz`、`/readyz`、`/version` 为文本 HTML 200；OpenAPI 缺少 `/api/v1/algorithms`、`/api/v1/datasets`、`/api/v1/model-versions`、`/api/v1/workspace/summary`；仍登记 `/api/v1/edge-device-jobs*`。
- 退出码：`1`，表示阻断项存在；未登录、未写入、未触发训练/编译/发布。
- 下一依赖：预生产后端/Nginx 版本对齐、JSON 探针、主链路路由和退休写入口收口；完成后使用同一脚本复验。
- 独立记录：`labelu/docs/7gai_run_0314_production_readonly_route_audit.md`。
- 状态：未提交、未推送、未部署；生产 Worker 保持失败关闭。

## RUN-0313 本地统一验收门禁与证据指纹（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R06/R07/R08/R09/R10 | WP0/WP2/WP3/WP5/OPS | 执行本地统一验收脚本，生成工作树指纹、逐项日志和 SHA256 证据 | L1；8 项全部通过，外部环境未执行 |

- 命令：`python scripts/verify_staging_and_hil.py --environment local --evidence-root .tmp/evidence/run0313 --run-local --check`。
- 通过项：迁移唯一 head、空库重复升级、前端外部门禁语义、去项目化审计、证据完整性、探针证据契约、Provider 契约、板端协议契约。
- 汇总：`blocking=0`，退出码 `0`；工作树指纹 `7bfe5f4489fe76e66ced2362fa0214090a5a8e832e409d60deb35efc72f6`。
- 真实 GPU、TPU-MLIR、BM1688、MySQL、AICM、S3、systemd、生产浏览器和生产部署未执行，不能升为 L2/L3/L4。
- 独立记录：`labelu/docs/7gai_run_0313_local_gate_evidence.md`。
- 状态：未提交、未推送、未部署；生产 Worker 和自动训练/边端开关保持失败关闭。

## RUN-0312 工具链与真实设备入口环境核验（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R06/R07/R09/R10 | WP3/WP5/OPS | 只读核验本机工具、代码登记的 GPU/TPU-MLIR/BM1688 入口和远程 SSH 可达性；固化真实证据边界与下一步清单 | L0 环境审计；本机缺 TPU-MLIR/BModel/MySQL 工具，BM1688/生产 SSH 探测超时，真实环境未完成 |

- 本机可用：Python、`nvidia-smi`、Docker、SSH/SCP。
- 本机缺失：`tpu_mlir`、`model_deploy`、`bmrt_test`、`mlir_shell`、`mysql`、`mysqld`。
- 配置登记：TPU-MLIR `/data/labeIU/tpu-mlir`；BM1688 `10.10.11.26:22`、远端容器 `bm1688`；生产回连地址 `http://113.141.72.175:10000`；GPU 训练节点由 `remote_train_nodes` 治理配置提供。
- 只读探测：`linaro@10.10.11.26` 与 `root@113.141.72.175` 均 `Connection timed out`；未执行远程写入。
- 结论：不能把本机工具存在、配置路径、HTTP 200 或本地 Worker 测试替代真实 PT→ONNX→三精度 BModel、BM1688、GPU、MySQL 或生产证据。
- 开关：训练、BM1688 编译、边端验证、Board Agent 主路径、S3 自动回流继续失败关闭。
- 独立记录：`labelu/docs/7gai_run_0312_toolchain_device_environment_audit.md`。
- 状态：未提交、未推送、未部署；等待可达真实环境证据和用户明确“提交代码”。

## RUN-0311 BM1688 模型目录并发摘要合并（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03/R04 | 模型中心服务并发控制 | 同用户、同筛选条件的全局摘要计算采用进程内 single-flight；完成后立即清除，不改变数据口径 | 本地代码完成 |
| R09 | 性能回归 | 页内数据、全局摘要和并发只执行一次专项回归，并完成全量回归 | 定向 `39 passed, 1 skipped`；全量 `2707 passed, 56 skipped, 420 warnings` |
| R10 | 生产放行 | 明确多进程、MySQL、连接池和真实 P95 必须重新测量 | 生产未部署 |

- 独立记录：`labelu/docs/7gai_run_0311_bm1688_catalog_singleflight.md`。
- 本轮未修改生产、未迁移数据库、未提交、未推送、未部署。

## RUN-0310 生产模型中心并发性能只读采样（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03/R04/R09/R10 | 模型中心列表接口并发性能 | 管理员只读采样任务、发布模型、BM1688 导出列表；定位分页后全量摘要计算路径；补充 5 并发实测 | 生产 L0；BM1688 列表为 P1 性能阻断 |
| R01/R02/R08 | 页面响应契约 | 保持列表摘要字段语义和筛选/分页行为不变，下一轮拆分页数据与全局摘要时复核页面状态 | 待实现 |

- 独立记录：`labelu/docs/7gai_run_0310_production_catalog_concurrency_probe.md`。
- 证据：BM1688 导出列表单请求中位 `1644ms`；5 并发单请求 `8142-8674ms`，总耗时 `9458ms`。
- 本轮仅 GET/管理员只读，未修改代码、数据库、配置或生产服务；性能优化与部署待下一工作包。

## RUN-0309 生产只读放行准备审计（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03/R04/R09/R10 | 生产路由、认证、开关和设备只读核验 | 复核 `/openapi.json`、未登录内部回流接口、管理员列表接口、自动训练/BM1688/S3/AICM 配置摘要和 SSH 探针 | 生产 L0；发现内部接口未鉴权和本地/线上漂移 |
| R05/R06/R07 | 外部平台、模型和边端放行前置 | 确认 AICM 配置就绪但未对账，BM1688/边端开关关闭，SSH `probe_status=fail`、`auto_run_ready=false` | 未达到 L2/L3 |

- 独立记录：`labelu/docs/7gai_run_0309_production_readonly_readiness_audit.md`。
- 关键阻断：线上 `/internal/v1/experiment-datasets` 未登录可读；必须先部署本地服务鉴权并重新核验。
- 本轮仅 GET/登录只读，未创建任务、未上传、未触发训练/编译/发布、未修改生产配置、未提交代码。

## RUN-0308 角色团队本地总验收（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01-R10 | 全一期菜单、按钮、Worker、模型链、边端链、回流链和并发门禁收口 | 统一核对两类用户、六个一级菜单、任务→冻结数据→训练→PT/ONNX/BModel→BM1688→AICM/平台下载、第三方 S3 自动回流和失败关闭 | 本地 L1 |
| R09 测试 | 全量回归、编译和发布门禁 | `2706 passed, 56 skipped, 420 warnings`；`compileall`、`git diff --check`、`release_gate_valid=True` | 通过 |
| R10 运维 | 生产边界和放行条件 | 生产仍未部署；真实 MySQL、TPU-MLIR、BM1688、S3、AICM、systemd 和浏览器证据列为前置 | 未放行 |

- 独立记录：`labelu/docs/7gai_run_0308_role_team_local_acceptance.md`。
- 关联记录：`RUN-0300` 至 `RUN-0307`；迁移唯一 head `ze1f2a3b4c5d`。
- 本轮未提交、未推送、未部署、未写生产数据库。只有在用户明确“提交代码”后，才进入备份、迁移、预生产和生产发布流程。

## RUN-0304 第三方 S3 问题图片自动回流 Worker 审计（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R04/R05/R06/R09/R10 | WP1/WP2/WP4/WP5/OPS | 审计 `/strategy-integration-s3-config` 对应 S3 回流链：自动接收、包幂等、对象校验/去重、AICM `model_set_id` 对齐、聚合任务、预标注、等待唤醒、租约/fencing、重启接管与并发失败关闭 | 本地 Worker 专项 `13 passed`；无新增安全最小修复；生产 L2/L3 未完成 |

- 独立记录：`labelu/docs/7gai_run_0304_s3_evolution_worker_audit.md`。
- 关键边界：管理人员只处理异常、规则和抽查；正常第三方包由独立 Worker 自动处理，不提供人工逐包建任务的必经步骤。
- 真实环境边界：未连接生产页面写接口、生产 MySQL、第三方 S3、AICM、systemd 或 BM1688；未执行迁移、未提交、未推送、未部署。
- 放行前置：同版本 MySQL 备份/迁移/锁等待、真实 S3 对象变更、AICM/本地目录/metadata 三方对账、重启接管和 `2/10/20/50` 并发容量证据；`EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED` 继续保持 `false`。

# RUN-0303 模型中心 AICM 发布独立 Worker

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R03/R04/R06 | WP3 | AICM 发布请求保存完整 payload；开关开启后 Web 只入队，独立 Worker 领取、续租、fencing、重启接管；复用本平台模型目录原子安装 | 本地代码完成 |
| R09 测试 | WP5 | 发布 Worker 开关、ready 失败关闭、单次领取、payload 传递、缺 payload 失败及既有 AICM 门禁回归 | `43 passed`；compileall 通过 |
| R10 运维/R07 边端 | OPS | 明确真实 AICM、同版本 MySQL、共享目录、systemd、并发、重启和三方对账前置 | 生产未验收，开关保持关闭 |

- 本轮未新增迁移；复用 `z9b0c1d2e3f4_add_aicm_publish_claim_lease.py` 的发布租约字段。
- 外部平台已经下载的模型不回滚；问题模型只允许关闭 LabelU 后续下载并发布替代模型。
- 独立记录：`labelu/docs/7gai_run_0303_aicm_publish_worker.md`。
- 本轮未连接生产、未提交、未推送、未部署。

# RUN-0300 四类 Provider 预标注独立 Worker

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R06 | WP1/WP2 | TagTool、LocateAnything、VLX-Seek、Rex-Omni 作业保存完整 payload，统一使用租约、续租和 fencing；VLX/Rex 复用 LocateAnything 作业表并按 provider 分流 | 本地代码完成 |
| R09 测试 | WP5 | 提供方隔离、过期接管、旧 token 拒绝、Worker 开关和 VLX 分流回归 | `27 passed`；compileall 通过 |
| R10 运维 | OPS | 明确共享权重目录、真实模型、MySQL/InnoDB、systemd 和长批次续租前置 | 生产未验收，开关保持关闭 |

- 修改：三条 Provider 服务、两个作业模型/CRUD、Worker 运行时、Worker 脚本、配置和 `ze1f2a3b4c5d` 迁移。
- 旧 Web 线程路径只在独立 Worker 开关关闭时保留；开关开启后 API 只入队，避免双消费者。
- 续租在每个样本开始前执行；单次模型调用超过租约时长时没有后台心跳线程，生产需按真实 P95 调整租约并完成长请求接管演练。
- 独立记录：`labelu/docs/7gai_run_0300_provider_preannotate_worker.md`。
- 本轮未连接生产、未提交、未推送、未部署。

# RUN-0301 训练 Worker 独立执行与生产边界审计

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R06 | WP2/WP3 | 核对 YOLO/车牌专用 `yolo_train_worker` 与通用 `training.worker.command.v1` Outbox 的边界；确认训练输入、冻结数据、队列/执行租约和 fencing 复用 | 本地契约完成 |
| R09 测试 | WP5 | 领取竞争、过期接管、旧 token 拒绝、资源并发和 20 进程竞争 | `54 passed` |
| R10 运维 | OPS | 核对真实 GPU、systemd、MySQL/InnoDB 和重启接管前置 | 未完成；生产开关保持关闭 |

- 独立记录：`labelu/docs/7gai_run_0301_training_worker_audit.md`。
- 两条训练 Worker 不是同一个消费者，菜单、部署和验收文档必须分开描述。
- 本轮仅本地代码/测试审计，未连接生产、未提交、未推送、未部署。

# RUN-0302 生产只读现状与本地 Worker 契约漂移

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R09/R10 | OPS/生产对账 | GET-only 复核线上健康、OpenAPI、一期路由、内部实验数据接口和历史边端写入口；记录线上 `/internal/v1/experiment-datasets` 未认证可读与本地鉴权契约不一致 | 生产 L0；发现漂移，禁止放量 |

- 生产地址：`http://113.141.72.175:10000`；OpenAPI 295 条路径；`/health` 返回 SPA HTML。
- 未认证 `/internal/v1/experiment-datasets` 返回 200 真实数据，不能视为本地内部服务鉴权已部署。
- 线上仍登记 `/api/v1/edge-device-jobs`、`claim`、`result` 三条历史写路径，需部署后再次确认退休隔离。
- 独立记录：`labelu/docs/7gai_run_0302_production_readonly_drift_after_worker_contract.md`。
- 本轮仅只读探针和文档，未登录、未写生产、未提交、未推送、未部署。

# RUN-0295 训练冻结数据逐样本内容校验

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R02/R03/R06 | WP1/WP2 | DatasetVersion 清单保存图片、投影标注和原始标注摘要 | 本地契约完成 |
| R04 后端 | WP1 | 训练导出前校验冻结样本内容，变化时失败关闭 | 本地代码完成 |
| R09 测试 | WP5 | 冻结清单、图片/标注变化和标签子集回归 | 28/28 通过；compileall 通过 |
| R10 运维 | OPS | 同版本 MySQL、对象存储、训练节点和吞吐证据 | 预生产/生产待执行 |

- 修改：`dataset.py`、`yolo_train.py`、`test_yolo_train_frozen_manifest.py`。
- 本地 L1；未连接生产、未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0295_training_frozen_content_gate.md`。

# RUN-0296 策略演化创建接口服务鉴权

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R04 后端/R05 外部平台 | WP2/WP4 | 三个策略演化内部创建 POST 接口统一复用服务令牌/HMAC 鉴权 | 本地代码完成 |
| R03 架构 | WP2 | 服务身份与浏览器用户身份隔离；缺凭据失败关闭 | 本地契约完成 |
| R09 测试 | WP5 | 鉴权专项 7/7；策略演化路由回归 35/35 | 通过 |
| R10 运维 | OPS | 真实密钥、重放、限流和生产监控 | 待预生产/生产 |

- 修改：`strategy_evolution.py`、`test_internal_service_auth.py`。
- 本地 L1；未连接生产、未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0296_strategy_internal_create_auth.md`。

# RUN-0294 前端系统管理路由守卫与两类角色入口闭环

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01 产品 | WP0 | 固化标注人员业务权限与管理人员系统管理边界 | 本地契约完成 |
| R08 前端/UI | WP4 | `/admin` 及历史兼容跳转增加管理人员守卫；中文加载、无权限提示 | 本地代码完成 |
| R09 测试 | WP5 | 标注人员重定向、管理人员放行、身份查询失败关闭 | Vitest 3/3；布局与管理页 7/7 |
| R04 后端/R10 运维 | WP1/OPS | 保留服务端 401/403，准备两类真实账号回放 | 预生产/生产待执行 |

- 修改：`RequireManager.tsx`、`routes.tsx`、`RequireManager.test.tsx`。
- 本地：`pnpm run type-check` 通过；本地 L1。
- 生产边界：未连接、未登录、未写入、未迁移、未提交、未推送、未部署；生产仍为旧部署。
- 独立记录：`labelu/docs/7gai_run_0294_frontend_manager_route_guard.md`。

# RUN-0293 第三方 S3 附件来源身份与同名去重（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R05/R09/R10 | WP4/数据来源身份 | `TaskAttachment` 保存 S3 桶、对象键、版本、ETag、来源 SHA-256；同内容重试复用，同名不同对象使用稳定后缀；新增迁移、来源索引和专项测试 | 本地 L1：回流 25 passed，迁移 3 passed，迁移闸门 17 passed；真实 MySQL/S3/Worker 并发待验收 |

- 独立记录：`labelu/docs/7gai_run_0293_s3_attachment_source_identity.md`。
- 范围边界：未改 S3 回流业务分流、任务创建、预标注、标签契约或 Worker 状态机；未提交、未推送、未部署。
- 生产放行前置：同版本 MySQL 迁移/备份恢复、真实 S3 ETag/版本对象、同名并发下载和大文件摘要吞吐必须补证据。

# RUN-0291 模型中心 AICM 编号与平台下载目录绑定

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R04/R05/R06/R08/R09 | WP3/WP5 | 发布请求支持 AICM 编号、环境和包路径；服务端校验五项质量门禁及 BM1688 三精度，自动生成并保留平台 ZIP，登记 `PublishedModelCatalog` 并回写模型版本目录编号；React 页面增加居中发布弹窗 | 本地 L1：模型路由专项 `7 passed`、前端模型中心 `11 passed`；AICM/TPU-MLIR/BM1688/生产未验收 |
| R03/R07/R10 | WP3/OPS | 固化 AICM 环境+编号唯一身份、原子安装、内容摘要冲突 409 和外部下载不可回滚边界 | 本地契约通过；生产继续失败关闭 |

- 独立记录：`labelu/docs/7gai_run_0291_model_center_aicm_catalog_binding.md`。
- 生产边界：只实现“接收 AICM 已返回编号并登记平台目录”；没有调用真实 AICM，没有提交、推送或部署。
- 下一依赖：React 模型中心增加居中发布弹窗；真实 AICM 回调、固定评估集、TPU-MLIR 和 BM1688 真机证据完成后才进入 L2/L3。

# RUN-0290 Board Agent 主动拉取设备通道

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R04/R07/R08/R09 | WP3/WP5 | 设备配置页增加 SSH/板端主动拉取通道；板端不要求 SSH 字段，按心跳和环境状态显示在线；SSH 通道缺凭据失败关闭；详情和编辑保持居中弹窗 | 本地 L1：扩展前端 `23/23`，设备注册/调度专项通过 |
| R03/R10 | WP3/OPS | 核对设备 ID、心跳 TTL、调度状态和 Board Agent 开关失败关闭 | 本地契约完成；真实 MySQL、Board Agent、BM1688、systemd 和生产未验收 |

- 独立记录：`labelu/docs/7gai_run_0290_board_agent_device_channel.md`。
- 生产边界：RUN-0288 已证明生产仍为旧部署；本轮未连接、未写入、未迁移、未提交、未推送、未部署。
- 下一依赖：真实 BM1688 Agent 身份/验签/断网/重启证据，以及固定评估集、标签和三精度质量门禁接入 Board Agent 包。

# RUN-0289 第三方 S3 回流等待状态自动唤醒（2026-09-20）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R09/R10 | WP4/OPS | 增加等待模型、等待分类、等待样本的冷却扫描重入队；模型目录注册/同步、分类新增/更新和重复包到达可主动唤醒；保留租约/fencing 失败关闭 | 本地 L1：Worker 13 passed，S3 入口 39 passed，模型目录 23 passed；生产未提交、未部署，真实 MySQL/S3/AICM/Worker 待验收 |

- 独立记录：`labelu/docs/7gai_run_0289_s3_waiting_state_auto_wakeup.md`。
- 关键配置：`EXPERIMENT_DATASET_WAITING_REQUEUE_SECONDS=30`（默认值；生产需结合 S3 延迟和数据库负载复核）。
- 范围边界：本轮未改附件去重、模型解析顺序、任务创建和预标注规则。
- 下一依赖：用户明确“提交代码”后，按生产备份、迁移、部署和真实 Worker 并发/重启接管清单执行。

# RUN-0297 策略回流首次并发插入竞态

| 责任角色 | 工作包 | 验收状态 |
|---|---|---|
| R03 架构/R04 后端 | WP1/WP2 | `strategy_task_integration` 首次创建捕获唯一键竞争，事务回滚后加锁回读赢家并复用请求摘要校验 | 本地代码完成 |
| R09 测试 | WP5 | 竞争回读赢家、赢家缺失失败关闭及原策略回流路由回归 | `11 passed` |
| R10 运维 | OPS | 同版本 MySQL 2/10/20 并发及统一导入副作用对账 | 尚未执行 |

- 修改：`labelu/internal/application/service/strategy_task_integration.py`、`labelu/tests/internal/application/service/test_strategy_task_integration_create_race.py`。
- 本轮无数据库迁移；本地 L1，未连接生产 MySQL/S3，未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0297_strategy_integration_first_insert_race.md`。

# RUN-0281 BM1688、Board Agent/SSH、三精度与 AICM 生产差距审计

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R06/R07/R09/R10 | WP3/WP5/OPS | 只读核对 BM1688 Profile、三精度、Board Agent/SSH 通道、设备租约、AICM 本地目录和生产只读证据；定义最小真实 HIL 放行包 WP3-HIL-01 | 本地 L1；生产 L2 失败关闭；未修改业务代码 |

- 独立记录：`labelu/docs/7gai_run_0281_bm1688_board_agent_production_gap_audit.md`。
- 生产证据：`.tmp/7gai-production-readonly-route-audit-0279.json`；GET-only、未认证、未执行退休写探针，OpenAPI 295 条，健康接口为 SPA HTML，四个一期路由缺失，旧 `edge-device-jobs` 写路径仍暴露。
- 关键判断：Board Agent 统一路径、三精度和 AICM/本地下载目录已有本地契约，但 `BOARD_AGENT_PRIMARY_ENABLED=false`、设备默认 SSH、独立 Worker 默认关闭；当前调度器仍对所有通道要求 SSH 凭据/探测，未形成无 SSH 的 Agent 就绪证据。
- 推荐下一工作包：WP3-HIL-01，使用一个真实 YOLO11 代表模型和一台真实 BM1688 完成真实 TPU-MLIR、三精度、固定评估集、设备 fencing 和 AICM/LabelU/S3 对账；Board Agent 只有在在线/身份/验签/断网证据完成后才切主路径。
- 生产边界：未连接生产 MySQL、TPU-MLIR、BM1688、AICM 或 S3；未执行迁移、未提交、未推送、未部署。等待用户明确“提交代码”。

# RUN-0277 车牌训练前端测试契约与一期矩形框规则对齐（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R06/R08/R09 | WP0/WP1/WP3/WP5 | 修正过时的前端静态断言，区分一期普通矩形框新建与历史复合车牌兼容查看 | 车牌前端专项 `8 passed, 113 warnings`；全量 `2662 passed, 56 skipped, 410 warnings` |

- 独立记录：`labelu/docs/7gai_run_0277_plate_frontend_contract_regression.md`。
- 关键规则：一期不使用多边形、四角点、Pose、OCR、颜色或类型训练；历史复合字段只用于兼容展示/历史迭代。
- 生产边界：未连接生产、未修改业务接口/数据库、未提交、未推送、未部署。

# RUN-0276 任务训练编译边端与 S3 回流并发缺口审计（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R09/R10 | WP1/WP2/WP3/WP4/WP5/OPS | 对照任务、训练、BM1688、边端和 S3 回流的重复提交、租约/fencing、数据库锁等待与 `2/10/20/50` 并发边界 | 本地专项 `34 passed, 116 warnings`；无安全最小业务修复；真实 MySQL/设备容量仍阻断 |

- 独立记录：`labelu/docs/7gai_run_0276_concurrency_gap_audit.md`。
- 关键发现：任务创建和手工训练提交未定义请求幂等键，租约只能防止同一对象重复执行，不能防止重复创建；修复需先确定 API/唯一索引语义。
- 证据边界：训练本地 20 进程、S3 本地 2/10/20 单赢家通过；BM1688/边端为函数级租约测试；通用 MySQL 矩阵仅操作合成表，不能替代四类业务表。
- 生产边界：未连接生产或业务 MySQL，未执行迁移、未修改代码、未提交、未推送、未部署。

# RUN-0275 生产只读路由审计模式与旧部署证据

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R09/R10 | WP2/WP5/OPS | 新增 `--readonly-route-audit`，只发送 GET，不登录、不带请求体、不执行退休写探针；复用统一 OpenAPI 契约核对生产 | 本地专项 `15 passed`，compileall 通过；生产证据 `passed=false`，失败关闭 |

- 对应主方案条款：15.8、15.10、15.11、15.12；角色执行手册第 4、5 节。
- 独立记录：`labelu/docs/7gai_run_0275_readonly_route_audit_mode.md`。
- 证据：`.tmp/7gai-production-readonly-route-audit-0275.json`；`method_allowlist=[\"GET\"]`、`authenticated=false`、`retired_write_probes_executed=false`。
- 生产结果：OpenAPI 路径数 `295`；一期 `algorithms/datasets/model-versions/workspace/summary` 缺失；旧 `edge-device-jobs` 写路径仍暴露；健康接口返回 SPA HTML。
- 生产边界：未登录、未写数据库、未上传、未执行迁移、未提交、未推送、未部署；不得再次向生产发送旧写探针。

# RUN-0274 生产只读路由与旧写入口复核

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R09/R10 | WP1/WP2/WP3/WP4/WP5/OPS | 只读核对生产 OpenAPI、一期新路由和退休边端写入口；不发送任何生产写请求 | 线上版本 `1.4.5`；四个一期路由缺失；旧 `edge-device-jobs` POST 仍暴露于 OpenAPI；生产失败关闭 |

- 独立记录：`labelu/docs/7gai_run_0274_production_readonly_route_recheck.md`。
- 只读结果：`/openapi.json` 返回 `200` JSON；目标新路由 GET 均 `404`；未调用旧 POST 探针。
- 生产边界：未登录、未写数据库、未上传、未执行迁移、未提交、未推送、未部署。

# RUN-0273 本地验收运行手册与统一门禁闭环

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R03/R07/R08/R09/R10 | WP2/WP3/WP4/WP5/OPS | 补齐 staging/HIL 运行手册；统一本地门禁生成工作树指纹、日志和摘要；明确真实 API、axe/键盘、BM1688 和失败关闭边界 | `test_local_gate_inventory.py 3 passed`；8 个 local_windows 门禁全部 PASSED；`blocking=0` |

- 对应主方案条款：15.8、15.10、15.11、15.12。
- 独立记录：`labelu/docs/7gai_run_0273_local_acceptance_runbook_and_gates.md`。
- 证据：`.tmp/7gai-evidence-0273b/local-gates-20260920T062723Z.json`，包含每个门禁的代码摘要、工作树指纹和日志 SHA256。
- 全环境前置检查 `blocking=9`，均为未配置预生产/真实硬件/Linux 发布环境的外部条件；BM1688 HIL 明确为 `SKIPPED_UNAVAILABLE`，未被当作通过。
- 生产边界：本轮未连接 staging、生产、MySQL、S3、AICM 或 BM1688；未执行迁移、未提交、未推送、未部署。

# RUN-0272 迁移证据来源可追溯闸门

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03 架构、R04 后端工具、R09 测试、R10 运维 | WP2/WP5/OPS | 三类迁移证据统一增加隔离沙箱、MySQL 版本、脚本版本、生成时间和代码提交摘要；矩阵测试隔离真实探针 | 本地专项 `18 passed`；compileall 通过；正式迁移继续失败关闭 |

- 独立记录：`labelu/docs/7gai_run_0272_migration_evidence_provenance_gate.md`。
- 关键约束：缺少 `evidence_meta`、错误沙箱库名、`unknown/workspace/dirty` 提交摘要或非四档矩阵，发布闸门均拒绝。
- 后端全量回归：`2659 passed, 56 skipped, 410 warnings`，退出码 `0`。
- 前端：`npm run type-check` 通过，`npm run build` 通过；扩展契约 `22 passed`。
- 只读迁移发布闸门实跑退出码 `1`，阻断原因符合预期，未执行 Alembic DDL。
- 生产边界：本轮未连接生产或业务 MySQL，未执行正式迁移，未提交、未推送、未部署。

# RUN-0265 本机 MySQL 沙箱与迁移演练脚本只读审查（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R09/R10 | WP2/WP5/OPS | 检查本机 MySQL/Docker 能力、监听与沙箱环境变量；审查首次/重复升级、备份恢复、锁等待和 `2/10/20/50` 演练脚本覆盖 | MySQL90 正在运行但不是已证明沙箱；Docker 引擎不可用；演练脚本仅部分覆盖；迁移仍失败关闭 |

- 独立记录：`labelu/docs/7gai_run_0265_mysql_sandbox_and_rehearsal_audit.md`。
- 本地测试：迁移/演练/发布闸门专项 `29 passed, 124 warnings`。
- 关键事实：`0.0.0.0:3306` 和 `[::]:3306` 正在监听；当前没有 `LABELU_MYSQL_REHEARSAL_URL` 或 `LABELU_MIGRATION_SANDBOX_URL`；仓库 `.env` 的 `DATABASE_URL` 为业务库 `labelu`，本轮未连接。
- 结论：现有脚本可验证合成结构幂等和单档通用锁竞争，但不实际执行备份恢复、不自动生成四档并发矩阵，也未覆盖四类业务表；未修改数据库、未生成迁移、未提交、未推送、未部署。

# RUN-0264 BM1688 三精度制品、设备验证与下载链只读审查（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R06/R07/R09/R10 | WP3 | 审查 FP32/FP16/INT8 制品清单、BM1688 设备注册、统一/兼容 Board Agent、普通下载和验证包下载链 | 本地契约具备；发现普通 BModel `filename` 路径 containment/manifest 白名单缺口；真实 BM1688、生产 Worker、签名密钥和 AICM/S3 对账未验收 |

- 独立记录：`labelu/docs/7gai_run_0264_bm1688_artifact_device_download_readonly_audit.md`。
- 本地测试：`31 passed, 1 skipped`；硬件 HIL 跳过，未连接真实 BM1688。
- 关键事实：统一 Board Agent 有设备 ID、短期任务令牌、执行租约、fencing 和证据门禁，但 `BOARD_AGENT_PRIMARY_ENABLED` 默认关闭；旧兼容链仍只有静态板端令牌。
- P0/P1：普通 `/yolo-bm1688-exports/{id}/bmodel?filename=...` 未把解析后的路径限制在当前导出 `models/` 根目录，且未限定 manifest 白名单；应先修复路径穿越/绝对路径读取和制品摘要校验。
- 生产边界：未连接生产、未写数据库、未修改业务代码、未提交、未推送、未部署；在真实板卡、systemd、签名公钥、三精度加载、断网/重启/fencing 和 AICM/S3 对账完成前，不得标记为 BM1688 生产闭环。

# RUN-0261 异步评估执行代次本地实现（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R06/R09/R10 | WP2/WP3/WP5/OPS | 评估阶段代次、报告幂等、旧代次迟到拒绝、候选迁移和专项测试 | 专项 `16 passed`；代码编译通过；正式迁移和生产未放行 |

- 独立记录：`labelu/docs/7gai_run_0261_eval_generation_implementation.md`。
- 关键约束：历史 `operation_id=NULL` 只读兼容；新评估必须携带代次；不新增菜单/角色。
- 复验结果：后端全量 `2651 passed, 56 skipped, 410 warnings`；前端类型检查通过，生产构建 `4094 modules transformed`。
- 生产边界：候选迁移尚未进入正式 Alembic，未连接 MySQL、未提交、未推送、未部署。

# RUN-0260 生产只读探针复核（2026-09-20，生产 L2 失败关闭）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R09/R10 | WP2/WP5/OPS | 重跑生产健康、版本、OpenAPI、退休入口和未授权访问探针 | 生产仍为旧部署；退出码 `1`；未执行登录和写入 |

- 独立记录：`labelu/docs/7gai_run_0260_production_readonly_recheck.md`。
- 证据文件：`.tmp/production-readonly-acceptance-20260920-r3.json`。
- 结论：必须先完成提交授权、备份、迁移闸门和部署，不能把本地 L1 当作生产完成。

# RUN-0259 角色团队联合本地发布基线（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01-R10 | WP0-WP5/OPS | 联合核对菜单、任务标注、训练、模型中心、进化中心、BM1688 契约、权限、并发和交付记录 | 后端 `2648 passed, 56 skipped`；专项 `104 + 63 passed`；前端类型检查和构建通过 |

- 独立记录：`labelu/docs/7gai_run_0259_role_team_local_release_baseline.md`。
- 结论：当前工作树达到本地 L1；不代表生产完成。
- 生产边界：异步评估正式迁移、同版本 MySQL 证据、TPU-MLIR、BM1688、S3、AICM、systemd 和两类生产账号仍未验收；不提交、不推送、不部署。

# RUN-0258 本地一期缺口审计（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R03/R04/R06/R07/R08/R09/R10 | WP0-WP5/OPS | 对照一期 17 项要求抽查模型中心、进化中心、自动评估、前端中文/无抽屉契约和真实环境放行边界 | 本地专项 `64 passed, 113 warnings`；未发现安全代码缺口 |

- 独立记录：`labelu/docs/7gai_run_0258_local_l1_gap_audit.md`。
- RUN-0257 异步评估代次补充审计：`labelu/docs/7gai_run_0257_eval_generation_audit.md`；核对 `YoloTrainJob`、`YoloEvalReport`、评估服务和 Alembic 后确认 `operation_id` 字段、报告唯一幂等键、当前代次条件完成尚未落地。
- 本地证据：评估/迁移/执行 fencing 专项 `23 passed, 123 warnings`；迁移闸门总体 `valid=false`，缺少沙箱 URL、备份恢复、锁等待和 `2/10/20/50` 并发证据。
- 结论：本轮不新增业务代码；剩余问题均需要同版本 MySQL、真实 GPU/TPU-MLIR、BM1688、S3、AICM、生产部署和两类账号证据，继续失败关闭。
- 状态：未连接生产、未提交、未推送、未部署。

# RUN-0255 生产预发布验收探针（2026-09-20，生产只读 L2）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R09/R10 | WP2/WP5/OPS | 使用仓库验收脚本核对生产探针、OpenAPI、退休写入口和未授权访问 | 健康探针/路径对账失败关闭；未执行登录和写入 |
| R01/R04/R05/R06/R07/R08 | WP0-WP4 | 对照一期菜单、任务、模型、S3/AICM 和 BM1688 主链 | 生产仍为旧部署，未进入业务回放 |

- 独立记录：`labelu/docs/7gai_run_0255_production_staging_probe.md`。
- 本地证据：`.tmp/production-readonly-acceptance-20260920.json`。
- 本地契约回归：角色执行手册与验收脚本 `12 passed, 113 warnings`。
- 结论：生产不能放行；未提交、未推送、未部署。

# RUN-0254 当前完成审计矩阵（2026-09-20，本地 L1 / 生产未放行）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R03/R04/R06/R07/R08/R09/R10 | WP0-WP5/OPS | 更新 17 项一期要求、证据等级、生产缺口和下一依赖 | 当前实现 L1；生产整体未放行 |

- 独立记录：`labelu/docs/7gai_run_0254_current_completion_audit.md`。
- 结论：本地代码和测试已形成闭环，但真实 MySQL、TPU-MLIR、BM1688、S3、AICM、systemd 和生产账号证据缺失。
- 状态：未提交、未推送、未部署；保持相关 Worker 失败关闭。

# RUN-0253 MySQL 沙箱启动与迁移闸门复核（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R09/R10 | WP2/WP5/OPS | 尝试启动 Docker Desktop 并重新执行迁移发布闸门 | Docker Linux 引擎不可用；闸门失败关闭 |
| R04/R06/R07 | WP2/WP3 | 保持训练、编译和边端 Worker 依赖真实证据 | 未连接真实工具链，保持关闭 |

- 独立记录：`labelu/docs/7gai_run_0253_mysql_gate_recheck.md`。
- 结论：正式 head `z9f3a4b5c6d7`、源码闭包通过，但没有沙箱 URL、备份恢复、锁等待和 `2/10/20/50` 并发证据；不连接生产。
- 状态：未提交、未推送、未部署。

# RUN-0252 生产只读路由复核（2026-09-20，生产只读 L2）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R09/R10 | WP2/WP5/OPS | 复核生产健康/就绪/版本探针、OpenAPI 和一期关键路由 | 线上仍为旧部署；四个一期 API 404 |
| R01/R05/R06/R07/R08 | WP0/WP3/WP4 | 对照菜单、S3/AICM、模型和 BM1688 生产入口 | 未进入写入或真实联调 |

- 独立记录：`labelu/docs/7gai_run_0252_production_readonly_route_recheck.md`。
- 结论：生产未加载当前工作树；不以 HTML 200 或旧 OpenAPI JSON 作为上线证据。
- 状态：未提交、未推送、未部署；等待提交授权和 MySQL/备份/部署闸门。

# RUN-0251 角色团队本地集成回归（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R03/R04/R06/R08/R09 | WP0-WP5 | 将菜单、自动进化、模型中心、训练、边端和权限契约放在当前工作树联合回归 | 后端 `2648 passed, 56 skipped, 410 warnings` |
| R09 | WP5 | 前端类型检查、生产构建和专项回归 | 类型检查通过；构建 `4094 modules transformed`；专项通过 |
| R10 | OPS | 固化真实 MySQL、GPU、TPU-MLIR、BM1688、S3/AICM 和生产账号边界 | 未执行，保持失败关闭 |

- 独立记录：`labelu/docs/7gai_run_0251_role_team_integration_regression.md`。
- 结论：当前工作树本地 L1 集成通过；不代表生产完成，不提交、不推送、不部署。

## RUN-0249 进化中心自动推进租约写入栅栏（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R03/R04 | WP4/WP5 | 自动推进每次使用唯一执行者编号；活跃租约禁止同进程重复领取；自动化快照按持有者和未过期时间条件写入 | 本地代码完成；无迁移 |
| R05/R06 | WP4 | 保持 S3 去重、AICM 模型编号、任务复用和训练触发既有链路，失租后不继续终态回调 | 业务契约未改变 |
| R09 | WP5 | 同进程竞争、过期接管、迟到写入和自然过期写入专项 | 自动推进专项 `33 passed, 113 warnings`；进化主链联合回归 `75 passed, 113 warnings` |
| R10 | WP5/OPS | 保留真实 MySQL 并发、systemd 接管和生产 Worker 放行边界 | 未连接生产，相关开关不放行 |

- 独立记录：`labelu/docs/7gai_run_0249_strategy_automation_lease_fencing.md`。
- 代码范围：`crud_strategy_task_integration.py`、`strategy_evolution.py`；测试：`test_strategy_evolution_auto_advance.py`。
- 结论：旧自动推进执行者失租或超时后不能覆盖新状态；不增加项目维度或登录角色。下游训练、编译、边端和发布仍复用各自幂等/fencing。
- 状态：未提交、未推送、未部署；未连接真实 S3/AICM/MySQL/GPU/TPU-MLIR/BM1688/systemd。

# RUN-0248 进化中心菜单权限与写操作互斥（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R01/R08 | WP0/WP4 | 第五一级菜单统一为“进化中心”；标注人员可进入并只读查看本人可见难例，管理人员保留审核与回流编排 | 页面与权限契约本地完成 |
| R03/R08 | WP4/WP5 | 聚类重建、批次生成、接受批次和冻结版本使用统一忙碌态，任一写操作执行时锁定其他写控件 | 重复点击专项通过；后端幂等仍是并发真源 |
| R09 | WP5 | 菜单、布局、进化中心页面单测、静态无项目契约、生产构建和本地浏览器验证 | Vitest `13 passed`；静态契约 `2 passed`；浏览器 `3 passed`；类型检查和构建通过 |
| R10 | WP5/OPS | 记录本地/生产边界 | 未连接生产、未提交、未推送、未部署 |

- 独立记录：`labelu/docs/7gai_run_0248_evolution_menu_role_and_action_gate.md`。
- 页面规则：详情继续使用中文居中弹窗，不增加抽屉；页面不显示项目入口，不发送 `project_id`。标注人员不显示“回流编排”和“提交审核”，管理人员可执行管理动作。
- 本地页面：`http://127.0.0.1:3014/`；浏览器数据由本地路由模拟，不是生产对象证据。
- 结论：本轮达到本地 L1；生产仍需两类真实账号、真实难例范围、快速重复点击、后端幂等和部署版本复验。

# RUN-0247 MySQL 沙箱能力探针与迁移证据边界（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R09/R10 | WP2/WP5/OPS | 检查 Docker/MySQL 能力、执行严格 URL 门禁和迁移/评估专项回归 | 本地专项 `23 passed, 123 warnings`；真实 MySQL 阻断 |
| R04/R06/R07 | WP2/WP3 | 保持 Worker、模型编译和边端验证的失败关闭边界 | 未连接真实工具链，保持关闭 |

- 独立记录：`labelu/docs/7gai_run_0247_mysql_sandbox_capability_probe.md`。
- 结论：Docker 引擎未启动且本机无 MySQL 服务，不能伪造迁移、锁等待或并发证据；不连接生产数据库。
- 状态：未提交、未推送、未部署；正式迁移候选保持阻断。

## RUN-0246 异步评估执行代次与迁移放行设计（2026-09-20，架构 L0）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04 | WP2 | 设计训练作业评估代次、报告来源代次、条件完成和旧代次拒绝规则 | 架构设计完成；未生成正式迁移 |
| R06 | WP2 | 确认全量评估、下一轮评估和质量证据继续复用同一训练作业真源 | 规则完成 |
| R09 | WP5 | 对照 RUN-0245 专项及全量回归证据，冻结迁移前测试要求，并复跑迁移图/发布闸门 | 迁移图专项 `13 passed, 121 warnings`；真实并发未执行 |
| R10 | WP5 | 明确同版本 MySQL、备份恢复、锁等待和 `2/10/20/50` 并发前置条件 | 未提供，正式迁移保持阻断 |

- 独立记录：`labelu/docs/7gai_run_0246_eval_generation_migration_design.md`。
- 结论：当前正式 head `z9f3a4b5c6d7` 不变；不把未验证字段带入生产。下一轮候选迁移编号为 `RUN-0247`，前提是 R10 提供真实沙箱证据。
- 状态：未提交、未推送、未部署；评估和迁移 Worker 保持 `fail-closed`。

## RUN-0245 异步评估跨进程条件领取闸门（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04 | WP2 | 评估状态按作业、评估类型和旧状态执行数据库比较并更新，跨进程竞争失败关闭 | 本地代码完成；无迁移 |
| R06 | WP2 | 保持训练完成后全量评估与下一轮评估的业务顺序 | 本地契约未改变 |
| R09 | WP5 | 自动评估、报告契约、条件领取和全量回归 | 专项 `17 passed, 113 warnings`；全量 `2641 passed, 56 skipped, 410 warnings`；`py_compile` 通过 |
| R10 | WP5 | 记录 MySQL 并发、备份恢复和真实 Provider 放行边界 | 未连接生产 |

- 独立记录：`labelu/docs/7gai_run_0245_async_evaluation_claim_gate.md`。
- 代码范围：`crud_yolo_train_job.py`、`yolo_train_evaluation.py`；测试：`test_yolo_auto_eval.py`。
- 结论：数据库条件领取消除了最常见的多进程重复启动竞争；评估执行代次和报告唯一幂等键尚未完成，不能把本地 L1 写成生产完成。
- 状态：未提交、未推送、未部署；下一轮 `RUN-0246`。

## RUN-0244 角色交付文档机制审计与车牌训练并发修复（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R08 前端/UI、R09 测试、R10 运维；工作包 WP0/WP1/WP2/WP3/WP4/WP5。
- 本轮交付：审计角色方案、执行手册和台账，补齐“六个一级菜单 × 每个核心按钮 × 两类用户 × 主责角色 × 工作包 × 必须证据”的统一对照；固定“进化中心”为一期菜单名称（“数据回流”仅兼容叫法），“系统管理”为一期菜单名称（“平台设置”仅兼容叫法）。同时由 R04/R06/R09 补齐车牌分类、OCR、姿态组件进度文件与 PT/ONNX 制品的 owner/token 执行世代守卫，租约查询异常失败关闭，并为 YOLO 主训练回调增加批次/阶段执行世代检查。
- 文档要求：每个代码/页面工作包必须有独立 `7gai_run_<编号>_<主题>.md`，并同步角色方案和台账；必须记录菜单按钮、对象 ID/请求编号、权限、幂等、租约/fencing、并发上限、测试原始证据、本地/预生产/生产边界、L0-L4 和提交部署状态。
- 本轮证据：`labelu/docs/7gai_run_0244_role_delivery_document_audit.md`；车牌专项 `31 passed, 116 warnings`；训练 Worker、YOLO 回调联合专项 `143 passed, 118 warnings`；`git diff --check`、目标文件 `py_compile` 通过。未连接生产写入，未提交、未推送、未部署。
- 结论：文档机制达到 L0，车牌训练和 YOLO 回调并发修复达到本地 L1；后续实现不得只写“联合回归通过”，必须按按钮和对象留存可复核证据。训练完成后的异步评估写入仍未完全统一 owner/token，整体方案仍受 RUN-0243 的 MySQL 沙箱、备份恢复、并发、真实工具链、BM1688、S3/AICM、systemd 和浏览器证据约束。
- 下一轮：`RUN-0245`。

## RUN-0243 发布闸门证据边界复核（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；工作包 WP2/WP5/OPS。
- 本轮交付：对比本地 `release_gate` 与正式 `migration_release_gate`；前者代码闭包通过，后者在缺少沙箱 URL、备份恢复、锁等待和 `2/10/20/50` 并发证据时失败关闭。
- 本地证据：`release_gate_valid=True`、`migration_heads=z9f3a4b5c6d7`；正式迁移闸门退出码 `1`，未连接数据库。
- 结论：本地迁移结构通过不等于可以升级生产；真实 MySQL 沙箱、备份恢复、并发和锁等待证据是下一依赖。
- 独立记录：`labelu/docs/7gai_run_0243_release_gate_evidence_boundary.md`。
- 状态：未提交、未推送、未部署。

## RUN-0242 当前工作树全量本地回归基线（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R06 模型算法、R07 BM1688 边端、R08 前端/UI、R09 测试、R10 运维；工作包 WP1/WP2/WP3/WP4/WP5/OPS。
- 本轮交付：对当前工作树执行后端全集、前端类型检查和生产构建，验证前面多轮角色改动没有形成新的本地回归。
- 本地证据：后端 `2637 passed, 56 skipped, 410 warnings`；前端 `tsc --noEmit` 退出码 `0`；生产构建 `4094 modules transformed`、退出码 `0`、耗时 `32.21s`。
- 风险：警告未阻断本地通过，但生产仍未部署；真实 MySQL、GPU/TPU-MLIR、BM1688、S3、AICM、systemd 和浏览器证据缺失。
- 状态：未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0242_full_local_regression_baseline.md`。

## RUN-0241 生产只读路由与部署版本复核（2026-09-20，生产只读 L2）

- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；工作包 WP2/WP5/OPS。
- 本轮交付：匿名复核生产健康/就绪/版本探针、OpenAPI 和一期必需路由，确认线上仍是旧部署；不执行登录、写入、训练、编译、发布或下载。
- 生产证据：`/healthz`、`/readyz`、`/version` 均为 `200 text/html`；`/openapi.json` 为 JSON；`/api/v1/datasets`、`/api/v1/algorithms`、`/api/v1/model-versions`、`/api/v1/workspace/summary` 均 `404`；退休 `/api/v1/edge-device-jobs` 仍在 OpenAPI。
- 结论：当前工作树未部署到生产，生产整体不能放行；必须先经过提交授权、迁移/备份/并发闸门和 Nginx/Worker 部署，再重做两类账号与真实链路验收。
- 独立记录：`labelu/docs/7gai_run_0241_production_readonly_route_recheck.md`。

## RUN-0240 标注审核全局范围与居中弹窗审计（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R08 前端/UI、R09 测试、R10 运维；工作包 WP0/WP1/WP4/WP5/OPS。
- 本轮交付：确认标注审核和 AI 预测审核按当前账号全局范围查询，不要求项目选择、不传 `project_id`；审核处理继续使用居中弹窗；规则中心和旧设备发布页仅保留隐藏历史兼容代码，不扩大修改。
- 本地证据：`annotationReview.test.tsx` `3 passed`；覆盖预测审核进入、通过/驳回和投影到标注画布。
- 生产边界：未连接生产账号/API/浏览器，未提交、未推送、未部署；生产需用两类账号复验无项目入口和弹窗交互。
- 独立记录：`labelu/docs/7gai_run_0240_frontend_modal_chinese_no_project_audit.md`。

## RUN-0239 边端人工探测结果防旧后台覆盖（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试、R10 运维；工作包 WP2/WP3/WP5/OPS。
- 本轮交付：设备后台环境检查保存 `probe_*` 快照；若人工探测在后台 SSH 检查期间产生了更新，后台旧结果不得覆盖人工最新结果；环境 `environment_*` 状态继续独立落库。
- 本地证据：边端环境/路由专项 `4 passed`；BM1688 设备测试文件 `38 passed, 1 skipped`；后端全集 `2636 passed, 56 skipped, 410 warnings`；目标文件 `py_compile` 和 `git diff --check` 通过。
- 修复前证据：`test_edge_devices_probe_updates_device_status` 期望 `auth_failed`，实际被旧后台结果覆盖为 `connect_failed`。
- 生产边界：未连接生产 MySQL、真实 BM1688、Board Agent、SSH 或 systemd，未提交、未推送、未部署；真实并发探测、重启接管和两类账号回放待授权发布后执行。
- 独立记录：`labelu/docs/7gai_run_0239_edge_probe_manual_result_fencing.md`。

## RUN-0238 模型中心下载开关与 AICM 编号对齐验收（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R06 模型算法、R09 测试、R10 运维；工作包 WP0/WP1/WP2/WP3/WP5/OPS。
- 本轮交付：补齐模型中心下载关闭后的管理/公开双入口失败契约，并验证第三方回流别名仍返回同一 AICM `model_set_id`；不做外部模型回滚，不增加项目维度。
- 本地证据：模型目录服务与路由联合专项 `23 passed`（服务 `18`、路由 `5`）；新增测试使用本地 ZIP，仅验证平台逻辑。
- 生产边界：未连接生产 AICM、S3、TPU-MLIR、BM1688 或 MySQL，未提交、未推送、未部署；真实生产编号对账和浏览器下载回放待发布后执行。
- 独立记录：`labelu/docs/7gai_run_0238_published_model_download_gate.md`。

## RUN-0237 Provider 本地 HIL 失败状态失败关闭（2026-09-20，本地 L1）

- 责任角色：R01 产品/体验、R03 架构、R04 后端、R09 测试、R10 运维；工作包 WP0/WP1/WP2/WP3/WP4/WP5/OPS。
- 本轮交付：本地 HIL 失败时运行时证据写入 `UNHEALTHY`，只有成功才写 `READY`；生产可用性闸门保持独立。
- 本地证据：Provider 运行时/HIL/启动注册 `10 passed`；Provider 路由、作业和 Worker 联合 `35 passed`；前端 Provider 页面 `2 passed`。
- 生产边界：未连接生产 Provider/S3/AICM/TPU-MLIR/BM1688，未修改生产配置，未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0237_provider_hil_failure_closed_status.md`。

## RUN-0236 自动训练看门狗 Outbox 租约心跳（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；工作包 WP1/WP2/WP3/WP5/OPS。
- 本轮交付：自动训练看门狗为真实数据库会话注入独立 Outbox 心跳，初始 Outbox 租约至少 60 秒；不复用看门狗处理会话。
- 本地证据：看门狗专项 `7 passed, 113 warnings`；Outbox/派发/事件/看门狗联合 `51 passed, 116 warnings`。
- 生产边界：未连接生产 MySQL/S3/AICM/TPU-MLIR/BM1688，未启动自动训练，未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0236_watchdog_outbox_heartbeat.md`。

## RUN-0235 Outbox 常驻循环租约心跳透传（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；工作包 WP1/WP2/WP3/WP5/OPS。
- 本轮交付：`run_outbox_loop` 支持租约心跳回调和心跳间隔透传；非法心跳间隔启动前失败关闭；不改变事件状态机和数据库结构。
- 本地证据：Outbox、自动训练派发/事件/看门狗联合专项 `50 passed, 116 warnings`；单文件专项为 `12 passed`。
- 生产边界：未连接生产 MySQL/S3/AICM/TPU-MLIR/BM1688，未修改 systemd，未提交、未推送、未部署；真实并发、锁等待和 Worker 重启接管待生产前执行。
- 独立记录：`labelu/docs/7gai_run_0235_outbox_loop_heartbeat_forwarding.md`。

## RUN-0234 管理页面中文文案与测试契约同步（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R08 前端/UI、R09 测试、R10 运维；工作包 WP0/WP2/WP4/WP5/OPS。
- 本轮交付：管理页面分类弹窗继续使用中文优先文案；测试从旧“分类名称填写中文/分类标识（英文）”同步到当前“分类名称用于页面展示/分类系统标识”契约；不改变运行时逻辑、接口、权限或数据结构。
- 本地证据：管理页面与工作台联合 `2 test files, 7 tests passed`；前端 `type-check` 退出码 `0`；生产构建退出码 `0`，4094 个模块转换，28.58 秒；`git diff --check` 退出码 `0`。
- 测试边界：工作台测试实际路径为 `src/pages/platform/wallboard.test.ts`；Vitest 的 React/jsdom 弃用和环境提示为既有警告。
- 生产边界：未提交、未推送、未部署，未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688；生产页面须在授权发布后用两类账号复验。
- 独立记录：`labelu/docs/7gai_run_0234_admin_copy_test_contract.md`。

## RUN-0233 用户角色投影与管理页面一致性（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R08 前端、R09 测试、R10 运维；工作包 WP0/WP1/WP2/WP4/WP5/OPS。
- 本轮交付：用户列表与当前用户接口统一调用 `is_task_superuser(user)`，避免管理员在管理页面被显示为标注人员；保持只有管理人员、标注人员两类角色。
- 本地证据：用户路由专项 `12 passed, 116 warnings`；修复后仓库后端全集 `2631 passed, 56 skipped, 410 warnings`。
- 生产事实：只读核验已观察到旧部署中 `/users/me` 与 `/users` 的管理员字段不一致；需部署后用两类账号复验。
- 状态：未提交、未推送、未部署。独立记录 `labelu/docs/7gai_run_0233_user_role_projection_alignment.md`。

## RUN-0232 后端探针路由与部署闸门回归（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；工作包 WP2/WP5/OPS。
- 本轮交付：验证健康/就绪/版本/OpenAPI 与中文文档 exact route 在 SPA fallback 前代理到后端，并核对迁移发布和证据完整性失败关闭。
- 本地证据：`test_deploy_nginx_backend_routes.py`、`test_migration_release_deploy_gate.py`、`test_release_evidence_integrity.py` 合计 `15 passed in 0.18s`。
- 工具边界：Windows 无可用 WSL `/bin/bash`，对应 shell 脚本未执行；Python 源码契约通过不等于线上 Nginx 已加载。
- 生产边界：未提交、未推送、未部署；未修改生产 Nginx、systemd 或数据库。部署后仍需用 `curl` 验证四个精确路径返回 JSON。独立记录 `labelu/docs/7gai_run_0232_backend_probe_route_gate_regression.md`。

## RUN-0231 工作台全局文案与无项目入口回归（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R08 前端/UI、R09 测试、R10 运维；工作包 WP0/WP2/WP4/WP5/OPS。
- 本轮交付：工作台设备监控、运行墙和空状态从“当前项目/项目运行摘要”改为“平台”语义；保留历史 API `project_id` 兼容，不增加项目选择入口。
- 本地证据：`wallboard.test.ts` `3 passed`；前端类型检查通过；生产构建退出码 `0`，4094 个模块转换，28.03 秒；`git diff --check` 通过。
- 生产边界：未提交、未推送、未部署；未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688；生产两类账号工作台回放待授权发布后执行。独立记录 `labelu/docs/7gai_run_0231_workspace_global_copy_regression.md`。

## RUN-0230 生产只读路由与业务数据探针（2026-09-20，生产只读 L2）

- 责任角色：R01 产品、R03 架构、R04 后端、R05 外部平台、R06 模型算法、R07 BM1688、R09 测试、R10 运维；工作包 WP0/WP1/WP2/WP3/WP5/OPS。
- 本轮交付：管理员只读登录、OpenAPI 路由核验、任务/训练/BM1688/边端/发布模型/S3 统计核对；生产业务接口返回结构化 JSON。
- 生产证据：任务 `128`、训练任务 `63`、BM1688 编译 `66`、边端验证 `96`、发布模型 `17`、训练模型 `68`、导入模型 `3`；S3 配置可读。
- 只读脚本证据：`.tmp/production-readonly-route-probe-20260920.json`；未提供密码，不执行登录、创建、修改、发布或下载。
- 路由对照：本地 `app.openapi()` 347 条路径通过必需/退休契约；生产 OpenAPI 对账失败，确认线上部署版本落后于当前工作树。
- 生产阻断：`/healthz`、`/readyz`、`/version` 返回前端 HTML；OpenAPI 缺少 `/api/v1/datasets`、`/api/v1/algorithms`、`/api/v1/model-versions`、`/api/v1/workspace/summary` 且仍暴露退休 `/api/v1/edge-device-jobs`；自动训练和 BM1688/边端开关全部关闭；真实 MySQL、S3 回流、TPU-MLIR、BM1688 真机和 Worker 接管仍未验收。
- 状态：只读 L2；未提交、未推送、未部署、未修改生产数据。独立记录 `labelu/docs/7gai_run_0230_production_readonly_route_and_data_probe.md`。

## RUN-0229 迁移、发布闸门与部署契约回归（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；工作包 WP2/WP5/OPS。
- 本轮交付：固化正式 Alembic 唯一 head、去项目化 schema、执行租约字段/索引闭包、备份/并发/锁等待证据门禁、MySQL 沙箱地址校验、Nginx API/健康路由和 Worker 默认关闭契约。
- 本地证据：迁移/发布闸门专项 `78 passed, 144 warnings in 15.40s`。
- 失败关闭：缺少备份恢复、`2/10/20/50` 并发、锁等待或旧 token 拒绝证据时不连接生产；健康路径被 SPA 接管、迁移父链未知、Worker 未显式启用时均拒绝放行。
- 生产边界：未连接生产 MySQL，未执行正式 DDL、备份恢复、真实并发、Nginx/systemd 部署或 Worker 接管；未连接 S3/AICM/TPU-MLIR/BM1688；未提交、未推送、未部署。独立记录 `labelu/docs/7gai_run_0229_migration_release_gate_regression.md`。

## RUN-0228 接入页面去项目化前端对齐（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R08 前端、R09 测试、R10 运维；工作包 WP0/WP1/WP2/WP4/WP5/OPS。
- 本轮交付：接入作业、数据源、上传目标、媒体资产全部改为当前账号范围；前端不再查询项目、不再提交 `project_id`，页面保留居中弹窗和幂等上传流程。
- 本地证据：`src/pages/platform/pages/ingestion.test.tsx` `5 passed`；平台静态契约 `8 passed, 113 warnings`；前端类型检查退出码 `0`；生产构建退出码 `0`，4094 个模块、28.85 秒；仓库后端全集 `2630 passed, 56 skipped, 410 warnings`。
- 生产边界：未提交、未推送、未部署；未连接生产 MySQL、S3、AICM、TPU-MLIR、BM1688，待授权发布后做真实接口和浏览器验收；独立记录 `labelu/docs/7gai_run_0228_ingestion_frontend_no_project_alignment.md`。

## RUN-0227 BM1688 边端、Board Agent 与验证 Worker 回归（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R06 模型算法、R07 BM1688、R09 测试、R10 运维；WP2/WP3/WP5/OPS。
- 本轮交付：回归签名清单、三精度制品、设备注册、执行世代、Board Agent/SSH 验签、边端验证 Worker、短稳指标和旧入口失败关闭。
- 本地证据：边端联合套件 `197 passed, 1 skipped, 121 warnings in 11.52s`。
- 关键边界：BM1688 只加载/推理；清单、SHA256、执行世代或固定评估集不一致即拒绝；旧 `edge-device-jobs` 控制面不恢复。
- 生产边界：未连接真实 BM1688/TPU-MLIR/systemd/生产 MySQL/S3/AICM，未验证真机 P95、短稳、断网和重启接管；未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0227_bm1688_edge_agent_regression.md`。

## RUN-0226 训练 Worker、冻结清单与执行 fencing 回归（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R06 模型算法、R09 测试、R10 运维；WP1/WP2/WP3/WP5/OPS。
- 本轮交付：回归自动训练派发/生命周期、冻结数据清单、训练 Worker 持久化/运行时、GPU 资源档案、训练代次和旧 token 失败关闭。
- 本地证据：训练 Worker 与执行 fencing 联合套件 `118 passed, 116 warnings in 24.69s`。
- 并发规则：过期租约由新代次接管；旧 Worker 不能覆盖新状态、日志或后续副作用；重试生成新代次，不复用旧制品。
- 生产边界：未连接生产 MySQL/GPU/systemd，未执行 `2/10/20/50` 领取矩阵、锁等待或重启接管；未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0226_training_worker_fencing_regression.md`。

## RUN-0225 一期需求逐项验收矩阵（2026-09-20，本地 L1，生产未放行）

- 责任角色：R01 产品、R03 架构、R09 测试、R10 运维；WP0/WP1/WP2/WP3/WP4/WP5/OPS。
- 本轮交付：将不引入项目、两类角色、任务/预标注/训练、矩形框、三精度 BModel、BM1688、AICM/本平台模型目录、S3 自动回流、规则治理、并发安全、中文弹窗和台账要求逐项映射到证据。
- 结果：当前代码与本地测试多项达到 L1；真实 MySQL、S3/AICM、TPU-MLIR、BM1688、生产浏览器和部署后探针仍未完成，矩阵明确列出每项阻断。
- 独立记录：`labelu/docs/7gai_run_0225_phase_one_requirement_acceptance_matrix.md`。本轮未修改业务接口，未提交、未推送、未部署。

## RUN-0224 前端路由类型检查恢复（2026-09-20，本地 L1）

- 责任角色：R03/R08/R09/R10；工作包 WP2/WP4/WP5/OPS。
- 交付：隔离 React Router `useRouteLoaderData` 的递归泛型，恢复 `TaskTip` 类型检查；不改变运行时逻辑。
- 验证：前端类型检查退出码 `0`；生产构建退出码 `0`，4094 个模块转换，28.87 秒。
- 状态：本地 L1；未提交、未推送、未部署；未连接生产 MySQL、S3、AICM、TPU-MLIR、BM1688。
- 独立记录：`labelu/docs/7gai_run_0224_frontend_route_typecheck_recovery.md`。

## RUN-0223 任务服务项目操作面清理（2026-09-20，本地 L1）

- 责任角色：R01/R03/R04/R08/R09/R10；工作包 WP0/WP1/WP2/WP4/WP5/OPS。
- 交付：删除前端任务服务中无调用方的 `assignTasksToProject`；任务页和任务服务均增加无项目赋值静态门禁；后端历史字段和接口保持兼容；对 `TaskTip` 的路由数据/翻译钩子增加显式兼容类型，消除 TS2589。
- 验证：平台静态契约 `7 passed`；`pnpm --filter @labelu/frontend type-check` 通过；Vite 构建退出码 `0`，4094 个模块，`28.39s`；`git diff --check` 通过。类型检查阻断已在本地修复。
- 状态：本地 L1；未提交、未推送、未部署；未连接生产 MySQL、S3、AICM、TPU-MLIR、BM1688。
- 独立记录：`labelu/docs/7gai_run_0223_task_service_project_surface_cleanup.md`。

## RUN-0222 第三方 S3 回流、问题样本自动处理与并发安全回归（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R05 外部平台、R06 模型算法、R08 前端、R09 测试、R10 运维；WP0/WP1/WP2/WP3/WP4/WP5/OPS。
- 本轮交付：回归 S3 接收、校验、去重、模型 ID 归并、难例/主动学习、Provider/Outbox Worker、执行租约/fencing 和媒体安全。
- 本地证据：第三方回流与并发安全联合套件 `171 passed, 2 skipped, 129 warnings in 43.33s`。
- 自动化边界：正常包自动处理；模型 ID 冲突、契约异常、低置信度和解析失败才进入管理审核；失败阶段可重试但不伪造成功。
- 生产边界：未接收真实生产 S3 包，未连接生产 MySQL/AICM，未执行 `2/10/20/50` 并发、锁等待或 Worker 重启接管；未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0222_s3_feedback_concurrency_regression.md`。

## RUN-0221 模型中心、BM1688 三精度与发布目录回归（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R05 外部平台、R06 模型算法、R07 BM1688、R08 前端、R09 测试、R10 运维；WP2/WP3/WP4/WP5/OPS。
- 本轮交付：回归模型版本血缘、PT/ONNX/BModel 清单签名、FP32/FP16/INT8 编译契约、BM1688 边端证据、AICM `model_set_id` 发布、本平台目录和下载禁用边界。
- 本地证据：模型中心/BM1688/AICM 联合套件 `283 passed, 1 skipped, 128 warnings in 13.85s`。
- 关键规则：缺制品/清单/固定评估集/真机证据禁止发布；发布不回滚外部文件，只能禁止本平台后续下载并发布替代模型；BM1688 只加载推理、不编译。
- 生产边界：未连接生产 AICM、TPU-MLIR、BM1688、S3 或 MySQL，未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0221_model_center_bm1688_publish_regression.md`。

## RUN-0220 标签契约、自动预标注与按标签训练回归（2026-09-20，本地 L1）

- 责任角色：R01 产品、R02 数据契约、R04 后端、R06 模型算法、R08 前端、R09 测试；WP0/WP1/WP3/WP4/WP5。
- 本轮交付：回归标签契约来源、自动预标注兼容模型选择、预标注幂等/进度、人工确认后冻结数据版本，以及全量标签/单标签子集训练。
- 本地证据：相关路由与服务联合套件 `41 passed, 123 warnings in 5.41s`。
- 规则边界：一期仅普通矩形框；不使用多边形、四角点、Pose 或 OCR；历史复合数据只读兼容。
- 生产边界：未连接生产 S3/AICM/TPU-MLIR/GPU/BM1688，未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0220_label_contract_preannotation_regression.md`。

## RUN-0218 生产只读路由与探针复核（2026-09-20，生产 L2 只读证据，未放行）

- 责任角色：R01 产品、R03 架构、R04 后端、R05 外部平台、R07 边端、R09 测试、R10 运维；WP1/WP2/WP3/WP4/WP5。
- 本轮交付：使用管理人员账号只读核验生产登录、任务、训练、BM1688 编译、边端验证、模型目录、S3 配置、设备和自动训练设置；未执行任何业务写入。
- 实测通过：`/openapi.json` 200 JSON；任务 `total=128`；训练任务 `total=63`；BM1688 编译 `total=66`；边端验证 `total=96`；发布/公开模型 `total=17`；训练模型 `total=68`；导入模型 `total=3`。
- 生产阻断：`/healthz`、`/readyz`、`/version` 为 200 HTML；`/api/v1/datasets`、`/api/v1/model-versions`、`/api/v1/algorithms`、`/api/v1/workspace/summary` 为 404；S3 active connection 候选路径为 404；需要部署当前代码并重新探针。
- 旧入口：`/api/v1/edge-device-jobs` 实际 GET 404，符合退休路径预期；OpenAPI 仍列出该路径，需部署后继续做真实写入口失败关闭验证。
- 独立记录：`labelu/docs/7gai_run_0218_production_readonly_route_probe.md`。未提交、未推送、未部署；不能宣称生产整体完成。

## RUN-0217 角色团队本地交付与前端构建基线（2026-09-20，本地 L1）

- 责任角色：R01-R10；WP0/WP1/WP2/WP3/WP4/WP5。
- 本轮交付：复核两类角色、无项目、标签契约、普通矩形框、任务/训练/模型/回流主链和失败关闭边界；完成前端类型检查与生产构建。
- 本地证据：仓库级 `2627 passed, 56 skipped, 410 warnings`（退出码 `0`，耗时 `393.83s`）；内部后端 `2283 passed, 25 skipped, 0 failed`；前端类型检查通过；Vite 构建退出码 `0`，`4094` 个模块转换，耗时 `29.01s`。
- 本地 HTTP 证据：隔离服务 staging acceptance 全部必需探针通过，相关脚本测试 `11 passed`，工作流 Outbox 保留且普通工作流仍失败关闭；该证据只代表本地 SQLite。
- 构建警告：Ant Design `use client`、`eval` 和大包体警告均为现有依赖/构建提示，不计为本轮失败，真实浏览器性能仍待验收。
- 生产边界：未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688；未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0217_role_team_frontend_build_baseline.md`。

## RUN-0216 内部后端全量回归与新鲜测试库基线（2026-09-20，本地 L1）

- 责任角色：R09 测试；R03 架构、R04 后端、R01 产品、R06/R07 协作；WP0/WP1/WP2/WP3/WP4/WP5。
- 本轮交付：使用按进程/启动时间隔离的新鲜 SQLite 和进程级媒体目录执行内部全量回归，确认旧共享测试库结构失败不再影响一期主链。
- 本地证据：`python -m pytest labelu/tests/internal -q --disable-warnings --tb=short` 得到 `2283 passed, 25 skipped, 0 failed`。
- 生产边界：未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688；未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0216_internal_full_regression_fresh_isolation.md`。

## RUN-0214 并行测试媒体目录隔离（2026-09-20，本地 L1）

- 责任角色：R09 测试；R03 架构、R04 后端、R10 运维协作；WP5/OPS。
- 本轮交付：测试 `MEDIA_ROOT` 按进程隔离，与已有测试数据库隔离保持一致；生产存储逻辑未修改。
- 本地证据：附件路由套件 `20 passed`；两份并行附件套件均 `20 passed`。
- 生产边界：未连接生产、S3 或 MySQL；未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0214_parallel_test_media_isolation.md`。

## RUN-0213 一期退休规则中心二期隔离（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R09 测试；WP0/WP1/WP5。
- 本轮交付：将依赖项目绑定、设备发布和设备验签的历史规则中心行为套件标记为二期隔离；新增一期规则中心写入口 `403` 失败关闭契约，不恢复规则/设备发布业务。
- 本地证据：`test_phase_one_retired_surfaces.py` 与历史规则套件回归 `1 passed, 22 skipped`。
- 生产边界：未连接生产、未写生产数据、未提交、未推送、未部署；二期重新启用必须另建菜单、API 和设备证据。
- 独立记录：`labelu/docs/7gai_run_0213_phase_one_retired_rule_suite.md`。

## RUN-0211 自动训练旧测试契约迁移与二期隔离（2026-09-20，本地 L1）

- 责任角色：R09 测试；R04 后端、R03 架构、R06 模型算法协作；WP2/WP5。
- 本轮交付：本地评估器测试使用测试专用显式开关隔离；`reserve_active_round` 增加一期 `403/57122` 失败关闭断言，并保留二期 CAS 隔离测试；不恢复生产写入口。
- 本地证据：评估器、持久化目标套件 `11 passed`。
- 生产边界：未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688；未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0211_auto_training_legacy_test_contract.md`。

## RUN-0210 数据治理版本校验测试迁移到无项目夹具（2026-09-20，本地 L1）

- 责任角色：R09 测试；R04 后端、R03 架构、R01 产品协作；WP2/WP5。
- 本轮交付：数据治理版本校验测试不再创建项目或向数据集 URL 传 `project_id`；直接使用全局数据集，并断言响应不暴露项目字段。
- 本地证据：数据治理目标用例、数据集生命周期、标签契约回归 `13 passed`。
- 生产边界：未修改生产路由、未连接 MySQL/S3/AICM、未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0210_data_governance_no_project_fixture.md`。

## RUN-0209 迁移审计事实模型刷新（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试；WP2/WP5。
- 本轮交付：迁移计划不再把已去新 FK 的 `dataset/dataset_version` 计入活动项目外键队列；审计断言更新到正式 head `z9f3a4b5c6d7`、历史版本计数 `46`、实际项目外键表 `20`。
- 本地证据：迁移租约审计、项目外键计划、一期迁移预演 `9 passed`。
- 未处理项：退役项目/规则/旧边端写链、第三方回流旧 project fixture、自动训练旧写入测试需下一批按新契约迁移或二期隔离。
- 生产边界：未执行 DDL、未连接 MySQL、未提交、未推送、未部署；独立记录 `labelu/docs/7gai_run_0209_migration_audit_contract_refresh.md`。

## RUN-0208 内部全量回归与历史契约清单（2026-09-20，本地 L1）

- 责任角色：R09 测试；R03 架构、R04 后端、R01 产品协作；WP0/WP1/WP2/WP5。
- 本轮交付：执行 `pytest labelu/tests/internal`，将失败按退休入口、去项目化旧构造、自动训练计划人工写入口和项目前置夹具分类；确认不恢复已取消的项目、规则中心和旧设备发布写链。
- 本地证据：`2267 passed, 1 skipped, 38 failed`；前端 `type-check` 和 `build` 均通过。失败均已归入待迁移历史测试契约或已明确退休边界，未发现一期主链新增运行时失败。
- 后续：把旧测试改为退休/无项目/自动回流新契约后再重跑全量；在此之前以 RUN-0203/0204/0205/0207 主链专项作为 L1 放行依据。
- 生产边界：未连接生产、未写生产数据、未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0208_internal_full_regression_legacy_contract_inventory.md`。

## RUN-0207 边端验证父任务一致性先行门禁（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试；WP1/WP5。
- 本轮交付：边端验证创建入口先校验导出任务与当前任务的父任务一致性，再读取训练作业、一期算法类型和 BM1688 平台，避免跨任务请求因历史字段缺失走错错误分支。
- 本地证据：跨任务守卫与边端验证联合 `32 passed`。
- 生产边界：未连接生产、未执行 BM1688 真机、未写生产数据、未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0207_edge_validation_parent_gate_order.md`。

## RUN-0206 旧边端控制面测试契约同步（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试；WP1/WP5。
- 本轮交付：确认已退休的 `edge-device-jobs` 旧写入口必须在任何父任务查询和数据库写入前直接失败关闭；同步旧测试从父任务冲突 `409` 到控制面退休 `403`。
- 本地证据：算法、训练、项目兼容和模型无项目联合回归 `33 passed`；首次基线为 `32 passed, 1 failed`，失败仅为旧测试契约未同步；`git diff --check` 和目标文件 `py_compile` 通过。
- 生产边界：未连接生产、未写生产数据、未提交、未推送、未部署；真实 BM1688 主链仍以新边端验证入口验收。
- 独立记录：`labelu/docs/7gai_run_0206_legacy_edge_test_contract.md`。

## RUN-0203 一期算法与 BM1688 执行门禁（2026-09-20，本地 L1）

- 责任角色：R06 模型算法、R04 后端；R07 BM1688 边端、R09 测试协作；WP0/WP1/WP5。
- 本轮交付：训练任务、直接训练、人工/自动/后台 BM1688 编译和边端验证服务层统一只允许 YOLO 检测及既有车牌兼容链，编译平台只允许 BM1688；二期算法注册定义保留展示但不能进入一期写链。
- 本地证据：一期门禁 `8 passed`；训练任务 `16 passed`；BM1688 导出 `35 passed, 1 skipped`；边端验证 `31 passed`；BM1688 联合流程 `8 passed`；`py_compile` 和 `git diff --check` 通过。
- 生产边界：未连接生产、TPU-MLIR、BM1688、AICM、S3 或生产数据库；真实三精度、真机性能、并发和发布对账仍待 L2/L3。
- 独立记录：`labelu/docs/7gai_run_0203_phase_one_algorithm_chip_gate.md`。

## RUN-0204 数据集项目兼容字段与新写链门禁（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试；R01 协作；WP2/WP5。
- 本轮交付：`dataset`、`dataset_version` 保留历史可空 `project_id` 列但不再生成新项目外键；自动训练计划创建/预览命令拒绝非空项目参数；历史 task/计划查询保留只读兼容。
- 本地证据：一期 schema/FK 与迁移预演、训练路由 `14 passed`；自动训练阶段门禁与 schema `45 passed`；自动训练路由 `120 passed`。
- 生产边界：未删除生产列、未执行 DDL、未连接 MySQL、未提交、未推送、未部署；正式生产外键约束仍需备份后迁移预演确认。
- 独立记录：`labelu/docs/7gai_run_0204_project_compatibility_write_gate.md`。

## RUN-0205 生产前 MySQL、并发矩阵与备份恢复证据门禁（2026-09-20，本地 L1）

- 责任角色：R10 运维、R09 测试；R03 架构、R04 后端协作；WP0/WP5。
- 本轮交付：在现有只读迁移闸门上补充强制 `2/10/20/50` 并发矩阵证据；备份恢复证据要求备份来源、摘要、首次/重复升级、独立恢复目标和恢复结果；命令新增 `--concurrency-evidence`。
- 本地证据：迁移闸门专项 `10 passed`；目标模块 `py_compile` 和 `git diff --check` 通过。缺少任一并发档位、备份恢复字段或危险 URL 时失败关闭。
- 安全边界：门禁只读取本地源码和 JSON 证据，不连接数据库；本轮未执行 DDL、不连接生产/MySQL/S3/AICM/TPU-MLIR/BM1688，不修改业务 Worker 或正式迁移。
- 生产缺口：四张业务表的真实 MySQL 2/10/20/50、22 张项目外键表快照对账、在线 DDL/元数据锁、独立实例备份恢复、Worker 重启接管和设备资源释放仍未验收。
- 状态：本地 L1，未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0205_preproduction_mysql_evidence_gate.md`。

## RUN-0202 生产只读探针复核（2026-09-20，生产未通过）

- 责任角色：R03/R09/R10；R01 协作；WP5。
- 只读结果：`/openapi.json` HTTP 200 且为真实 JSON；`/healthz`、`/readyz`、`/version` 虽为 200，但均返回 `text/html` SPA 回退。
- 结论：线上仍是旧部署，健康/就绪/版本探针和目标一期路由不能据此放行；需用户授权提交后部署当前版本并重新验证。
- 生产边界：未执行写请求、迁移、S3、AICM、TPU-MLIR、BM1688 或账号业务操作。
- 独立记录：`labelu/docs/7gai_run_0202_production_readonly_probe_recheck.md`。

## RUN-0201 居中弹窗 Esc 关闭与真实浏览器回归（2026-09-20，本地 L1）

- 责任角色：R08/R09；R01 协作；WP0/WP5。
- 本轮交付：模型详情、数据回流审核、设备验签详情的居中弹窗支持 Esc 关闭；修正嵌套弹窗误选、发布按钮精确断言和回流标题断言。
- 本地证据：隔离 Vite 前端下桌面、1024 和 375 窄屏浏览器回归 `9 passed`；前端类型检查通过；Vite 服务已停止。
- 生产边界：未连接生产、MySQL、AICM、S3、TPU-MLIR 或 BM1688；未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0201_modal_escape_browser_acceptance.md`；当前为本地 L1。

## RUN-0197 自动训练派发失败关闭与边端任务原子门禁（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R07 BM1688/边端、R09 测试；R06/R10 协作；WP2/WP3/WP5。
- 本轮交付：训练、BM1688、边端派发和恢复入口统一拒绝“持有人存在但过期时间为空”的损坏租约；BM1688 执行 token 条件更新与边端验证任务插入放入同一事务，旧 Worker 失租不能创建新任务。
- 本地证据：自动派发专项 `26 passed`；Outbox 与边端打包专项 `23 passed`；相关 Python 编译和差异检查通过；独立记录 `labelu/docs/7gai_run_0197_auto_dispatch_fail_closed_and_edge_atomic_gate.md`。
- 已知测试库阻断：自动训练 reconciliation 既有测试库缺少 `task_annotation_contract.label_scope_mode`；该结构漂移已由 RUN-0198 的测试夹具兼容补丁处理，并由 `17 passed` reconciliation 回归确认。
- 生产边界：未连接 MySQL、S3、AICM、TPU-MLIR、BM1688 或 systemd；未提交、未推送、未部署。
- 状态：本地 L1；下一步为测试库结构对齐及同版本 MySQL 并发/重启接管验证。

## RUN-0194 设备验签详情弹窗验收契约对齐（2026-09-20，本地 L1）

- 责任角色：R08/R09；R01 协作；WP0/WP5。
- 本轮发现：组件已经是居中 `Modal`，平台壳 E2E 仍使用旧 Drawer 选择器；已将测试改为弹窗角色和弹窗内详情断言。
- 本地证据：源码未发现 `Drawer`/`drawer` 详情实现；类型检查/构建沿用 RUN-0192；浏览器真实回归因本机 9888/3004 未启动，未宣称通过。
- 生产边界：未连接生产、未改生产页面、未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0194_device_signature_modal_e2e_alignment.md`。

## RUN-0193 BM1688 发布门禁 Fail-Closed 收口（2026-09-20，本地 L1）

- 责任角色：R04/R06/R07/R09；WP3/WP5。
- 本轮交付：手工与自动 AICM 发布共用完整边端证据；无固定有标注评估集、Profile、规则、执行世代、三精度或真机证据直接阻断；`allow_quality_risk` 不再绕过门禁；通用模型要求 FP32/FP16/INT8，车牌专用按运行契约要求 FP16；无 GT 不再算通过。
- 本地证据：AICM/Runner/导出/模型发布前端及相关辅助门禁回归 `193 passed`；Python 编译和差异检查通过。
- 生产边界：未连接 TPU-MLIR、BM1688、MySQL、S3、AICM 或 systemd；未提交、未推送、未部署；真实三精度和固定集仍待 L2/L3。
- 独立记录：`labelu/docs/7gai_run_0193_bm1688_release_fail_closed.md`。

## RUN-0191 BM1688 编译完成后回调执行期 fencing（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试；R06 模型算法协作；WP3/WP5。
- 本轮交付：边端 SSH 验证、服务器 PT/ONNX 评估和质量报告统一接收 `execution_monitor`；失租时在任务创建、评估目录/脚本和报告条件写入前阻断，`ExecutionFencingLost` 不再被后处理回调吞掉；复合车牌和重新编译路径同步接入。
- 本地证据：BM1688 导出/复合车牌专项 `47 passed`；质量刷新、Worker fencing 和边端业务联合 `26 passed`；相关 Python `py_compile` 通过；独立记录 `labelu/docs/7gai_run_0191_bm1688_post_compile_fencing.md`。
- 生产边界：未连接 MySQL、TPU-MLIR、BM1688 真机、S3、AICM 或 systemd；未提交、未推送、未部署；真实双 Worker 失租接管与质量/边端对象对账仍待执行。
- 状态：本地 L1；下一步为同版本 MySQL/真实 BM1688 长任务和重启接管验证。

## RUN-0192 任务列表摘要并发加载与前端类型收口（2026-09-20，本地 L1）

- 责任角色：R03/R08/R09；WP0/WP5。
- 本轮交付：任务列表并发加载任务、标签摘要和数据集分类摘要；按筛选范围隔离缓存；完成页显式指定国际化命名空间。
- 本地证据：前端静态契约 `22 passed`；`pnpm --filter @labelu/frontend type-check` 和 `pnpm --filter @labelu/frontend build` 通过（保留既有构建警告）。
- 并发边界：只读请求并发，无新增写锁；摘要 60 秒、列表 30 秒缓存；未改变训练/Worker 租约。
- 生产边界：未连接生产、未提交、未推送、未部署；独立记录：`labelu/docs/7gai_run_0192_task_loader_summary_cache_and_typecheck.md`。

## RUN-0190 BM1688预检失败回写执行期 fencing（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试；WP3/WP5。
- 本轮修复：通用 BM1688 导出在“训练作业未完成/缺少权重”和“缺少 ultralytics”两个预检失败分支，存在执行期租约时改用 `execution_monitor.conditional_update`；迁移字段不可用时保留旧兼容事务路径，防止旧 token 接管后无条件写 `FAILED`。
- 本地证据：BM1688 自动恢复与执行 fencing 套件 `43 passed`；关联 BM1688 Worker、车牌检测/识别编译和边端业务回归 `19 passed`；`py_compile`、目标文件 `git diff --check` 通过；独立记录 `labelu/docs/7gai_run_0190_bm1688_stale_failure_fencing.md`。
- 范围边界：未处理编译完成后的边端验证/质量报告回调；未连接 MySQL、TPU-MLIR、BM1688 真机、生产数据库或 systemd；未提交、未推送、未部署。
- 状态：本地 L1；后续需继续补回调前执行租约当前性检查及真实双进程/长任务验收。

## RUN-0189 前端详情统一居中弹窗（2026-09-20，本地 L1）

- 责任角色：R01/R08/R09；WP0/WP5。
- 本轮交付：BM1688 编译移动端参考、历史兼容页注释和紧凑样式统一为中文居中弹窗语义；新增页面契约测试，长详情保留弹窗内部滚动。
- 本地证据：`node frontend/extensions/tests/yolo_bm1688_modal_contract.test.mjs` 通过；既有前端类型检查和构建通过。
- 生产边界：未连接生产、未改生产页面、未提交、未推送、未部署；部署后需用两类账号复核真实页面。
- 独立记录：`labelu/docs/7gai_run_0189_frontend_modal_interaction_contract.md`。

## RUN-0188 正式迁移覆盖候选租约草稿（2026-09-20，本地 L1）

- 责任角色：R03/R04/R09/R10；WP2/WP5。
- 本轮结论：正式 `z4a5b6c7d8e9` 已覆盖候选 `gai_p1_08` 的全部字段和索引；候选保留用于审计并声明 `SUPERSEDED_BY`，不再作为待执行迁移。
- 本地改动：迁移候选标记、发布闸门候选状态校验、未标记候选失败回归。
- 本地证据：迁移闸门、候选对账和候选结构专项 `16 passed`；`compileall` 通过。
- 生产边界：测试中的备份/锁等待为夹具，未连接 MySQL、未执行 DDL、未写生产；当前 L1。
- 独立记录：`labelu/docs/7gai_run_0188_formal_migration_candidate_supersession.md`；未提交、未推送、未部署。

## RUN-0187 实验数据集内部查询认证门禁（2026-09-20，本地 L1）

- 责任角色：R03/R04/R05/R09；R10 协作；WP1/WP4/WP5。
- 本轮修复：实验数据集列表、已创建任务、按包详情三个 GET 统一挂载内部服务令牌/HMAC 认证；不新增密钥，不改变 POST 回流受理协议。
- 本地证据：认证与回流路由 `28 passed`；中文 OpenAPI 与 S3 配置回归 `11 passed`；缺凭据返回 401，OpenAPI 声明 `APIKeyHeader`。
- 生产边界：未连接生产、未写任务/S3/AICM/数据库；线上仍需部署后用真实服务凭据复核。
- 独立记录：`labelu/docs/7gai_run_0187_experiment_dataset_internal_auth_gate.md`；状态本地 L1，未提交、未推送、未部署。

## RUN-0186 生产 OpenAPI、本地主链、角色与 Worker 只读审计（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R09 测试、R10 运维；R07 BM1688 协作；WP0/WP4/WP5/OPS。
- 本轮范围：只对照已保存的生产 OpenAPI 只读证据与当前本地 `app.openapi()`，未连接生产、未写数据。整理部署后必验路由、两类人工角色、旧回流入口、四个 systemd Worker 和探针/退休路径门禁。
- 关键事实：生产快照仍缺少 `/api/v1/datasets`、`/api/v1/algorithms`、`/api/v1/model-versions`、`/api/v1/workspace/summary`，仍暴露三条 `edge-device-jobs` 退休路径；本地 OpenAPI 已生成 347 条目标路径并隐藏这些退休路由。
- 本地缺口（已在 RUN-0187 修复）：审计时发现实验数据集内部三个 GET 路由缺少 `APIKeyHeader`；当前本地测试已为 `28 passed`，生产仍需部署后复核。
- Worker 核验：部署后必须核对 `labelu-training-worker.service`、`labelu-bm1688-worker.service`、`labelu-edge-validation-worker.service`、`labelu-experiment-dataset-worker.service` 的开关、systemd active、日志、租约/fencing 和重启接管；Web 不能回退同步执行。
- 独立记录：`labelu/docs/7gai_run_0186_production_openapi_role_worker_readonly_audit.md`。
- 状态：只读审计完成；未提交、未推送、未部署。

## RUN-0184 生产只读基线与部署差距复核（2026-09-20，生产只读不通过）

- 责任角色：R01/R03/R04/R08/R09/R10；WP0/WP1/WP5。
- 生产实测：`/openapi.json` 为 HTTP 200 JSON；`/healthz`、`/readyz`、`/version` 和 `/api-docs/openapi.json` 为 HTTP 200 SPA HTML；旧 `edge-device-jobs` 路径仍暴露。
- 差距：生产未出现目标数据集、算法、模型版本和工作台主链路由；不能标记为无项目、无设备发布目标版本。
- 生产边界：仅只读探针，无登录写入、任务、训练、设备、S3、AICM 或数据库操作。
- 状态：生产验收不通过；等待提交部署后按 RUN-0184 第4节复验。独立记录：`labelu/docs/7gai_run_0184_production_readonly_baseline.md`。

## RUN-0183 角色工作包联合回归与交付记录（2026-09-20，本地 L1）

- 责任角色：R03/R04/R06/R07/R08/R09/R10；WP0/WP2/WP3/WP4/WP5。
- 本轮交付：收口无项目默认菜单；完成迁移发布闸门、旧 S3 入口统一和 SSH 边端清单/签名/执行世代门禁的联合回归；每项结果均有独立 RUN 记录。
- 本地证据：菜单 `2 passed`；迁移闸门 `10 passed`；执行世代 `7 passed`；SSH/边端 `36 passed`、世代/运行器 `28 passed`；边端证据 `8 passed`；SSH 专项 `34 passed`；主动学习/健康 `16 passed`；训练 Worker `11 passed`；BM1688 Worker/fencing `51 passed`；实验数据集内部认证与回流 `28 passed`；中文 OpenAPI/S3 回归 `11 passed`；前端 `pnpm type-check`、`pnpm build`、Python 编译通过。
- 生产边界：未连接生产 MySQL、S3、AICM、TPU-MLIR、GPU、BM1688 或 systemd；未写生产数据；未提交、未推送、未部署。
- 状态：本地 L1；进入 L2/L3 前仍需同版本 MySQL、真实 S3/AICM、TPU-MLIR/BM1688 和 systemd 证据。
- 独立记录：`labelu/docs/7gai_run_0183_role_delivery_joint_regression.md`。

## RUN-0182 默认菜单与无项目主链契约（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R08 前端/UI、R09 测试；R10 运维协作；WP0/WP5。
- 本轮交付：固定默认六个中文入口（工作台、任务与标注、训练中心、模型中心、数据回流、系统管理）；历史 `deployments`、`workflows` 地址仅做兼容跳转；默认业务页面不加载项目列表、不发送 `project_id`、不使用项目缓存键；数据回流移除未使用的项目参数。
- 本地改动：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/evolution.tsx`；新增 `labelu/tests/e2e/test_7gai_visible_menu_no_project_contract.py`；独立记录 `labelu/docs/7gai_run_0182_visible_menu_no_project_contract.md`。
- 本地证据：菜单无项目契约 `2 passed`；前端 `pnpm type-check` 通过；进化页面/模块回归 `9 passed`。
- 生产边界：未连接生产、未写数据库、未提交、未推送、未部署。生产 OpenAPI、Nginx 和隐藏技术页面的历史兼容参数仍需部署后复核。
- 下一依赖：与对应后端退休路由、正式迁移和生产菜单验收一起复核；没有后端退休证据时不直接删除隐藏兼容代码。
- 状态：本地 L1；未提交、未推送、未部署。

## RUN-0181 第三方 S3 旧入口统一回流 Worker 门禁（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R06 模型算法、R09 测试；R10 运维协作；WP4/WP5。
- 本轮交付：审计 `/api/v1/integrations/strategy-tasks` 与 `/internal/v1/experiment-datasets` 两条入口；确认旧入口现为兼容适配器，只做幂等投影和字段转译，统一调用新 `accept()`，由独立 S3 Worker 执行下载、去重、AICM `model_set_id` 解析、聚合组冲突门禁、任务复用和预标注。删除可执行的旧同步副作用链，`_legacy_direct_create()` 改为 `410` 失败关闭。
- 本地改动：`strategy_task_integration.py`；旧入口服务测试和路由专项测试；新增专项记录 `labelu/docs/7gai_run_0181_legacy_s3_ingress_unification.md`。
- 本地证据：旧 S3 适配器与旧入口路由专项 `16 passed`；覆盖旧入口不读 S3、不建任务、状态地址统一、旧同步实现失败关闭、幂等和转译。
- 生产边界：未连接生产 MySQL、S3、AICM 或 systemd；未写生产任务、图片、模型或配置；未提交、未推送、未部署。生产仍需真实 `strategy-integration-s3-config` 对象包、AICM `model_set_id`、任务复用、预标注数量和 Worker 重启接管对账。
- 风险/遗留：部署前生产旧路由仍可能运行历史代码；Worker 开关、租约列、安全配置或模型目录未就绪时必须失败关闭，不能回退到同步 S3。
- 下一依赖：用户明确“提交代码”后再提交、推送、部署；部署后复验旧/新入口最终落同一导入表和 Worker 状态机。
- 状态：本地 L1；未提交、未推送、未部署。

## RUN-0175 ModelVersion 制品链清单校验收口（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R06 模型算法、R09 测试；R10 运维协作；WP3/WP5。
- 本轮交付：ModelVersion 创建时补齐模型版本绑定并由服务端签名；生产发布前重新验证签名、版本 ID、媒体根路径、文件大小和 SHA256；保持 BM1688 FP32/FP16/INT8 三精度门禁。
- 本地证据：ModelVersion 清单门禁 `2 passed`；模型版本路由 `6 passed`；既有模型目录/AICM 回归保持通过。
- 生产边界：未配置生产密钥，未连接生产 MySQL、AICM、BM1688 或 S3，未发布真实模型；只读 OpenAPI 仍缺少 `/model-versions`、`/models`、`/experiment-datasets`，同时保留旧导出/S3 配置和已退休设备任务路径；Ed25519 设备/外部平台验签和真实密钥轮换仍未完成。
- 状态：本地 L1；未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0175_model_version_manifest_unification.md`。

## RUN-0174 ModelManifest 接入导出与 AICM 发布门禁（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R06 模型算法、R09 测试；R07 BM1688、R10 运维协作；WP3/WP5。
- 本轮交付：导出清单支持服务端 HMAC 签名；AICM 发布列表和实际发布前复用同一校验，验证密钥版本、导出编号、执行摘要，并逐文件重算字节数/SHA256；生产无密钥失败关闭，历史无签名清单仅在非生产兼容模式读取。
- 安全边界：HMAC 不下发 BM1688；设备/外部平台仍需 Ed25519 公钥验签和防重放。`ModelVersion` 第二套制品接口尚未统一，不能绕过 YOLO 主链。
- 本地证据：ModelManifest/清单/AICM 门禁 `38 passed`；模型版本、AICM 发布辅助、模型目录 `125 passed`；BM1688 导出/业务回归 `73 passed, 1 skipped`；发布页制品清单阻断 UI `7 passed`；编译检查通过。
- 生产只读复核（2026-09-20）：`healthz/readyz/version` 和 `/api-docs/openapi.json` 仍为 200 非 JSON SPA 内容；`/openapi.json` 为 JSON；OpenAPI 仍暴露三条已退休 `edge-device-jobs` 路径。未配置生产密钥，未连接 AICM、TPU-MLIR、BM1688、S3、生产 MySQL 或 systemd；真实清单、真机验签、`model_set_id` 对账仍未验收。
- 状态：本地 L1；未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0174_model_manifest_publish_gate_integration.md`。

## RUN-0173 ModelManifest 签名与制品完整性基础契约（2026-09-20，本地 L1）

- 责任角色：R03 架构、R06 模型算法、R07 BM1688 边端、R09 测试；R04 后端协作；WP3/WP5。
- 本轮交付：新增可复用 ModelManifest 基础契约，支持规范化签名、key_id/导出编号/执行摘要校验，以及逐文件 SHA256/字节数重算和 FP32/FP16/INT8 三精度检查；导出阶段可按服务端密钥写入签名清单，AICM 发布列表和实际发布前接入校验门禁。
- 重要边界：HMAC 只用于平台服务内部验证，未下发到 BM1688；Board Agent/SSH 仍只消费边端摘要，ModelVersion 第二套接口和设备端正式验签尚未统一。设备/外部平台正式方案仍需 Ed25519 公钥验签、防重放和密钥轮换。
- 本地证据：ModelManifest 签名与既有制品清单专项 `11 passed`；既有摘要/Profile/AICM 基线 `36 passed`。
- 生产边界：未配置生产密钥，未生成真实签名清单，未连接 TPU-MLIR、BM1688、AICM、S3、MySQL 或 systemd；不能宣称发布门禁已完成。
- 状态：本地 L1；未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0173_model_manifest_signature_contract.md`。

## RUN-0172 S3 回流 Worker 程序化入口 readiness 门禁（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；R01 产品协作；WP4/WP5/OPS。
- 本轮交付：`run_import_worker_once()` 在 `claim_pending()` 前统一调用 `dedicated_worker_ready()`；schema、安全配置或 Worker 开关未通过时，返回 `ready=False`，不领取任务、不创建租约、不执行 S3 下载。命令行入口继续保留启动预检。
- 文档事实修正：训练 Worker 当前主路径是 `training.worker.command.v1` Outbox 消费和记录 fencing；BM1688、边端、S3 继续使用各自任务表租约，避免验收文档与代码事实不一致。
- 本地证据：S3 Worker 专项 `10 passed`；S3/训练/BM1688 Worker 联合 `29 passed`；健康/预发布和边端发布门禁联合回归保持通过。
- 生产边界：未连接生产 MySQL、S3、AICM、systemd，未领取生产任务；同版本 MySQL 20 并发、重启接管、真实 S3 对账仍未验收。
- 状态：本地 L1；未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0172_s3_worker_readiness_gate.md`。

## RUN-0171 第三方回流聚合组模型 ID 一致性门禁（2026-09-20，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R06 模型算法、R09 测试；R10 运维协作；WP4/WP5。
- 本轮交付：第三方 S3 回流按 `aggregation_key` 聚合前校验 `target.model_id`。同组多个非空模型 ID 不一致，或空 ID 与非空 ID 混合时，进入 `WAITING_FOR_MODEL`，保存 `aggregation_model_id_conflict`，不创建任务、不混合样本、不猜测模型；全部为空的历史类别型回流保持兼容。
- 血缘规则：回流模型 ID 必须是模型中心已发布目录中的 AICM `model_set_id`；本地 `export_id` 不能替代。通过后任务、数据集版本、难例批次和训练作业继续沿用同一模型 ID。
- 测试库修复：`labelu/tests/conftest.py` 按需补建旧 pytest 数据库缺少的 `task_annotation_contract.dataset_label_contract_id` 可空列；未修改正式迁移、生产数据库或回流状态机。
- 本地证据：回流路由 `22 passed`；预标注模型选择 `13 passed`；`git diff --check` 待本轮收尾复核。
- 生产边界：未连接真实 S3/AICM/生产 MySQL，未导入第三方图片，未执行生产写入；真实 metadata、model_set_id、任务和样本对账、MySQL 20 并发仍待验收。
- 状态：本地 L1；未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0171_s3_aggregation_model_gate.md`。

## RUN-0170 BM1688 Profile 摘要贯穿边端验证与 AICM 发布门禁（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R06 模型算法、R07 BM1688 边端、R09 测试；R10 运维协作；WP3/WP5。
- 本轮交付：Board Agent/SSH 验证包的 `run_config.json` 增加 `bm1688_profile`、`profile_digest`；板端回传相同摘要；平台证据门禁按导出清单重算并校验，缺失进入 `PARTIAL_COMPLETED/pending`，不一致进入 `fail`。
- AICM 交付门禁：模型中心列表显示 `profile_digest_missing/profile_digest_mismatch`；实际发布前再次比对，失败关闭，不调用 AICM 上传；历史没有 Profile 的制品保持读取兼容。
- 本地证据：边端证据、AICM 发布门禁、Board Agent/SSH 打包专项联合 `64 passed`；相关 Python `compileall` 通过；差异检查无错误。
- 生产边界：未执行真实 TPU-MLIR、BM1688、AICM、S3、MySQL 或生产部署；不能据此开启 Worker 或放量。
- 状态：本地 L1；未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0170_bm1688_profile_edge_publish_chain.md`。

## RUN-0168 BM1688 Profile 版本化契约与清单一致性（2026-09-20，本地 L1）

- 责任角色：R03 架构、R06 模型算法、R07 BM1688 边端、R09 测试；WP3。
- 本轮实现：新增版本化 `BM1688Profile` 快照和稳定摘要，固定模型族/stem、ONNX opset/静态 batch/输入尺寸、前后处理、输出布局、工具链/运行时摘要、校准集和 qtable 摘要；配置默认值不改变既有 yolov11n/Sophon/BM1688 口径。
- 清单接入：导出清单记录 `bm1688_profile` 和 `profile_digest`，Profile 摘要纳入 `execution_signature`；编译门禁在新链路提供 Profile 时阻断清单缺失或快照不一致，历史无 Profile 调用保留兼容。
- 修改文件：`labelu/internal/common/bm1688_compile_contract.py`、`labelu/internal/common/bm1688_compiler_config.py`、`labelu/internal/common/model_asset_manifest.py`、`labelu/internal/application/service/yolo_bm1688_export.py`，以及对应 BM1688/清单专项测试。
- 本地证据：Profile/清单专项 `12 passed`；编译流程和边端业务流 `8 passed`；`compileall` 通过。真实 TPU-MLIR、BM1688、systemd、生产 MySQL/S3/AICM 未验收。
- 风险/遗留：Profile 默认输出布局、工具链摘要和运行时 ABI 仍需真实代表模型与设备确认；Board Agent/SSH 证据链尚未消费 Profile 摘要；未修改正式迁移和生产配置。
- 状态：本地 L1；未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0168_bm1688_profile_contract.md`。

## RUN-0164 BM1688 配置档案与生产差距产品审计（2026-09-20，补记，本地 L1/生产只读）

- 责任角色：R01 产品、R03 架构、R06 模型算法、R07 BM1688 边端、R09 测试、R10 运维；WP0/WP3/WP5。
- 结论：PT→ONNX→服务器 TPU-MLIR→BM1688 真机验证→AICM 的主链路方向正确，但当前仍不能按生产闭环放行；方案蓝图约 8/10，当前生产可上线性约 4-5/10。
- 本地证据：RUN-0141/0148/0155/0161 已覆盖三精度制品检查、编译失败终态、Worker fencing/失败关闭和本机 MySQL 沙箱租约；这些均不是真实 BM1688 或生产验收。
- 生产只读事实（2026-09-20）：`/openapi.json` 为 JSON 200；`/healthz`、`/readyz`、`/version` 虽为 200 但返回 SPA HTML；OpenAPI 仍暴露 `/api/v1/edge-device-jobs*`，目标算法/模型版本/工作台聚合路由尚未形成可验收真源。未写生产任务、模型、S3 或数据库。
- P0 下一轮：统一 BM1688 Profile（模型族、opset、输入输出、前后处理、工具链/ABI、校准/qtable 摘要）；生成并签名 `ModelManifest`；三精度“编译/加载/精度烟测 + 发布精度完整 HIL”分层；修复探针与退休路由门禁；完成真实 S3/AICM/平台目录三方对账；补同版本 MySQL/systemd/真实 BM1688 重启接管证据。
- 多余或下沉：主方案顶部 RUN 历史日志、重复的 PT/ONNX/BModel 和 UI 线框、无真实采集的温度/功耗硬门槛、Provider 作为模型类型，以及项目/设备发布/OTA/灰度/回滚写链。过程证据继续放独立 RUN 文档，历史对象只读隔离。
- 状态：只新增审计文档，未修改业务代码、未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0164_bm1688_profile_and_production_gap_review.md`。

## RUN-0165 四类 Worker 领取并发矩阵与 S3 回流 2/10/20 本地演练（2026-09-20，本地 L1）

- 责任角色：R03 架构、R09 测试、R10 运维；R04 后端、R07 BM1688/边端协作；WP2/WP3/WP5。
- 路径核对：训练已有 20 独立进程领取和旧 token 保护；BM1688 已有 2 进程竞争/过期接管；边端已有 execution fencing 结构和失租保护但没有多进程副作用演练；第三方 S3 回流原有单进程接管测试。
- 本轮增量：新增 `test_s3_import_claim_has_one_winner_at_local_concurrency_levels`，使用真实 `crud_experiment_dataset_import.claim_pending` 和独立 SQLite 进程，参数化验证 2、10、20 并发；每档仅一个领取者，`worker_fencing_token=1`、`attempt_count=1`。
- 本地证据：S3 回流专项 `9 passed`；训练 Worker `11 passed`；边端 Worker `4 passed`；BM1688 Worker `9 passed`。BM1688/训练并行运行时 Windows `spawn` 启动明显变慢，但业务领取和 fencing 断言通过。
- 真实边界：通用 MySQL 演练入口支持 2-64 竞争者，生产标准值为 20；本轮未按四张业务表执行 MySQL 2/10/20/50，未执行边端 SSH、真实 S3、TPU-MLIR、BM1688 或生产部署。SQLite 结果不得替代 MySQL/生产证据。
- 状态：仅测试和文档变更；未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0165_worker_concurrency_matrix.md`。

## RUN-0167 训练持久化专项测试库结构对齐（2026-09-20，本地 L1）

- 责任角色：R03 架构、R04 后端、R06 模型算法、R09 测试；WP2/WP5。
- 发现：持久化 pytest 数据库被项目去化候选演练重建后缺少 `dataset.project_id`，而 ORM 仍保留该可空历史兼容列，导致训练 Worker 持久化专项 2 项夹具失败。
- 处理：测试启动兼容层按需补建 `dataset.project_id INTEGER`；不修改生产 ORM、正式迁移或一期新写入链。
- 证据：修复后 `test_training_worker_persistence.py` 为 `12 passed`；训练、边端、S3、Provider 混合 Worker 套件为 `68 passed`（252.18 秒，保留 112 项依赖弃用警告）。修复前的 2 项失败均为上述测试库列缺失。
- 边界：未连接生产、未执行正式 DDL、未提交、未推送、未部署；生产迁移仍需脱敏快照、锁等待、结构/行数对账和备份恢复。
- 独立记录：`labelu/docs/7gai_run_0167_training_persistence_test_schema_alignment.md`。

## RUN-0166 MySQL 沙箱证据收口与正式迁移链放行阻断（2026-09-20，本地 L2-沙箱）

- 责任角色：R03 架构、R09 测试、R10 运维；WP0/U0B。
- 对账结论：RUN-0163 的本机 MySQL 9.0.1、REPEATABLE-READ、20 并发执行租约和第08段候选重复升级/降级结果已写入台账；第01段历史表脚本契约专项当前为 `7 passed`，不再是“待复跑”。
- 第01段证据：SQLite 非空历史行演练保留两张表的非 `project_id` 列和值、行数、索引和业务外键；MySQL 沙箱脚本额外验证 MySQL 外键命名冲突修复、`dataset_version.dataset_id` 外键保留和重复执行幂等。
- 正式链阻断：候选脚本仍位于 `.tmp/phase-one-rebuild/supplemental`，正式 `labelu/alembic_labelu/versions` 未替换；当前正式 head 仍为 `z9f3a4b5c6d7`，生产基线之后旧后缀不能直接执行。未完成脱敏生产快照、生产规模 `EXPLAIN`、元数据锁/在线 DDL、备份恢复、全 22 张项目外键表逐段对账。
- 下一步放行条件：同版本 MySQL 脱敏生产快照升级成功；逐表列/索引/外键/字符集/行数摘要一致；短锁等待和在线 DDL 证据通过；升级前备份可恢复；唯一 head 和空 SQLite/MySQL 白名单通过。上述条件全部满足前，U0B 不得超过本地 L2，禁止正式迁移、提交、推送、部署。
- 本地证据：`python -m pytest labelu/tests/scripts/test_rehearse_dataset_project_fk_rebuild.py labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py -q --disable-warnings --tb=short` = `7 passed`；迁移审计/候选链专项保持通过。
- 状态：未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0166_mysql_sandbox_release_gate_reconciliation.md`。

## RUN-0160 正式迁移 head 与项目外键归属审计契约刷新（2026-09-19，本地 L1）

- 责任角色：R03 架构、R09 测试；R10 运维协作；WP0/U0B。
- 交付：迁移审计唯一 head 更新为 `z9f3a4b5c6d7`；补齐 `z9c0d1e2f3a4`、
  `z9d1e2f3a4b5`、`z9e2f3a4b5c6`、`z9f3a4b5c6d7` 的对象级替代映射；当前 22 张
  项目外键表明确归属八段，其中 `dataset`、`dataset_version` 归第 01 段。
- 本地证据：迁移 head/映射/归属专项 `7 passed`；隔离候选骨架、候选链和调度租约
  演练 `12 passed`；`alembic heads` 输出单一 `z9f3a4b5c6d7`。
- 真实性边界：仅修正本地审计契约，没有修改正式迁移 DDL；未执行 SQLite/MySQL
  正式升级、生产快照、锁等待、备份恢复或生产连接。U0B 仍为本地 L1，不能宣称生产
  迁移完成。
- 状态：未提交、未推送、未部署。独立记录：
  `labelu/docs/7gai_run_0160_migration_audit_contract_refresh.md`。

## RUN-0161 BM1688 Worker 并行测试启动窗口审计（2026-09-19，本地 L1）

- 责任角色：R09 测试主责；R03 架构、R04 后端、R07 BM1688 边端协作；WP3/WP5。
- 交付：测试夹具统一使用 90 秒 Windows `spawn` 等待窗口，覆盖 ready、启动事件、候选屏障和结果队列；启动或结果超时输出子进程 pid、存活状态和退出码；异常路径清理子进程。
- 本地证据：BM1688 Worker 专项串行 `9 passed`；两份独立并行专项均 `9 passed`。此前 20 秒窗口的 4 个失败确认发生在业务函数调用前，不计为事务缺陷。
- 生产边界：未修改 BM1688 生产 Worker、未连接 MySQL/BM1688、未执行 systemd；真实 MySQL 2/10/20/50 并发仍未验收。
- 状态：未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0161_bm1688_worker_spawn_timeout_audit.md`。

## RUN-0162 dataset/dataset_version 非空历史行外键移除隔离演练（2026-09-19，本地 L1）

- 责任角色：R03 架构、R09 测试；R10 运维协作；WP0/U0B 第01段。
- 交付：新增 `.tmp/phase-one-rebuild/supplemental` 隔离补充候选，使用 SQLite batch 重建验证非空历史行、非 `project_id` 列、索引和外键结果；重复执行安全跳过，降级失败关闭；专项 `2 passed`，八段候选/调度租约复跑 `12 passed`，离线审计通过。
- 生产边界：不属于正式八段候选链，不修改正式 Alembic DDL、ORM 或生产数据库；同版本 MySQL、脱敏生产快照和备份恢复仍未验收。
- 独立记录：`labelu/docs/7gai_run_0162_dataset_project_fk_rebuild_rehearsal.md`。
- 状态：本地 L1，未提交、未推送、未部署。

## RUN-0163 本机 MySQL 沙箱执行租约与第01段迁移演练（2026-09-20，本地 L2-沙箱）

- 责任角色：R03/R04/R07/R09/R10；WP0/WP2/WP3/WP5。
- 交付：建立本机 `labelu_migration_sandbox_20260919`，完成 MySQL 9.0.1/REPEATABLE-READ 探针；20 并发执行租约演练唯一领取通过、旧 token 拒绝、新 token 完成写入；第08段候选结构在 MySQL 沙箱重复升级/降级通过。
- 第01段脚本：`labelu/scripts/rehearse_dataset_project_fk_rebuild.py` 已在本机 MySQL 沙箱完成非空历史数据演练；脚本契约专项现为 `7 passed`，不再是待执行项。
- 真实性边界：只证明本机 MySQL 沙箱，不等于生产；未执行脱敏生产快照、生产 DDL、GPU/BM1688、S3/AICM 或部署。
- 独立记录：`labelu/docs/7gai_run_0163_mysql_sandbox_fencing_and_migration.md`。
- 状态：未提交、未推送、未部署。

## RUN-0158 主动学习旧项目测试迁移与一期自动训练门禁（2026-09-19，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R09 测试、R10 运维；WP4/WP5。
- 交付：主动学习旧专项从项目创建和 `project_id` 请求迁移为全局 `scope_key=global`；全局编排使用管理人员权限；冻结阶段验证自动训练计划写入关闭时返回 `403 / 57122` 且无审计副作用。
- 本地证据：`labelu/tests/internal/adapter/routers/test_active_learning.py` = `9 passed`。
- 生产边界：只改测试契约，未写生产；真实 S3/AICM、MySQL 并发、生产自动训练观察仍未验收，自动训练开关保持关闭。
- 状态：未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0158_active_learning_legacy_test_migration.md`。

## RUN-0157 服务探针与 SPA 回退边界（2026-09-19，本地 L1）

- 责任角色：R03 架构、R04 后端、R08 前端、R09 测试、R10 运维；WP5/OPS。
- 交付：新增健康探针和规范 OpenAPI JSON 防回归测试；确认根级探针在 SPA 静态挂载前注册，页面历史路由仍可回退到 `index.html`。
- 本地证据：探针专项 `2 passed`；中文 OpenAPI 专项 `8 passed`。
- 生产边界：线上曾返回 HTML，尚未提交、部署或重新验证 Nginx/应用版本；部署后必须记录状态码、Content-Type、首字符、版本号和时间戳。
- 状态：未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0157_service_probe_spa_boundary.md`。

## RUN-0155 BM1688 重编译异常终态与可重试说明（2026-09-19，本地 L1）

- 责任角色：R04 后端、R07 BM1688 边端、R09 测试；R03 架构、R10 运维协作。
- 交付目标：解决手工 `recompile` 在工具链预检或编译执行异常时长期停留 `PENDING`、异常收尾误写 `ONNX_READY` 的问题。
- 后端：复用现有 `FAILED` 状态和 `error_message`；预检、缺 ONNX、编译异常统一写失败原因并说明可重试；独立 Worker 仍在租约字段缺失时失败关闭。
- fencing：独立 Worker 使用 execution monitor 条件写入；`ExecutionFencingLost` 不写失败终态，避免旧 Worker 覆盖新执行者；无 monitor 直写仅保留迁移期间历史兼容路径。
- 本地证据：重编译/自动恢复专项 `36 passed`；BM1688 Worker、质量规则和编译契约联合 `52 passed`；目标模块 `py_compile`、`git diff --check` 通过。
- 生产边界：真实 TPU-MLIR、BM1688、MySQL 锁等待、systemd 重启、三精度制品和固定评估集未验收；独立 BM1688 Worker 生产开关继续关闭。
- 状态：未提交、未推送、未部署，未写生产数据库。独立记录：`labelu/docs/7gai_run_0155_bm1688_recompile_terminal_state.md`。

## RUN-0154 全局难例审核权限与旧工作流测试迁移（2026-09-19，本地 L1）

- 责任角色：R01 产品、R03 架构、R04 后端、R08 前端、R09 测试；R10 运维协作。
- 交付目标：按生产两类登录角色收口全局难例审核权限；标注人员只读，管理人员审核；旧项目工作流测试迁移到当前无项目回调幂等契约。
- 前端：复用 `useTaskSuperuser`，标注人员弹窗只显示“关闭”并提示由管理人员审核；管理人员保留“提交审核”；继续使用居中弹窗。
- 测试：旧 `project_id`/项目工作流测试改为验证 `StrategyTaskIntegration` 回调回执幂等和不创建 `WorkflowInstance`。
- 本地证据：后端回调/去项目专项 `7 passed`；主动学习与迁移专项联跑 `12 passed`；进化中心页面专项 `3 passed`；`pnpm run type-check` 通过。
- 已知旧测试：`test_active_learning.py` 仍有 9 个项目创建夹具因一期项目写入口退役而返回 403，列为待迁移历史测试，不恢复项目菜单或默认项目。
- 生产只读探针：`/healthz`、`/readyz`、`/version` 和 `/api-docs/openapi.json` 仍返回前端 HTML；`/openapi.json` 返回 JSON；未登录模型目录返回 401。未执行线上写操作。
- 并发/生产边界：前端权限不替代后端鉴权；同版本 MySQL、20 并发、真实 S3/AICM/BM1688、生产迁移和部署仍未验收；未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0154_global_hard_example_review_permission.md`。

## RUN-0153 全局回流工作流与进化中心页面去项目化（2026-09-19，本地 L1）

- 责任角色：R03 架构、R04 后端、R08 前端、R09 测试；R01 产品、R10 运维协作。
- 交付目标：第三方 S3/边端难例不选项目即可完成批次接受、数据集版本和专家回流工作流；历史项目记录继续只读兼容。
- 后端：工作流新增 `scope_key`，`project_id` 可空；通用工作流公共写入口继续退役，仅主动学习服务可通过受控内部入口创建/推进 `expert_feedback`。
- 数据库：新增 `z9e2f3a4b5c6`、`z9f3a4b5c6d7`，当前唯一 head 为 `z9f3a4b5c6d7`。
- 前端：进化中心回流编排删除项目请求和项目门禁，聚类、批次、数据集版本、工作流统一使用全局接口。
- 本地证据：全局专项 `2 passed`；进化页面 `1 passed`；前端类型检查、目标模块编译和迁移 head 检查通过。
- 未验收：同版本 MySQL、20 并发、生产迁移、真实 S3/AICM/BM1688、部署和线上验证；未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0153_global_workflow_and_evolution_ui.md`。

## RUN-0152 主动学习聚类与批次去项目化（2026-09-19，本地 L1）

- 责任角色：R03 架构、R04 后端、R09 测试；R01 产品、R08 前端协作；R06 模型算法复核模型/来源血缘。
- 交付目标：第三方 S3/边端问题样本进入主动学习后，不再强制绑定项目；历史 `project_id` 只读兼容，新写入和幂等统一使用 `scope_key=global`。
- 后端：聚类按难例类型、设备、模型版本、来源包建立作用域键；批次按作用域、模型、来源包和难例集合生成可重试幂等键；重建、查询、批次生成接口支持省略 `project_id`。
- 权限：管理人员可重建和生成全局批次；标注人员查询全局聚类时沿用创建人、历史任务/项目权限和模型创建人可见性；不通过默认项目 ID 放大权限。
- 数据库：新增正式后继迁移 `z9d1e2f3a4b5_add_active_learning_global_scope.py`，为 `feedback_cluster`、`active_learning_batch` 增加作用域、模型、来源字段，替换项目唯一约束并保留历史项目列。
- 本地证据：项目无关的聚类、查询、来源过滤、批次生成和重复请求专项 `1 passed`；目标模块 `compileall` 通过。
- 未验收：全局批次接受后的数据集/工作流去项目化、同版本 MySQL 迁移、2/10/20/50 并发锁等待、真实 S3/AICM、生产部署和前端主动学习页面去项目化。
- 独立记录：`labelu/docs/7gai_run_0152_active_learning_global_scope.md`。

## RUN-0151 边端难例回流去项目化切片（2026-09-19）

- 责任角色：R03 架构、R04 后端、R08 前端、R09 测试；R01 产品复核。
- 交付目标：第三方 S3/边端问题图片进入进化中心时不再强制填写项目；历史 `project_id` 只读保留，不作为新写入、权限或幂等主键。
- 后端：`POST /api/v1/edge-feedback/events` 支持省略 `project_id`，写入 `scope_key=global` 和 `created_by`；事件标识、图片指纹按作用域唯一；`GET /api/v1/edge-feedback/hard-examples` 不带项目时按两类人员权限查询。
- 权限：管理人员可查看和复核全局难例；标注人员可查看自己创建或自己有历史任务权限的数据，无项目难例不能复核；历史项目查询继续兼容。
- 前端：进化中心难例审核不再请求项目列表或默认第一个项目；请求不带 `project_id`。主动学习编排仍保持历史项目门禁，列入下一工作包，页面不使用默认项目绕过。
- 数据库：新增正式迁移 `z9c0d1e2f3a4_add_global_evolution_scope.py`，增加 `scope_key/created_by`、可空历史项目字段、作用域唯一索引；SQLite 测试夹具可重建旧非空表。
- 本地证据：无项目写入/重复幂等/全局列表/模型版本兼容专项 `7 passed`；前端进化页面 `1 passed`；前端类型检查、目标模块编译和 `git diff --check` 通过；空库 Alembic 升级通过。
- 未验收：同版本 MySQL 在线迁移、历史重复指纹清理、真实第三方 S3/AICM 回流、主动学习和工作流的无项目新写链、生产部署。未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0151_edge_feedback_no_project.md`。

## RUN-0150 角色化开发落地启动（2026-09-19）

- 责任角色：R01-R10 按工作包协作；系统登录角色仍只有标注人员、管理人员。
- 本轮目标：把角色方案转为可执行开发顺序，每个产出同时记录代码、测试、证据和未完成边界。
- P0-1：R03/R04/R09 实现 `yolo_bm1688_export` 的 AICM 发布领取租约和 fencing，解决同一导出记录跨进程重复发布。
- P0-2：R07/R09 修复 `board_agent.py:634` 语法错误，恢复发布、模型目录和 S3 回流专项回归。
- P0-3：R05/R10 准备真实/脱敏 AICM `model_set_id`、S3 `metadata.json`、图片和 LabelU 任务样本对账证据。
- P1：R01/R08 收口中文菜单、按钮前置条件和居中弹窗，保持页面不出现英文业务文案、不恢复抽屉。
- 当前等级：本地 L1；未提交、未推送、未部署、未写生产。
- 独立记录：`labelu/docs/7gai_run_0150_role_based_delivery_kickoff.md`。

### RUN-0150 P0-1 AICM 发布领取租约本地实现

- 责任角色/工作包：R03 架构、R04 后端、R09 测试；WP3。
- 修改范围：`yolo_bm1688_export` 增加发布状态、发布任务号、租约 owner/过期时间和 fencing token；AICM 发布入口领取租约后才创建后台文件任务；后台完成时按 job/owner/token 条件写回。
- 迁移：新增 `z9b0c1d2e3f4_add_aicm_publish_claim_lease.py`，降级只允许备份恢复，不自动删列。
- 本地证据：目标模块 `compileall`/`py_compile` 通过；发布租约专项 `4 passed`，与发布辅助回归联合 `105 passed`；并发真实 MySQL、AICM 外部副作用、生产迁移仍未验收。
- 阻断：R07/R09 尚在修复 `board_agent.py:634` 语法错误；当前未能启动完整 AICM/S3 联合回归。
- 状态：本地 L1，未提交、未推送、未部署；生产发布和回流 Worker 保持关闭。
- 独立记录：`labelu/docs/7gai_run_0150_aicm_publish_lease.md`。
- 联合回归补充：第三方调用专项仍有 3 项旧项目夹具失败，原因是 `StrategyTaskIntegration` 去掉 `project_id` 后旧测试仍传入该字段；列入下一轮去项目化迁移，不恢复项目维度。

### RUN-0150 数据回流页面项目维度只读审计（本地 L1）

- 责任角色/工作包：R08 前端、R01 产品；R03 架构、R04 后端、R09 测试协作；WP4/WP5。
- 审计结论：`evolution.tsx` 通过 `usePlatformProject` 请求 `/v1/projects`，无 URL 项目参数时默认第一个项目；难例、聚类、批次、数据集和工作流请求继续携带 `project_id`。前端不能用默认项目或简单删除参数解决。
- 后端证据：`edge_feedback` 列表和 `active_learning` 命令将项目作为必填参数、权限边界、外键和唯一索引；`workflow` 列表仍要求项目且只允许系统管理员。现有无项目可复用链是数据集按任务/创建者权限查询、`ExperimentDatasetImport` 按来源包/AICM模型ID/任务血缘处理第三方回流、策略演化新写链不接收项目字段。
- 最小修改清单：只迁移进化中心，移除 `usePlatformProject` 和项目缓存键；后端按创建者/任务/来源集成/模型 ID建立权限与血缘；项目列保留为历史可空只读；工作流增加按数据集版本/来源集成的受保护查询。其他旧页面暂不全局删除 `projectQueryKey`。
- 测试建议：前端确认不请求 `/v1/projects`且请求不含项目字段；OpenAPI 拒绝显式项目参数；标注人员按任务权限、管理人员按全局异常权限；事件/指纹/批次并发幂等、迁移快照和索引执行计划必须在同版本 MySQL 验收。
- 本轮仅本地只读审计，未改业务代码、未连接生产/S3/AICM/MySQL/TPU-MLIR/BM1688，未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0150_project_scope_frontend_audit.md`。

> 该历史段落当时的最新轮次：`RUN-0149`。本轮完成 AICM 发布、本平台模型目录下载控制、第三方 S3 回流和 AICM 模型 ID 对齐的本地/生产只读证据审计；未修改业务代码，明确同一导出记录跨进程重复发布为 P0 下一工作包。目标模块 `py_compile` 和 `git diff --check` 通过；目标 pytest 因工作区另一轮 `board_agent.py:634` 语法错误未能启动。生产路由只读证据显示 `/published-models`、S3 配置和 `/openapi.json` 可访问，但缺少数据集/算法目录/模型版本/工作台路由且仍暴露退休边端设备作业路由；真实 AICM、S3 metadata、Worker、MySQL 并发和模型 ID 对账仍未验收。独立记录见 `labelu/docs/7gai_run_0149_aicm_s3_evidence_audit.md`。

### RUN-0149 板端主动拉取统一任务主路径收口（本地 L1）

- 责任角色/工作包：R03 架构、R04 后端、R07 BM1688 边端、R09 测试；R10 运维协作；WP3。
- 代码路径：新增 `BOARD_AGENT_PRIMARY_ENABLED`（默认关闭）；开关开启后，仅 `validation_channel=board_agent` 的设备使用统一 `yolo_edge_validation_job` 主路径，SSH 设备继续兼容链，旧 `yolo_board_validation` 不作为新业务真源。
- 安全与并发：统一任务领取复用 execution lease/fencing；设备 ID必须匹配；下载、进度、回报必须携带 HMAC 短期任务令牌；进度续租；租约过期后旧令牌拒绝，允许新执行器重领；同令牌同报告重复回报幂等，避免双消费者和重复终态。
- 设备配置：设备注册表新增 `validation_channel`，默认 `ssh`，不新增项目/业务空间；调度器只抑制主动拉取设备的 SSH 线程，不影响 SSH 设备。
- 修改文件：`board_agent.py`、`board_agent` 路由/响应、统一设备注册表命令/响应/服务、边端调度器、配置，以及新增 `test_board_agent_unified.py`。
- 本地证据：板端统一路径及旧路由 `6 passed`；设备注册表/边端 Worker/环境回归合计 `17 passed`；目标模块 `compileall`、`git diff --check` 通过。
- 生产边界：未执行生产迁移、MySQL 双 Worker/20 并发、真实 BM1688/板端客户端、systemd、AICM 或 S3；生产开关保持关闭，未提交、未推送、未部署。独立记录见 `labelu/docs/7gai_run_0149_board_agent_unification.md`。

### RUN-0149 AICM 发布、模型目录下载与第三方 S3 回流证据审计（本地 L1）

- 责任角色/工作包：R05 外部平台、R03 架构、R04 后端、R09 测试、R10 运维；WP3/WP4。
- 代码路径结论：发布成功后用 AICM `model_set_id` 作为平台 `public_model_id`，保留本地 ZIP 和 SHA256/MD5；下载开关使用带当前值的原子更新，关闭后返回 409；回流优先通过发布目录按 AICM ID 找到源导出和训练作业，未登记模型进入等待状态，不按 export_id 猜测。
- S3 自动链结论：统一 `/internal/v1/experiment-datasets` 负责受理，旧策略接口只做兼容转译；独立 Worker 负责 metadata/图片校验、聚合任务、预标注和 owner/token 租约。当前“自动”是第三方推送后自动入队，不是主动扫描桶。
- P0 缺口：`start_publish_job` 没有跨进程发布领取租约，`list_publishable` 仍展示已成功发布的 export；同一 export 并发提交可能上传多个 AICM 模型集。下一包按复用 `yolo_bm1688_export` 增加 publish 状态、job、租约 owner/过期和 fencing 字段（方案 A），并在正式 MySQL 迁移/快照/锁等待验证后落地；独立发布表（方案 B）保留为二期审计量增大时的选择。
- P1 遗留：本地模型包首次补缓存可能重复访问 AICM；真实第三方 `metadata.json`、图片清单、AICM ID、LabelU 导入/任务/样本 ID 对账尚缺；一期没有 S3 主动扫描器。
- 生产证据：`.tmp/evidence/run0119-production-admin-readonly.json`、`.tmp/evidence/run0125-production-readonly.json`；生产 `/api/v1/published-models` 和 `/api/v1/strategy-integration-s3-config` 为 200，未登录任务接口为 401，`/openapi.json` 为 JSON；整体验收仍失败。
- 本地证据：目标模块 `py_compile`、`git diff --check` 通过；目标 pytest 被 `board_agent.py:634` 未闭合括号阻断，待 R07/R09 修复后重跑。
- 生产/提交状态：未连接或写入生产，未打开 Worker；未提交、未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0149_aicm_s3_evidence_audit.md`。

### RUN-0149 前端弹窗居中与中文状态展示增量（本地 L1）

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品/UI体验 | WP5 | 统一平台弹窗居中；保持两类登录角色和中文页面边界 | 方案边界已记录；未增加项目、业务空间、回滚或设备发布菜单 |
| R08 前端 | WP5 | 14 个平台页面的 Ant Design `Modal` 补充 `centered`；模型中心和数据回流状态/制品名称中文化 | 本地代码完成；BM1688 旧扩展页已有居中遮罩，本轮未重复改造 |
| R09 测试 | WP5 | 新增 `modalCentering.test.ts` 静态契约；复核模型中心、数据回流测试 | Vitest 3 文件/13 项通过；`pnpm run type-check` 通过 |
| R03/R04 架构后端 | WP1/WP4 | 复核页面是否可绕过项目维度 | 未绕过；数据回流后端仍要求 `project_id`，列为后端去项目化阻断 |

本轮没有增加请求、轮询、数据库锁、队列消费者或外部副作用；未连接生产、S3、AICM、TPU-MLIR、BM1688，未提交、未推送、未部署。独立记录：`labelu/docs/7gai_run_0149_frontend_modal_cn_audit.md`。页面体验达到本地 L1，不等于业务闭环或生产验收完成。

### RUN-0148 BM1688 编译前缀、三精度落盘与真机通道审计（本地 L1）

- 责任角色/工作包：R06 模型算法、R07 BM1688 边端、R09 测试主责；R03/R04/R10 协作；WP3。
- 代码修复：`_finalize_bmodel_status` 使用训练权重实际模型前缀判断 `FP32/FP16/INT8` 三份 BModel，避免 `yolov11s` 文件被默认 `yolov11n` 配置误判为 `ONNX_READY`。
- 路径结论：服务器执行 PT→静态 ONNX→BModel，BM1688 只加载推理；Board Agent 主动拉取为目标主路径，当前 scheduler/执行器仍实际走受控 SSH，切换前禁止双消费者。
- 本地证据：BM1688 编译流程与契约 `10 passed`；新增非默认模型前缀三精度回归；`compileall`、`git diff --check` 通过。
- 生产证据：无。真实 TPU-MLIR、三精度同批制品、冻结评估集、真机精度/P95/短稳、Board Agent 领取、SSH 双通道切换、MySQL 双 Worker 和 systemd 均待验收。
- 提交/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0148_bm1688_flow_audit.md`。

### RUN-0148 并发与 Worker 重启接管验收契约（本地 L1）

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色/工作包：R03 架构、R09 测试、R10 运维；WP2 训练执行、WP3 BM1688/边端执行、WP5 生产运维。
- 实施内容：`rehearse_mysql_execution_fencing.py` 支持 2-64 个真实竞争进程，生产标准值为 `--并发数 20`；新增并发边界测试；四类独立 Worker systemd 单元补齐默认关闭、失败重启、停止超时、SIGTERM 和双消费者隔离静态契约；训练 Worker 单元补 `TimeoutStopSec=120/KillSignal=SIGTERM`。
- 本地证据：并发门禁与 systemd 契约 `8 passed`；脚本编译通过；`git diff --check` 通过。
- MySQL 验收规则：同版本本机 `labelu_migration_sandbox*` 才允许真实演练；20 个竞争者只能 1 个领取，19 个失败，旧 token 接管后写入必须为 0 行，新 token 写入为 1 行，并输出锁等待 JSON。未执行真实 MySQL 前不得标记通过。
- 生产/真实环境证据：无。未连接生产、未修改生产数据、未执行 Ubuntu/systemd 重启、生产快照、锁等待、GPU/板卡资源或双 Worker 接管；生产 Worker 开关保持关闭。
- 提交/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0148_concurrency_acceptance.md`。

### RUN-0147 独立 YOLO/车牌训练 Worker 可消费门禁与动态超参 fencing（本地 L1）

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色/工作包：R03 架构、R04 后端、R06 模型算法、R09 测试主责；R10 运维协作；WP2。
- 需求编号：`TRAIN-WORKER-ENABLE-01`、`TRAIN-FENCING-HYPERPARAM-01`。
- 改动文件：`yolo_train_worker.py`、`yolo_training/hyperparams.py`、`yolo_train.py`及对应 Worker/超参测试；新增 `labelu/docs/7gai_run_0147_training_worker_enablement.md`。
- 数据/API/页面影响：训练 API 和页面字段不变；满足租约、安全和数据库结构前置时，独立 Worker 可以领取本地 YOLO/车牌作业；动态超参应用增加 owner/token 门禁。
- 测试结果：`78 passed`；`compileall`、`git diff --check` 通过。
- 生产/真实环境证据：无。生产训练开关继续关闭，未执行 GPU、MySQL、systemd 或真实训练。
- 并发性能：保留 queue lease + execution lease/fencing；双 Worker、20 并发和 GPU 槽位实测待验收。
- 提交/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0147_training_worker_enablement.md`。

### RUN-0146 旧策略投影与正式任务血缘回写验收（本地 L1）

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色/工作包：R03 架构、R04 后端、R09 测试主责；R05 外部平台协作；WP4。
- 需求编号：`S3-LEGACY-BRIDGE-03`、`S3-TASK-LINEAGE-01`。
- 改动文件：`labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py`；新增 `labelu/docs/7gai_run_0146_legacy_projection_lineage_test.md`。
- 数据/API影响：只新增 Worker 完成态投影和任务血缘回写测试；`experiment_dataset_import` 仍是导入真源，旧策略表不创建样本。
- 测试结果：第三方导入路由、旧入口、兼容转换和 Worker 租约联合 `40 passed`；`compileall`、`git diff --check` 已通过。
- 生产/真实环境证据：无。真实 S3/AICM、旧调用方、MySQL 并发、systemd 和生产 Worker 未验收。
- 提交/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0146_legacy_projection_lineage_test.md`。

### RUN-0145 迁移审计测试契约刷新与联合回归（本地 L1）

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色/工作包：R03 架构、R04 后端、R09 测试主责；R10 运维协作；WP2/WP4。
- 需求编号：`MIG-TEST-CONTRACT-03`、`MIG-PROJECT-SCOPE-AUDIT-02`。
- 改动文件：`labelu/tests/alembic/test_phase_one_migration_plan.py`、`labelu/tests/alembic/test_phase_one_schema_scope.py`、`labelu/internal/common/phase_one_migration_plan.py`；新增 `labelu/docs/7gai_run_0145_migration_test_contract_refresh.md`。
- 数据/API影响：仅刷新迁移和去项目化审计断言，不改业务 API、正式 DDL、生产数据和登录角色。
- 测试结果：旧入口、S3 兼容、Worker、迁移 head、租约、项目范围和空库升级联合 `35 passed`；`compileall`、`git diff --check` 通过。
- 生产/真实环境证据：无。生产 MySQL、S3/AICM、systemd、TPU-MLIR、BM1688 和并发仍未验收。
- 并发性能：本轮仅保留扫描索引和 fencing 结构门禁；双 Worker、20 并发和 MySQL 锁等待待 R09/R10 验收。
- 提交/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0145_migration_test_contract_refresh.md`。

### RUN-0143 旧策略 S3 异步契约与本地迁移结构收口（本地 L1）

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色/工作包：R03 架构、R04 后端、R09 测试主责；R05 外部平台、R10 运维协作；WP4 第三方 S3 回流闭环。
- 需求编号：`S3-LEGACY-BRIDGE-02`、`MIG-IMPORT-WORKER-TEST-01`。
- 改动文件：`labelu/tests/conftest.py`、`labelu/tests/internal/adapter/routers/test_strategy_task_integration.py`、新增 `labelu/tests/internal/application/service/test_legacy_s3_adapter.py`；同步 RUN-0143 文档、角色计划和台账。
- 数据/API影响：测试 SQLite 补齐 `experiment_dataset_import` 来源字段、Worker owner/过期时间/fencing/attempt 字段及扫描索引；旧 POST 测试改为 `201 + PENDING + package_id + status_url`，请求结束不创建任务、数据源或样本；旧状态 GET 保留兼容验证。
- 测试结果：旧策略入口 `8 passed`；兼容转换、旧 metadata 格式、Worker 开关专项 `12 passed`；`compileall` 和 `git diff --check` 通过。
- 生产/真实环境证据：无。本地测试结构补丁不等价于 MySQL 迁移；生产只读数据库 head 仍需按正式 `z7a8b9c0d1e2` 对账，真实 S3/AICM/Worker/systemd 未测。
- 并发性能：请求只写入队状态，避免 Web 线程执行 S3；唯一包 ID和 Worker 租约测试通过；MySQL 锁等待、双 Worker、20 并发仍未验证。
- 风险/遗留：第三方调用方是否接受 `201 + PENDING` 未确认；模型未发布等待、真实 metadata 对账、跨包去重和生产迁移仍阻断放行。
- 下一依赖：R03/R10执行同版本 MySQL 沙箱迁移与执行计划对账；R05/R09提供真实脱敏 S3 包、AICM ID和外部重试证据。
- 提交/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0143_legacy_adapter_test_contract.md`。

### RUN-0142 旧策略 S3 入口统一回流兼容层（本地 L1）

- 日期和时区：2026-09-19，Asia/Shanghai。
- 责任角色/工作包：R03 架构、R04 后端主责；R05 外部平台、R09 测试、R10 运维协作；WP4 第三方 S3 回流闭环。
- 需求编号：`S3-LEGACY-BRIDGE-01`。旧 `POST /api/v1/integrations/strategy-tasks` 保留 URL、验签和基本响应字段，但不再同步读 S3、创建任务、样本或预标注。
- 代码影响：旧命令转换为 `CreateExperimentDatasetImportCommand`，稳定生成兼容 `package_id`，写入 `source_integration_id/source_package_id` 投影；新增旧入口状态查询；新 Worker 统一执行。新解析器兼容 `samples/images/根数组/单样本`、旧图片名和 `x/y/width/height/label` 框，并对框全部失效计为异常。
- 页面/API/数据影响：旧接口响应新增可选 `package_id/status_url`；新真源为 `experiment_dataset_import`，`strategy_task_integration` 只做策略/回调投影；不恢复项目/业务空间。
- 权限/安全：旧 v2 请求仍按旧 URL 校验 HMAC；转换后继续经过 AICM 已发布模型门禁、Worker 租约和 fencing；HTTP 201 仅表示已入队，禁止伪造 `COMPLETED`。
- 并发性能：复用 `package_id` 唯一键、聚合键和 Worker 租约；请求路径不执行 S3 I/O，避免 Web 请求占用连接和线程；跨包内容哈希去重、真实 MySQL 20 并发仍未验收。
- 本地验证：`compileall` 和 `git diff --check` 通过；旧同步测试中依赖请求内 `COMPLETED` 的断言属于契约迁移项，尚未作为本轮生产通过证据。真实 S3/AICM/Worker/外部调用方未测。
- 风险/遗留：旧协议日期窗口缺失时使用 `unknown` 哨兵；真实 `metadata.json` 字段、AICM ID 一致性、201+PENDING 是否被第三方接受需 R05 提供脱敏样例；旧历史已完成行保持只读兼容。
- 下一依赖：补新旧入口转换快照、重复通知、模型未发布等待、旧 metadata/框格式、Worker 完成后投影查询测试；再在预生产用真实 S3 包和 MySQL 并发验收。
- 恢复/提交状态：代码和文档均未提交、未推送、未部署；等待用户明确“提交代码”。失败可停用旧入口兼容开关并保留新入口数据，不删除已发布模型或历史包。
- 独立记录：`labelu/docs/7gai_run_0142_legacy_s3_adapter_analysis.md`。

### RUN-0141 BM1688 三精度与边端质量门禁审计（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0141_bm1688_quality_gate_audit.md`。
- 交付角色：R06 模型算法、R07 BM1688 边端、R09 测试；本轮不新增设备发布、OTA 或模型回滚。
- 审计结论：FP32/FP16/INT8 必须作为同一模型版本的三份边端制品留存；PT 先导出静态 ONNX，再由编译节点生成 BModel；BM1688 只加载推理。单精度编译只能用于诊断，不能绕过发布门禁。
- 执行通道：Board Agent 主动拉取作为主路径，SSH 作为受控兼容验证路径；二者都不是发布控制面。
- 本地证据：BM1688 规则/编译契约 `7 passed`；边端流程/评估证据 `22 passed`。
- 生产边界：真实 TPU-MLIR、BM1688、同一冻结评估集、P95/短稳和 AICM 发布仍未验收。
- 状态：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0139 模型中心发布目录与 AICM 对齐审计（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0139_model_publish_catalog.md`。
- 交付角色：R01 产品、R04 后端、R05 外部平台、R08 前端、R09 测试；本轮未新增菜单、登录角色、项目空间、设备发布或模型回滚。
- 核对结论：新发布必须以 AICM `model_set_id` 作为 `public_model_id`，成功后强制保留 LabelU 本地 ZIP；`ModelVersion` 回写目录血缘；下载开关使用当前值条件更新；第三方 S3 回流按同一 AICM ID 解析导出、训练和标签分类。
- 本地证据：模型目录服务、路由和前端桌面/窄屏专项 `22 passed, 2 skipped`。
- 生产只读证据：`/openapi.json` 为 JSON；健康入口仍被 SPA 回退；`/api/v1/published-models` 未带凭据返回 401，`/api/v1/datasets` 与 `/api/v1/model-versions` 仍为 404。
- 生产边界：真实 AICM 发布/对账、平台下载、S3 回流、MySQL 并发、迁移、systemd 和浏览器真机未验收；不得把本地 L1 写成生产完成。
- 状态：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0138 正式迁移 head 与历史后缀映射收口（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0138_migration_head_contract.md`。
- 交付角色：R03 架构、R04 后端、R09 测试；不修改正式迁移 DDL，不连接生产数据库。
- 交付内容：审计 head 更新为 `z7a8b9c0d1e2`，历史后缀计划纳入 `z5/z6/z7`，覆盖 S3 Worker 租约迁移；修正旧测试仍断言 `z4` 的漂移。
- 本地证据：迁移审计、重建审计、隔离预演和租约候选专项 `15 passed`。
- 生产边界：同版本 MySQL、空库/生产快照升级、锁等待、备份恢复和实际 Worker 仍未验收；生产开关继续关闭。
- 状态：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0137 BM1688/边端执行期租约续期与 fencing 收口（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0137_execution_fencing.md`。
- 交付角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试；不新增菜单、登录角色、项目空间、设备发布或模型回滚。
- 交付内容：边端长阶段心跳改为 owner/token/未过期条件写入；失租 Worker 不再刷新 `updated_at`；BM1688 独立 Worker 启动前检查三项执行租约字段，未迁移时零领取、零编译并失败关闭。
- 本地证据：BM1688 Worker、边端恢复和 fencing 专项 `26 passed`；`compileall`、`git diff --check` 通过。
- 生产边界：未执行正式 Alembic DDL、同版本 MySQL 双进程/20 并发、systemd、真实 TPU-MLIR/BM1688；独立 Worker 开关继续关闭。
- 状态：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0136 第三方 S3 回流 Worker 失败关闭与重启接管门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0136_s3_worker_fail_closed_lifecycle.md`。
- 交付角色：R03 架构、R04 后端、R05 外部平台与回流、R09 测试、R10 运维；不新增 S3 轮询协议、不新增菜单/角色/项目空间。
- 交付内容：Worker 启动前校验租约迁移字段和生产安全配置；新增 `--preflight` 与退出码 78；systemd 增加 `ExecStartPre` 和 `RestartPreventExitStatus=78`；单轮单包异常不阻断后续包；聚合租约失效时旧 Worker 不覆盖新 Worker 的失败状态。
- 本地证据：Worker 专项 `6 passed`；部署契约 `2 passed`；`compileall` 和 `git diff --check` 通过。
- 生产边界：Windows 无可用 WSL，未执行真实 `bash -n`/`systemd-analyze verify`；同版本 MySQL 双进程/20 并发、真实 S3 大包、AICM 对账和生产部署仍未验收；开关继续关闭。
- 状态：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0134 生产 OpenAPI 与健康入口只读复核（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0134_production_openapi_gap_recheck.md`。
- 交付角色：R03 架构、R04 后端、R09 测试、R10 运维；R01 产品同步生产菜单/API差距。
- 核验内容：生产 `/openapi.json` 真实 JSON、295 条路由、规范主链缺失、退休设备控制面仍暴露、健康入口被 SPA 回退、22 端口不可达。
- 结论：本地规范路由尚未部署，生产不能放行；执行器和 BM1688 开关继续关闭。
- 状态：只读核验，未提交、未推送、未部署，未修改生产数据。

### RUN-0133 BM1688 编译恢复 Worker 边界（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0133_bm1688_recovery_worker_boundary.md`。
- 交付角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试、R10 运维。
- 交付内容：BM1688 自动恢复在独立 Worker 开启时不再启动 Web 编译线程；失租 `PROCESSING` 事务回到 `PENDING` 并保留 `worker_action`。
- 验证：BM1688 恢复/入队定向专项 `2 passed`；训练 Worker/队列、边端调度/Worker、BM1688 恢复/入口合并回归 `100 passed`；Python 编译通过。
- 边界：真实 systemd、TPU-MLIR、MySQL、编译节点和生产部署尚未验证；边端自动创建验证任务的直接执行入口待下一轮审计。
- 状态：未提交、未推送、未部署。

### RUN-0132 边端验证独立 Worker 消费路径失败关闭（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0132_edge_worker_fail_closed.md`。
- 交付角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试、R10 运维。
- 交付内容：边端 Worker 开关打开后，Web 只分配设备；Worker 未就绪时禁止 Web SSH/推理线程，已分配作业保留等待独立 Worker。
- 验证：边端调度专项 `9 passed`；Python 编译通过。
- 边界：真实 systemd、MySQL、SSH/BM1688、设备重启接管和生产部署尚未验证；生产开关继续关闭。
- 状态：未提交、未推送、未部署。

### RUN-0131 训练独立 Worker 消费路径失败关闭（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0131_training_worker_fail_closed.md`。
- 交付角色：R03 架构、R04 后端、R06 模型算法、R09 测试、R10 运维。
- 交付内容：独立训练开关打开后，训练请求只进入持久队列；Worker 未就绪时禁止 Web 训练线程、数据库恢复和 Web 看门狗消费；远程训练保持原路径。
- 验证：训练 Worker/队列专项 `53 passed`；Python 编译和 diff-check 通过。
- 边界：真实 Ubuntu systemd、MySQL 双进程/20 并发、GPU、Worker 重启接管和生产部署尚未验证；生产开关继续关闭。
- 状态：未提交、未推送、未部署。

### RUN-0130 第三方 S3 回流长任务租约续期（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0130_s3_import_lease_renewal.md`。
- 交付角色：R03 架构、R04 后端、R05 外部平台、R09 测试。
- 交付内容：增加 owner+fencing token 条件续期；长 S3 导入期间由独立 Session 心跳续租；失租后阻止旧 Worker 写最终状态。
- 验证：租约与续期专项 `3 passed`；compileall 和 diff-check 通过；既有 S3 路由 `19 passed` 证据沿用 RUN-0128。
- 边界：真实 S3 大包、MySQL 锁等待、systemd 重启接管仍未验收；未提交、未推送、未部署。

### RUN-0129 第三方 S3 回流 Worker 部署契约（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0129_s3_worker_deployment_contract.md`。
- 交付角色：R03 架构、R04 后端、R05 外部平台、R09 测试、R10 运维。
- 交付内容：新增 `labelu-experiment-dataset-worker.service` 渲染函数；API 单元显式 fail-closed；接入 start/stop/restart/reset-clean、参数校验和部署检查清单；新增 shell 契约测试。
- 验证：PowerShell 等价静态契约 11 项通过；Python 部署契约 `2 passed`，Worker/迁移联合回归 `6 passed`，禁用开关命令行烟测通过；`git diff --check` 通过。Windows 无可用 WSL bash，shell 执行和 systemd 尚未验收。
- 生产边界：默认开关为 0；Ubuntu `bash -n`、`systemd-analyze verify`、MySQL 双进程/20 并发、真实 S3 大包下载仍是下一依赖。
- 状态：未提交、未推送、未部署。

### RUN-0128 第三方 S3 问题数据回流持久化 Worker（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0128_s3_import_durable_worker.md`。
- 交付角色：R03 架构、R04 后端、R05 外部平台、R09 测试、R10 运维。
- 交付内容：回流表增加 Worker 租约和 fencing token；接口在独立 Worker 开启时只入队；新增独立 Worker 命令入口，复用 S3 URI、metadata.json、去重、任务创建和预标注状态机。
- 验证：Worker 租约专项 `3 passed`；迁移契约 `1 passed`；既有 S3 导入路由 `19 passed`；compileall 和 diff-check 通过。
- 并发边界：旧 Worker 迟到写入被 owner+token 条件拒绝，过期租约可接管；真实 MySQL、20 并发、systemd、生产 S3 尚未验证。
- 状态：未提交、未推送、未部署；生产开关默认关闭，完成同版本 MySQL、Ubuntu/systemd 和真实 S3 小流量验收前不得打开。

> 该历史段落当时的最新轮次：`RUN-0127`。本轮由 R03 架构、R04 后端、R06 模型算法和 R09 测试协作，完成策略演化/专家回流新写链去项目化；本地 L1，未提交、未推送、未部署。真实 TPU-MLIR、BM1688、固定评估集同批对比、MySQL、Worker、S3、AICM 和生产环境仍未验收。

### RUN-0127 策略演化与专家回流新写链去项目化（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0127_strategy_evolution_no_project.md`。
- 交付角色：R03 架构主责，R04 后端、R06 模型算法、R09 测试协作；不新增菜单、按钮、登录角色、项目/业务空间、设备发布或模型回滚。
- 本轮改动：`CreateStrategyEvolutionTaskCommand` 删除 `project_id` 并拒绝新请求显式携带该字段；`accept_task` 不再写项目字段、不再创建 `WorkflowInstance`；平台回调不再追加项目工作流事件；出站回调保留原 HTTP 签名、重试和回执幂等；专家回流命令、领域模型、响应和路由保持无项目字段。
- 历史兼容：数据库历史 `project_id` 列、旧迁移和独立兼容读取不做破坏性删除；历史列不参与新请求、权限、响应、幂等键或新增对象。
- 本地证据：策略去项目专项 `5 passed`；策略回流路由专项 `8 passed`；策略演化完整路由回归 `35 passed`；`compileall` 和 `git diff --check` 通过。
- 生产边界：尚未核验生产 OpenAPI 请求 schema、生产 MySQL 历史列和第三方 S3/AICM/Worker；不得把本地证据写成生产完成。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L1 通过，L2/L3/L4 未完成。

### RUN-0126 BM1688 真机验证证据门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0126_edge_validation_evidence_gate.md`。
- 交付角色：R07 边端主责，R03 架构、R04 后端、R06 模型算法、R09 测试协作；不新增菜单、按钮、登录角色、项目维度、设备发布或回滚。
- 本轮交付：新增 `bm1688_edge_validation_evidence.v1` 证据契约；回传报告校验真实设备身份、模型/制品执行签名、FP32/FP16/INT8 三精度实际执行、固定评估集摘要、有效推理数、精度、P95、错误率、60 秒短稳和 fencing token。证据缺失时写入 `PARTIAL_COMPLETED` 与机器可读原因，不能作为发布通过。
- Board Agent 仅用于内网 BM1688 验证，不在板端编译、不变成设备发布控制面；父链校验为导出制品→训练作业→任务，历史 `project_id` 只保留兼容一致性检查。
- 本地证据：边端证据、Board Agent 路由和运行脚本专项共 `20 passed`；本轮文档和代码尚未提交。既有弃用警告不影响测试结论。
- 生产边界：未取得真实 BM1688 三精度、固定评估集、P95/短稳、MySQL 双进程、systemd 或生产部署证据；缺证据时保持发布阻断。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L1 通过，L2/L3/L4 未完成。

### RUN-0125 TPU-MLIR 编译链与制品证据门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0125_tpu_mlir_compile_gate.md`。
- 交付角色：R06 模型算法、R07 BM1688 边端主责；R03 架构、R04 后端、R09 测试协作。不新增菜单、按钮、登录角色、项目维度或模型回滚。
- 本轮发现：Sophon 导出实际为静态 `opset=17`，但旧制品清单硬编码记录 `opset=12`；已有三精度文件检查缺少集中证据契约。
- 本轮交付：新增统一 ONNX 导出契约；制品清单按实际 Sophon/兼容导出分支记录 opset、静态 batch 和后端；新增 BM1688 编译证据契约，检查 PT 后 ONNX、三精度 BModel、校准集、工具链版本、制品执行签名和固定评估集证据。
- 并发/失败处理：契约为只读检查，不增加数据库锁；三精度、校准集、工具链版本或固定评估集缺失时明确阻断，不显示发布通过；不执行真实编译，不替代设备租约和 fencing。
- 本地证据：编译契约与制品清单 `9 passed`；BM1688 编译流/自动编译 `8 passed`；质量评估 `26 passed`；发布范围/质量推荐 `13 passed`；`compileall` 和 `git diff --check` 通过；本轮 Python 代码和文档尚未提交。
- 生产边界：没有连接 TPU-MLIR/BM1688、生产 MySQL、S3、AICM 或生产 Worker；没有真实三精度、同批固定评估集、P95/短稳和部署证据。
- 生产只读复核：`.tmp/evidence/run0125-production-readonly.json`；健康/登录及旧训练、发布、BM1688、边端、S3 查询通过，但规范 OpenAPI 仍缺少 `/api/v1/datasets`、`/api/v1/algorithms`、`/api/v1/model-versions`、`/api/v1/workspace/summary`，并暴露 `/api/v1/edge-device-jobs`，生产路由门禁失败，未部署本地改动。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L1 通过，L2/L3/L4 未完成。

> 当前历史轮次：`RUN-0124`。本轮完成生产部署的 canonical OpenAPI 路由硬门禁和共享路由契约；本地 L1，未提交、未推送、未部署。
> 最新轮次索引补充：`RUN-0117` 已接入独立边端验证 Worker 部署生命周期，`RUN-0118` 已将验收基线改为无项目，`RUN-0119` 已完成生产只读差距证据，`RUN-0120` 已完成第三方 S3 接入去项目化与 Provider 兼容收口，`RUN-0121` 已完成 Provider 预标注作业去项目化，`RUN-0122` 已完成训练标签子集与不可变数据版本，`RUN-0123` 已完成训练 Worker 沙箱门禁，`RUN-0124` 已完成 canonical OpenAPI 路由门禁；正式 MySQL 沙箱、生产脱敏快照、systemd、真实 S3/AICM/TPU-MLIR/BM1688 和真实 Worker 仍待验证。

### RUN-0124 生产迁移与 canonical OpenAPI 路由门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0124_production_migration_and_route_gate.md`。
- 交付角色：R03 架构与迁移复核、R04 后端/部署脚本主责、R09 测试验收、R10 运维生产协作；不新增菜单、按钮、登录角色或项目维度。
- 本轮交付：新增 `scripts/ops/7gai_openapi_contract.json` 共享契约；预发布验收提取 `inspect_canonical_openapi()`；`deploy.sh` 收尾读取 `/openapi.json`，校验一期主链路径和退休路径，并在检查失败时由 `deploy_finalize` 返回非零。
- 生产问题对应：健康接口 200 不再等价于业务路由正确；`/api-docs/openapi.json` 不作为真源；缺少数据集、模型版本、工作台等主链路径或重新暴露旧设备控制面，部署直接失败。
- 并发/失败处理：只读单次 HTTP 校验，不增加业务并发；curl、Python、JSON、契约或路径校验任一缺失均 fail-closed；不替代数据库迁移、Worker 租约或 BM1688 资源门禁。
- 本地证据：`python -m pytest labelu/tests/scripts/test_staging_acceptance.py -q` 为 `7 passed`；`python -m pytest scripts/tests/test_deploy_nginx_backend_routes.py -q` 为 `3 passed`；`py_compile` 与 `git diff --check` 通过。
- 环境限制：Bash 静态测试因当前 Windows `bash.exe` 指向不可用 WSL 入口未执行；真实 MySQL、systemd/Nginx、生产部署、TPU-MLIR、BM1688、S3、AICM、Worker 并发和浏览器仍未验收。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L1 通过，L2/L3/L4 未完成。
> 文档一致性：总方案、角色交付计划和本台账当前统一截至 `RUN-0126`；本轮已完成 BM1688 真机验证证据门禁，仍为本地 L1，未提交、未推送、未部署。下一轮正式编号从 `RUN-0127` 开始。

### RUN-0123 训练 Worker 生产沙箱门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0123_training_worker_sandbox.md`。
- 交付角色：R04 后端主责，R03 架构、R06 模型算法、R09 测试、R10 运维协作；不新增菜单、按钮、登录角色或项目维度。
- 本轮交付：核对 queue lease、execution lease/fencing、GPU 槽位、训练子进程上下文、终态写入和独立 Worker 开关的 fail-closed 边界；明确 `dedicated_worker_ready()` 在真实迁移、子进程全链路 fencing 和执行器证据完成前必须返回不可用。
- 并发/失败处理：同一作业只有一个 queue owner；过期租约可接管；旧 token 不能写入新 owner 状态；字段、GPU、Runner 或迁移不可确认时不领取、不启动训练。
- 本地证据：训练 Worker 专项 `10 passed`；通用 Worker Outbox 运行时/持久化 `25 passed`；`compileall`、`git diff --check` 通过。
- 生产边界：未执行真实 MySQL 锁等待/20 并发、GPU/远程节点、systemd、节点重启接管、真实训练长任务；`YOLO_TRAIN_DEDICATED_WORKER_ENABLED` 继续保持关闭。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L1 通过，L2/L3/L4 未完成。

### RUN-0122 统一训练标签子集与不可变数据版本（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0122_label_subset_dataset_freeze.md`。
- 交付角色：R04 后端主责，R01 产品、R03 架构、R06 模型算法、R09 测试协作；不新增项目/业务空间，不改变标注人员和管理人员两类登录角色。
- 菜单与按钮：复用“任务与标注 → 任务详情 → 新建训练”；训练弹窗可选择“全部标签”或当前任务标签契约中的一个/多个标签。没有新增独立 DatasetVersion 菜单，冻结版本在训练详情和模型血缘中展示。
- 本轮交付：统一训练路由传递 `label_ids`；数据版本服务校验标签契约归属，生成标签子集快照、类别顺序摘要和选择摘要；manifest 仅保留所选矩形框；原始人工标注不修改；相同任务/样本/标签/标注内容摘要才复用不可变版本，标注变更会生成新版本；YOLO Worker 从冻结快照导出所选类别。
- 权限与数据：创建者（标注人员或管理人员）可新建训练；任务可见性仍由任务权限控制；冻结后不可编辑，修改原标注只影响新版本，不改变既有训练输入。
- 并发/失败处理：冻结版本以任务、样本集合和标签集合摘要复用，避免重复生成和重复导出；标签不属于当前数据集契约时立即失败；训练导出不重新读取易变的任务标签配置。
- 本地证据：`test_yolo_explicit_negative_samples.py` 9 passed；训练任务与契约 23 passed；训练路由与 Worker 持久化 20 passed；服务级双标签冻结回归 1 passed；`compileall` 和 `git diff --check` 通过。覆盖生产历史 `result` 字符串、全量/子集版本差异和旧 manifest 不变。
- 生产边界：仅本地 Windows/SQLite 和隔离测试；真实 MySQL、生产数据、GPU、TPU-MLIR、BM1688、S3、AICM、systemd 和浏览器未验收。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L1 通过，L2/L3/L4 未完成。

### RUN-0121 Provider 预标注作业去项目化（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0121_provider_no_project_scope.md`。
- 交付角色：R04 后端主责，R01 产品、R03 架构、R06 模型算法、R09 测试协作；Provider 注册表、预标注作业、预测集和审核审计均不再要求项目维度。
- 菜单与按钮：复用“任务与标注 → 任务详情 → 开始预标注”；能力列表只按图片类型、标注工具、标签契约和模型能力筛选；创建作业、重试、提交预测和审核均使用弹窗确认，不新增项目选择控件。
- 本轮交付：Provider 作业/预测集/审核审计/任务投影接口移除项目字段；创建者权限和任务可见性校验；HTTP 路由透传 `Idempotency-Key`；同一作业版本不同预测结果返回冲突；Worker 执行前复核媒体扫描、生命周期和删除状态；注册表运行检查不走项目权限；内部 Provider 请求 SPI 删除 `project_id`。
- 并发/失败处理：同一作业版本幂等重试只接受完全一致结果；不同结果返回冲突；媒体资产状态不满足要求时 fail-closed；Worker 仍依赖租约/状态门禁，未授权的旧任务不能覆盖新结果。
- 本地证据：Provider 作业、Worker、HTTP 模拟、历史桥接、适配器、迁移 32 passed；注册表/运行时/启动/适配器组合 20 passed；OpenAPI 8 passed；合计 52 passed；唯一 head 为 `z6b7c8d9e0f1`。
- 生产边界：迁移和代码尚未部署；生产只读验收仍显示旧路由/旧字段差距，不能把本地 OpenAPI 通过当作生产通过。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L1 通过，L2/L3/L4 未完成。

### RUN-0116 测试数据库结构漂移兼容收口（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0116_test_schema_compatibility_cleanup.md`。
- 交付角色：R09 测试与验收主责，R03 架构/迁移复核，R04 后端协作，R07 边端业务流复核；不新增菜单、用户角色、项目维度或生产字段。
- 本轮交付：pytest 跳过 Alembic 并复用旧 SQLite 时，对 `task.dataset_id`、`task.dataset_label_contract_id`、`task.task_label_scope_mode` 和 `yolo_bm1688_export.worker_action` 做增量兼容；正式生产迁移仍以 Alembic 为唯一来源。
- 本地证据：使用确实缺列的旧库副本先复现失败，再运行 `labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py -q`，结果 `2 passed`；新库同专项亦为 `2 passed`。
- 数据/API/页面影响：仅测试启动夹具；不改变业务 API、页面、权限、模型制品、下载控制或生产数据。
- 并发/性能/失败处理：兼容列补齐在建表后一次执行并刷新列集合；不等价于 MySQL 迁移、锁等待或生产容量证据。
- 真实性边界：仅本地 Windows/SQLite；真实 MySQL、systemd、TPU-MLIR、BM1688、S3、AICM 和生产浏览器未验收。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0115 BM1688 与生产闭环差距审计（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0115_bm1688_production_gap_audit.md`。
- 审计角色：R06 模型算法、R07 边端、R03 架构、R09 测试、R10 运维；本轮只读，无业务代码和生产写入。
- 核心结论：服务器编译、BM1688 真机验证、六菜单/两登录角色、S3 回流、AICM 目录和独立 Worker 方向正确；但当前仍是本地 L1 研发改造蓝图，不得写成生产完成。
- 阻断项：真实 MySQL/Worker 接管、TPU-MLIR/BM1688 三精度链、第三方 S3 首轮重训、AICM/平台下载对账、预标注-人工确认-DatasetVersion 冻结闭环、开源二开治理。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0114 边端验证独立 Worker 最小执行入口（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0114_edge_validation_dedicated_worker.md`。
- 交付角色：R07 边端主责，R04 后端协作，R03 架构复核，R09 测试验收，R10 负责后续 systemd/设备演练。
- 本轮交付：新增独立边端验证 Worker 运行时和脚本；Web 调度器在开关打开且字段就绪时只分配设备，不起 SSH/推理线程；复用现有 `_run_manual_job_sync` 和 execution lease。
- 本地证据：Worker/调度器专项 `11 passed`；`py_compile`、`git diff --check` 通过。
- 基线问题：合并 BM1688 业务流曾因本地测试库缺少 `task.dataset_id` 失败；该结构漂移已在 RUN-0116 补齐兼容列并在旧库副本回归通过。
- 生产边界：未执行 MySQL、systemd、真实 SSH、BM1688、GPU/板卡资源、S3、AICM 或生产写入；开关保持关闭。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0113 训练子进程终态与阶段文件 fencing 收口（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0113_train_terminal_fencing_cleanup.md`。
- 交付角色：R04 后端主责，R06 模型算法协作，R09 测试验收，R03 架构复核；R07/R10 负责后续真实执行器和 MySQL/systemd 验证。
- 本轮交付：异常退出写入 `phase.json` 时传递 owner/token；训练最终收尾标记取消时保留执行租约上下文；旧 Web 无租约路径保持兼容。
- 本地证据：YOLO 稳定性、独立 Worker、车牌训练专项 `58 passed`；`py_compile` 和 `git diff --check` 通过。
- 数据/API/页面影响：不新增菜单、按钮、用户角色、项目维度、API、数据库字段或模型制品格式。
- 并发/失败处理：旧 token 不能覆盖新 Worker 阶段文件和终态；仍未完成真实 MySQL 锁等待、双进程接管、GPU/systemd 释放。
- 真实性边界：仅本地 Windows/SQLite 和模拟/隔离测试；真实 GPU、TPU-MLIR、BM1688、S3、AICM 和生产浏览器未验收，独立训练 Worker 继续 `fail-closed`。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0112 执行租约正式迁移链落地（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0112_formal_execution_lease_migration.md`。
- 交付角色：R03 架构与迁移主责，R04 后端协作，R09 测试验收；R10 负责后续同版本 MySQL 沙箱和备份恢复演练。
- 本轮交付：新增唯一后继 `z4a5b6c7d8e9`，覆盖三张执行任务表的 queue/execution 租约字段、Worker 扫描索引、BM1688 `worker_action`，以及训练 Worker 使用的 `assigned_gpu_json`、`execution_runtime_pid`；历史空动作不做全表回填，由 Worker 按 `export` 兼容。
- 本地证据：正式迁移结构、重复升级/降级、唯一 head、漂移审计和一期候选预演专项通过；正式 Alembic head 更新为 `z4a5b6c7d8e9`。完整命令和结果见独立记录。
- 数据/API/页面影响：只补生产执行器所需数据库结构和审计测试，不新增菜单、用户角色、项目维度、模型回滚或新业务接口；已发布模型异常仍通过人工禁止下载处理。
- 并发/风险：迁移以非空历史表为前提，执行期 token 默认 0，Worker 扫描使用复合索引；SQLite 只能证明结构和幂等，MySQL 锁等待、在线 DDL、20 并发和真实数据规模尚未验证。
- 生产边界：未连接生产、未执行正式迁移、未启动真实 TPU-MLIR/BM1688 Worker；在同版本 MySQL 沙箱、生产脱敏快照、备份恢复和执行计划完成前，独立 Worker 继续 `fail-closed`。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。本轮达到本地 L1，不代表生产放行。

### RUN-0110 执行租约 ORM 与正式 Alembic 链漂移审计（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0110_execution_lease_migration_drift.md`。
- 交付角色：R03 架构与迁移主责，R04 后端协作，R09 测试验收；R10 后续提供受控数据库演练环境。
- 本轮交付：新增只读 `audit_execution_lease_migration.py` 及审计测试；确认正式 head 唯一为 `z3a4b5c6d7e8`，但 `execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token` 三表均无正式迁移来源；租约/Worker 扫描索引和 `yolo_bm1688_export.worker_action` 字段/索引仅存在 ORM/一期候选。
- 本地证据：审计、候选迁移和一期隔离预演合计 `11 passed`；正式链不完整被显式标记“存在迁移漂移”，不连接数据库、不执行迁移。
- 数据/API/页面影响：仅新增静态审计和测试，不新增菜单、用户角色、项目维度、正式表结构或生产 API；候选 SQLite 仍只作结构演练。
- 并发/风险：空库 bootstrap 按当前 ORM 建表，不能证明历史生产库升级覆盖租约；正式迁移缺字段/索引前，派发和独立 Worker 保持 fail-closed。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0/L1 通过，正式迁移 L2/L3/L4 未完成。

### RUN-0109 独立 BM1688 Worker 执行租约领取门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0109_bm1688_worker_execution_lease_gate.md`。
- 交付角色：R04 后端主责，R03 架构、R06 模型算法、R07 边端、R09 测试、R10 运维协作；不新增菜单、用户角色、项目维度、API 或生产表结构。
- 本轮交付：独立 BM1688 Worker 候选扫描和原子领取同时排除未过期 execution lease；真正编译入口继续保留执行租约领取和监控。
- 本地证据：独立 Worker `8 passed`；与派发、恢复、训练 fencing 合并 `153 passed`；`py_compile` 与 `git diff --check` 通过。
- 数据/API/页面影响：仅 BM1688 Worker、隔离演练表和测试变化，不改变模型制品、页面、权限或外部接口。
- 并发/性能/失败处理：有效执行者存在时零候选、零 queue lease、零编译副作用；真实 MySQL、20 并发、GPU、systemd、TPU-MLIR、BM1688、S3、AICM 和生产容量未执行。
- 真实性边界：仅本地 Windows/SQLite；独立 BM1688 Worker 仍受正式迁移和真实设备门禁约束。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0/L1 通过，L2/L3/L4 未完成。

### RUN-0108 自动派发与恢复领取的执行租约原子门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0108_dispatch_execution_lease_atomic_gate.md`。
- 交付角色：R04 后端主责，R03 架构、R06 模型算法、R07 边端、R09 测试、R10 运维协作；不新增菜单、用户角色、项目维度、API 或生产表结构。
- 本轮交付：训练/编译/边端正常派发和恢复领取均在同一 SQL 条件中排除未过期 execution lease，消除前置读取到条件更新之间的接管窗口；双进程演练表补齐执行租约列。
- 本地证据：自动派发、恢复和边端/编译专项 `64 passed`；覆盖唯一领取、过期接管和有效执行租约拒绝。
- 数据/API/页面影响：仅派发服务、隔离测试表和测试变化，不改变模型制品、页面、权限或外部接口；正式生产列仍必须由迁移提供。
- 并发/性能/失败处理：单条条件更新保持幂等；未完成正式迁移时派发/恢复失败关闭；真实 MySQL 锁等待、20 并发、执行计划、GPU、systemd、TPU-MLIR、BM1688、S3、AICM 和生产容量未执行。
- 真实性边界：仅本地 Windows/SQLite；独立训练、编译与边端 Worker 继续 fail-closed。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0/L1 通过，L2/L3/L4 未完成。

### RUN-0107 BM1688 编译与边端验证恢复执行租约门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0107_bm1688_edge_recovery_execution_lease_guard.md`。
- 交付角色：R04 后端主责，R03 架构、R06 模型算法、R07 边端、R09 测试、R10 运维协作；不新增菜单、用户角色、项目维度、API 或数据库字段。
- 本轮交付：BM1688 导出恢复和边端验证恢复读取作业后，先跳过有效 execution lease；恢复租约只负责调度交接，真正执行函数仍保留原子 execution lease 领取。
- 本地证据：编译恢复与边端恢复专项 `41 passed`；`py_compile` 与 `git diff --check` 通过。
- 数据/API/页面影响：仅恢复服务和测试变化，不改变 PT/ONNX/BModel、页面、权限或外部接口。
- 并发/性能/失败处理：有效执行租约下不创建恢复线程、不回队、不启动 SSH/编译；真实 MySQL、跨进程接管、GPU/设备、systemd、TPU-MLIR、BM1688、S3、AICM 和生产容量未执行。
- 真实性边界：仅本地 Windows/SQLite；独立编译与边端验证 Worker 继续 fail-closed。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0/L1 通过，L2/L3/L4 未完成。

### RUN-0106 自动续训与手工重启执行租约门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0106_auto_resume_execution_lease_guard.md`。
- 交付角色：R04 后端主责，R03 架构、R06 模型算法、R07 边端、R09 测试、R10 运维协作；不新增菜单、用户角色、项目维度、API 或数据库字段。
- 本轮交付：统一 `execution_lease_is_active()`；自动续训恢复和按配置重启在首次读取及事务内复核有效租约；看门狗在有效租约存在时跳过，避免重复启动训练。
- 本地证据：队列专项 `39 passed`；执行期 fencing、Worker、稳定性、队列和契约合并 `81 passed`；`py_compile` 与 `git diff --check` 通过。
- 数据/API/页面影响：仅训练恢复服务、租约公共函数和测试变化，不改变 PT/ONNX/BModel、页面、权限或外部接口。
- 并发/性能/失败处理：有效租约下重复启动 fail-closed；事务内复核缩小接管窗口；非法时间值按失效处理；真实 MySQL、双进程、20 并发、GPU、systemd、TPU-MLIR、BM1688、S3、AICM 和生产容量未执行。
- 真实性边界：仅本地 Windows/SQLite；独立 YOLO/车牌 Worker 继续 fail-closed。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0/L1 通过，L2/L3/L4 未完成。

### RUN-0105 旧训练队列路径执行租约门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0105_legacy_queue_lease_guard.md`。
- 交付角色：R04 后端主责，R03 架构、R06 模型算法、R07 边端、R09 测试、R10 运维协作；不新增菜单、用户角色、项目维度、API 或数据库字段。
- 本轮交付：资源不足回队和僵尸恢复在修改 `PROCESSING` 作业前跳过有效执行租约；事务内再次确认，避免旧 Web 队列覆盖独立 Worker；queue lease 与 execution lease 保持分离。
- 本地证据：队列专项 `38 passed`；训练 Worker/生命周期/队列/执行契约合并 `72 passed`。
- 数据/API/页面影响：仅训练队列服务和测试变化，不改变模型制品、页面或外部接口。
- 并发/性能/失败处理：防止重复领取、错误重启和旧回队状态覆盖；真实 MySQL 锁等待、双进程、20 并发和 GPU 性能未执行。
- 真实性边界：仅本地 Windows/SQLite；真实 MySQL、systemd、GPU、TPU-MLIR、BM1688、S3、AICM、浏览器和生产观察均未完成；独立 YOLO/车牌 Worker 继续 fail-closed。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0/L1 通过，L2/L3/L4 未完成。

### RUN-0104 训练结果写入统一 fencing（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0104_legacy_train_result_fencing.md`。
- 交付角色：R04 后端主责，R06 模型算法、R07 边端、R09 测试协作，R03 架构/R10 运维复核；不新增菜单、用户角色、项目维度、API 或数据库字段。
- 本轮交付：新增 `_assert_train_result_write_fence()`，统一保护训练启动、失败、取消、完成和异常收尾；带 owner/token 必须匹配未过期租约；无上下文旧路径在有效租约存在时拒绝；失败写入在同一数据库事务内再次确认，缩小接管窗口。
- 本地证据：训练 Worker、生命周期和队列专项 `64 passed`；新增隔离 SQLite 行验证旧无上下文写入被有效租约拒绝。
- 数据/API/页面影响：仅训练服务和测试变化，不改变 PT/ONNX/指标/模型制品格式，无页面或接口变化。
- 并发/性能/失败处理：旧进程不能覆盖新 owner 的状态；真实 MySQL 锁等待、双进程、20 并发和 GPU 性能未执行。
- 真实性边界：仅本地 Windows/SQLite；真实 MySQL、systemd、GPU、TPU-MLIR、BM1688、S3、AICM、浏览器和生产观察均未完成；独立 YOLO/车牌 Worker 继续 fail-closed。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0/L1 通过，L2/L3/L4 未完成。

### RUN-0103 独立训练 Worker 嵌套子进程收尾 fencing（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0103_nested_train_fencing.md`。
- 交付角色：R04 后端主责，R06 模型算法、R07 边端、R09 测试协作，R03 架构/R10 运维复核；不新增菜单、用户角色、项目维度、API 或数据库字段。
- 本轮交付：大图/多卡训练的第二层子进程继续透传 `execution_owner` 和 `execution_fencing_token`；启动卡死、异常退出和生命周期收尾前按未过期租约确认；失租或旧 token 只记录忽略，不写 `phase.json`、失败状态或新 owner 的终态；无上下文历史 Web 路径保持两参数兼容。
- 本地证据：训练生命周期和队列专项 `54 passed`；覆盖 watchdog 上下文透传、失租失败收尾拒绝、队列退出处理、暂停/完成和兼容路径。
- 数据/API/页面影响：仅修改训练服务函数参数和测试，无菜单、权限、数据库结构、模型制品格式或外部接口变化。
- 并发/性能/失败处理：拒绝嵌套旧进程迟到失败写入；训练租约、GPU、MySQL 锁等待和真实多卡性能本轮未执行。
- 真实性边界：仅本地 Windows/SQLite/模拟进程；真实 MySQL 双进程、systemd、GPU、共享目录、TPU-MLIR、BM1688、S3、AICM、浏览器和生产观察均未完成；独立 YOLO/车牌 Worker 继续 fail-closed。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0/L1 通过，L2/L3/L4 未完成。

### RUN-0101 车牌训练组件制品世代隔离（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0101_plate_phase_generation.md`。
- 交付角色：R04 后端主责，R06 模型算法、R07 边端、R09 测试协作；未新增菜单、用户角色、项目维度或数据库字段。
- 本轮交付：车牌检测/识别训练子进程透传 owner/token；组件状态、PT/ONNX 制品和最终终态写入均受执行 fencing；带租约制品写入原子世代标记，恢复时校验 owner、token、世代和文件路径；旧/损坏/篡改标记拒绝恢复；旧 Web 无上下文但检测到有效租约时拒绝组件/终态写入；无租约历史 Web 路径保持兼容。
- 本地证据：车牌训练专项 `65 passed`；阶段/Worker 回归 `16 passed`；目标 Python 编译和差异检查通过。
- 真实性边界：仅隔离 SQLite、模拟子进程和本地文件；真实 MySQL 双进程、systemd、共享存储、GPU、真实车牌训练节点、TPU-MLIR、BM1688、S3、AICM 和生产浏览器未验收。车牌独立 Worker 继续 fail-closed。

### RUN-0102 真实 MySQL 双进程与锁等待安全门禁（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0102_mysql_dual_process_gate.md`。
- 交付角色：R03 架构主责，R09 测试验收，R10 运维协作，R04 后端复核；新增 `rehearse_mysql_execution_fencing.py` 和对应契约测试。
- 本轮交付：默认只读 MySQL 能力探针；仅本机 `labelu_migration_sandbox*` 库、显式 `--模式 演练 --允许修改` 和 `LABELU_MYSQL_REHEARSAL_CONFIRM=YES` 才能创建/清理本次运行独有的临时 InnoDB 表；双进程 A/B/C 入口覆盖行锁等待、唯一领取、旧 token 拒绝和新 token 接管；无 URL/不安全 URL/无凭据均失败关闭。
- 本地证据：`python -m pytest labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py -q` 为 `5 passed`；Python 编译和差异检查通过。无 URL 返回“阻断”，本机沙箱探针因无有效凭据返回 MySQL `1045`；没有真实双进程通过证据。
- 数据/API/页面影响：不新增菜单、用户角色、项目维度、正式迁移、API 或页面；写入仅限显式沙箱数据库，演练表最终清理。
- 并发/风险：SQLite 结果不替代 MySQL 锁等待；真实同版本 MySQL、20 并发、执行计划、备份恢复仍未完成。训练/编译/边端/远程 Worker 继续 fail-closed。
- 提交/部署：未提交、未推送、未部署，等待用户明确“提交代码”。L0-L1 为通过，MySQL L2/L3/L4 未完成。

### RUN-0100 训练子进程兼容回归与并发测试稳定性（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0100_training_regression_stability.md`。
- 交付角色：R04 后端主责，R09 测试协作，R03 架构复核；未新增菜单、用户角色、项目维度、数据库字段或生产开关。
- 核心证据：无 fencing 的 YOLO/车牌子进程阶段事件保持历史结构；两个兼容回归测试 `2 passed`；20 进程本地领取专项 `1 passed`，耗时 `54.08 秒`；合并训练、车牌、远程、test-val 和边端入口回归 `146 passed, 117 warnings`。等待窗口只改测试，不改变生产租约逻辑。
- 真实性边界：仅本地 Windows spawn + SQLite；真实 MySQL 锁等待、双进程接管、GPU、systemd、S3、AICM、TPU-MLIR 和 BM1688 未验收。

### RUN-0099 本地 YOLO 阶段文件执行世代隔离（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0099_local_phase_generation.md`。
- 交付角色：R04 后端主责，R06 模型算法、R09 测试协作；未新增菜单、用户角色、项目维度或数据库字段。
- 核心证据：`phase.json` 写入按 owner/token/未过期租约确认，旧进程迟到写入拒绝；阶段与摘要专项 `10 passed`；真实 MySQL、systemd、GPU/共享存储和车牌训练剩余生命周期仍未验收。

### RUN-0098 远程训练制品 HMAC 签名校验（本地 L1）

- 独立记录：`labelu/docs/7gai_run_0098_remote_hmac.md`。
- 交付角色：R03 架构、R04 后端主责，R06 模型算法、R09 测试协作；未新增菜单、用户角色、项目维度或数据库字段。
- 核心证据：平台和 Agent 对 `job_spec.json`、阶段/结果回传及 `best.pt` 旁车文件验签；`APP_ENV=prod/production` 缺密钥拒绝派发并将作业置为失败；HMAC专项 `6 passed`，既有远程专项 `27 passed`；Secret 管理器、轮换、真实 MySQL、节点重启/掉电和真实远程 GPU仍未验收。

### RUN-0097 远程训练制品内容摘要校验（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`REMOTE-ARTIFACT-DIGEST-01`。
- 工作包/角色：WP2 训练与执行器；R04 后端主责，R03 架构、R06 模型算法、R09 测试协作。
- 本轮目标：为远程操作世代增加 `job_spec.json` SHA256 摘要，阻止截断、错包和旧制品目录被误读。
- 实际代码变化：平台生成并记录摘要；Agent 启动参数、`phase.json`、`agent_result.json` 和失败回传带摘要；平台轮询和 `best.pt` 导入同时校验世代与摘要。未新增菜单、角色、数据库字段或项目维度。
- 权限/并发/失败影响：权限不变；摘要不一致时忽略回传或失败关闭，不写入当前作业；仍复用 owner/token 条件更新。
- 测试命令及结果：`LABELU_TEST_DB_NAME=run0097-digest` Agent/远程轮询/下载专项 `27 passed, 112 warnings`；最终合并训练/车牌/test-val/汇总回归 `LABELU_TEST_DB_NAME=run0097-regression` 为 `98 passed, 117 warnings`；两个 Python 文件 `py_compile` 通过；目标文件 `git diff --check` 通过。
- 本地、预生产、生产证据：仅隔离 SQLite、模拟节点和本地 Agent 脚本；未连接生产 MySQL、远程 GPU、systemd、S3、AICM、TPU-MLIR 或 BM1688；未提交、未推送、未部署。
- 风险与下一依赖：SHA256 是内容一致性摘要，不是带密钥的防篡改签名；HMAC/签名密钥、轮换、真实节点重启、断点续传、双进程接管和 GPU 释放仍未验收。
- L0-L4 结论：远程内容摘要达到本地 L1；生产防篡改和远程执行器仍未完成。
- 独立记录：`labelu/docs/7gai_run_0097_remote_artifact_digest.md`。

### RUN-0096 远程训练操作世代与回传制品隔离（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`REMOTE-TRAIN-FENCE-01`、`REMOTE-ARTIFACT-GENERATION-01`。
- 工作包/角色：WP2 训练与执行器；R04 后端主责，R03 架构、R06 模型算法、R09 测试协作。
- 本轮目标：避免远程目录按 `job-{id}` 复用时，旧 `phase.json`、`agent_result.json` 或权重被新训练轮次误读。
- 实际代码变化：每次派发使用 `owner:fencing_token` 生成操作世代；写入 `job_spec.json`、平台远程状态、节点阶段文件和 Agent 回传；轮询和制品导入先校验当前世代，旧/缺失世代直接丢弃。未新增菜单、角色、数据库字段或项目维度。
- 权限/并发/失败影响：权限不变；旧节点只能被忽略，不能覆盖当前训练状态或完成结果；世代不匹配时保持重试，不把旧文件标记为失败或完成。
- 测试命令及结果：`LABELU_TEST_DB_NAME=run0096-final2` Agent/远程轮询/下载专项 `26 passed, 112 warnings`；两个 Python 文件 `py_compile` 通过；目标文件 `git diff --check` 待文档完成后复核。
- 本地、预生产、生产证据：仅隔离 SQLite、模拟节点和本地 Agent 脚本；未连接生产 MySQL、真实远程 GPU、systemd、S3、AICM、TPU-MLIR 或 BM1688；未提交、未推送、未部署。
- 风险与下一依赖：当前世代由数据库 token 派生但还没有内容签名/防篡改校验；真实节点重启、断点续传、双进程接管、GPU 释放和跨主机文件安全仍未验收。
- L0-L4 结论：远程文件世代隔离达到本地 L1；真实远程执行器和生产放行仍未完成。
- 独立记录：`labelu/docs/7gai_run_0096_remote_operation_generation.md`。

### RUN-0095 远程训练状态与制品导入 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`CONCURRENCY-EXEC-02`、`REMOTE-TRAIN-FENCE-01`。
- 工作包/角色：WP2 训练与执行器；R04 后端主责，R03 架构、R06 模型算法、R09 测试协作。
- 本轮目标：保护远程 YOLO 训练派发、数据准备、上传/下载进度、轮询、失败收尾和 `best.pt` 导入，阻止旧远程线程或迟到轮询覆盖接管后的作业。
- 实际代码变化：`remote_yolo_train.py` 复用 `YoloTrainJob` 执行期 owner/过期时间/fencing token；派发前领取租约；状态、统计和错误信息使用 owner/token/未过期条件更新；远程运行期间由轮询器和上传回调续租；完成/失败释放租约并停止轮询；最终 YOLO 收尾透传 owner/token。未新增菜单、角色、数据库字段或迁移。
- 权限/并发/失败影响：两类系统用户权限不变；租约不可用、被占用或失效时失败关闭，旧 token 只能停止并被拒绝，不能覆盖当前执行器；继续复用现有独立执行器 fail-closed 边界。
- 测试命令及结果：`LABELU_TEST_DB_NAME=run0095-remote2` 远程训练/下载专项 `19 passed, 112 warnings`；`LABELU_TEST_DB_NAME=run0095-train` 训练队列/车牌/test-val/汇总专项 `71 passed, 117 warnings`；最终合并专项 `LABELU_TEST_DB_NAME=run0095-final` 为 `90 passed, 117 warnings`；`python -m py_compile labelu/internal/application/service/remote_yolo_train.py` 和目标文件 `git diff --check` 通过。
- 本地、预生产、生产证据：仅隔离 SQLite 和模拟节点；未连接生产 MySQL、真实远程 GPU、TPU-MLIR、BM1688、S3、AICM 或生产训练节点；未提交、未推送、未部署。
- 风险与下一依赖：远程节点 `phase.json`/日志/制品文件的操作世代仍未签名；MySQL 锁等待、双进程接管、节点重启恢复、GPU 释放和真实长时续租仍未验收。通用租约测试有两条固定 13:00 时间用例因当前时间已过而失败，属于既有测试时间假设，不改通用逻辑。
- L0-L4 结论：远程训练数据库写入达到本地 L1；远程 GPU 和生产链路仍未完成。
- 独立记录：`labelu/docs/7gai_run_0095_remote_training_fencing.md`。

### RUN-0094 test-val 评估结果乐观并发 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`EVAL-VERSION-FENCE-01`。
- 工作包/角色：WP2 训练与执行器；R04 后端主责，R06 模型算法、R03 架构、R09 测试协作。
- 本轮目标：防止两个 test-val 评估或训练状态变更并发完成时，旧评估结果覆盖最新 `metrics_summary`。
- 实际代码变化：`crud_yolo_train_job.py` 新增“作业仍为 `COMPLETED` 且摘要未变化”的条件更新；`yolo_train.py` 写入唯一评估操作编号并丢弃过期结果；新增专项测试覆盖同一摘要只成功一次和作业状态变化后的拒绝。
- 权限/并发/失败影响：不新增用户、菜单、API 或迁移字段；条件失败只记录警告并保留当前数据库结果，避免旧评估覆盖新结果。
- 测试命令及结果：`LABELU_TEST_DB_NAME=run0094-fresh` 训练摘要/稳定性/test-val 专项 `27 passed, 114 warnings`；`LABELU_TEST_DB_NAME=run0094-full-fresh` 评估恢复/数据证据/分析/路由专项 `27 passed, 113 warnings`；相关 Python 文件编译通过，目标文件 `git diff --check` 通过。
- 本地、预生产、生产证据：仅全新隔离 SQLite；未连接生产 MySQL、GPU、TPU-MLIR、BM1688、S3、AICM 或生产训练队列；未提交、未推送、未部署。
- 未完成与下一依赖：MySQL 锁等待和双评估竞争、epoch 文件世代、远程状态和真实执行器仍未完成；下一轮验证生产同版本 MySQL 并继续纳入远程训练写入边界。
- L0-L4 结论：本轮达到本地 L1；不能据此宣称评估执行器或生产训练链路完成。
- 独立记录：`labelu/docs/7gai_run_0094_test_val_optimistic_fence.md`。

### RUN-0093 训练 Worker 中间与车牌结果 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`CONCURRENCY-EXEC-02`。
- 工作包/角色：WP2 训练与执行器；R04 后端主责，R06 模型算法、R03 架构、R07 边端、R09 测试协作。
- 本轮目标：把 owner/token 继续贯通到 YOLO 启动/取消/失败写入，以及车牌检测、车牌识别的组件状态、制品、最终状态和异常收尾，阻止失租 Worker 覆盖新 Worker。
- 实际代码变化：`yolo_train.py` 对不完整执行上下文失败关闭，并保护 `PROCESSING`、`CANCELLED`、`FAILED` 收尾；`plate_recognition_train.py` 保护组件 JSON、PT/ONNX 制品、数据集摘要、最终权重/指标/完成与失败写入；`yolo_train_queue.py` 将 owner/token 传入车牌子进程，父进程失租终止子进程并拒绝异常收尾。
- 权限/并发/失败影响：不新增用户权限；写入事务前后均校验执行租约，旧 token 只能停止或被拒绝，不能覆盖新 Worker 状态。独立 Worker 仍保持 fail-closed。
- 测试命令及结果：`python -m py_compile labelu/internal/application/service/yolo_train.py labelu/internal/application/service/plate_recognition_train.py labelu/internal/application/service/yolo_train_queue.py` 通过；训练/车牌/队列专项 `75 passed, 115 warnings`；使用全新 `LABELU_TEST_DB_NAME=run0093-fresh` 的稳定性/汇总/Worker/车牌/队列专项 `95 passed, 117 warnings`。
- 本地、预生产、生产证据：仅隔离 SQLite、模拟子进程和本地专项测试；未连接 MySQL、GPU、TPU-MLIR、BM1688、S3、AICM 或生产训练队列；未提交、未推送、未部署。
- 未完成与下一依赖：epoch 阶段文件和中间指标、test-val 并发世代、远程训练状态、多进程 MySQL 接管、重启恢复和 GPU 释放仍未完成；下一轮继续做独立操作版本校验和真实执行器验收。
- L0-L4 结论：本轮达到本地 L1；不能据此宣称训练全链路、车牌训练生产可用或独立 Worker 已上线。
- 独立记录：`labelu/docs/7gai_run_0093_training_fencing_expansion.md`。

### RUN-0092 YOLO 训练结果写入 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第8章训练中心、第11章异步执行、第15.7节并发验收；执行契约 `CONCURRENCY-EXEC-02`。
- 工作包/角色：WP2训练与执行器；R04后端主责，R06模型算法、R03架构、R09测试协作。
- 本轮目标：将独立训练 Worker 的 owner/token 传递到 YOLO 子进程，并保护数据集快照和最终权重/指标/完成状态写入。
- 实现内容：YOLO 默认运行器和子进程传递 token/owner；旧 Web 路径无上下文时保持参数兼容；数据集快照和最终 `weights_path`、`metrics_summary`、`COMPLETED` 写入增加当前租约校验；失租只停止结果写入，不覆盖新 Worker 状态。
- 测试命令及结果：Worker/队列专项 `44 passed`；训练稳定性/汇总/评估证据专项 `31 passed`；目标 Python 文件 `py_compile` 通过。
- 本地、预生产、生产证据：仅本地 SQLite/模拟子进程；未连接生产 MySQL、GPU、systemd、S3、AICM、TPU-MLIR 或 BM1688。
- 风险与遗留：中间进度、日志、评估和车牌多阶段制品尚未全部 token 化；独立 Worker 仍 fail-closed。
- 独立记录：`labelu/docs/7gai_run_0092_yolo_result_fencing.md`。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：YOLO 关键结果写入达到本地 L1；全链路 fencing、真实 MySQL/设备和生产放行未完成。
- 轮次索引：本轮为 `RUN-0092`，下一轮统一使用 `RUN-0093`；未提交、未推送、未部署。

### RUN-0091 全量回归基线与历史失败分类（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第15章测试与验收、第15.10节证据矩阵；回归基线契约 `REGRESSION-BASELINE-01`。
- 工作包/角色：WP0基线收口、WP5测试与验收；R09主责，R01产品、R03架构协作。
- 本轮目标：在 RUN-0089/0090 后重新执行全量测试，确认本轮没有引入新的专项回归，并将历史失败按退休控制面分类。
- 测试命令及结果：`python -m pytest labelu/tests -q --disable-warnings --tb=short`；`2372 passed, 47 skipped, 58 failed, 386 warnings`，约 7 分 21 秒。此前基线为 `2369 passed, 47 skipped, 60 failed`。
- 失败分类：主动学习、数据治理、旧边端控制面、第三方 Provider 和规则中心历史测试仍要求项目/业务空间或退休入口；不恢复项目维度，不把历史失败写成新功能回归。
- 本地、预生产、生产证据：仅本地全量测试；未连接生产数据库、浏览器、S3、AICM、TPU-MLIR 或 BM1688。
- 生产只读探针复核（2026-09-18）：生产 `/healthz`、`/readyz`、`/version`、`/api-docs/openapi.json` 仍为 HTTP 200 的 SPA HTML（首字符 `<`），只有 `/openapi.json` 返回真实 JSON（首字符 `{`）；未发送任何写请求或设备执行请求。
- 独立记录：`labelu/docs/7gai_run_0091_full_regression_baseline.md`。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：回归基线和失败分类达到本地L1；退休测试清单清理、生产迁移、真实设备和生产放行未完成。

### RUN-0090 训练结果 Outbox fencing 传递补齐（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第8章训练中心、第14.6.3节后台执行器与租约；训练结果一致性契约 `TRAIN-RESULT-FENCE-01`。
- 工作包/角色：WP2训练与执行器；R04后端主责，R03架构、R06模型算法、R09测试协作。
- 本轮目标：补齐 `training.worker.result.v1` 由 Outbox 消费时的 fencing 检查传递，防止旧 Worker 结果在消费阶段修改训练作业状态、指标或远端状态。
- 实现内容：`handle_outbox_event()` 将消费器的 `fencing_check` 传给 `persist_training_worker_result()`；结果消费继续沿用 owner/token 检查，旧 token 事件可被消费但不能写入训练作业；新增旧 token 结果事件不改变作业状态、指标和 `remote_status_json` 的回归断言。
- 测试命令及结果：训练结果持久化、训练 Worker runtime、YOLO Worker 专项 `29 passed, 112 warnings`；自动训练 reconciliation/evaluator/scheduler 回归 `28 passed, 112 warnings`；相关 Python 文件 `py_compile` 通过；目标文件 `git diff --check` 通过。
- 权限、并发、失败和审计：不新增登录角色、菜单或外部接口；仅收紧旧 Worker 结果写入边界；事件仍按幂等键消费，拒绝写入的旧结果保留消费审计，不覆盖新 Worker 状态。
- 本地、预生产、生产证据：仅本地隔离 SQLite/专项测试；未连接生产 MySQL、真实 GPU、AICM、S3、TPU-MLIR 或 BM1688，未执行 systemd 重启、双进程接管、真实训练或生产写入。
- 真实性限制：这是结果事件消费阶段的局部 fencing 修复，不代表 YOLO/车牌训练状态、进度、权重路径、评估结果和制品写入已全部 token 化；独立训练 Worker 继续 fail-closed。
- 下一依赖与负责人：R04/R06继续完成训练全链路 owner/token 条件写入；R03/R09补同版本 MySQL 双进程、租约过期接管、重启恢复、GPU 释放和生产快照证据；R07补真实 BM1688 证据。
- 独立记录：`labelu/docs/7gai_run_0090_training_result_outbox_fencing.md`。
- commit/push/deploy 状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4 结论：结果事件消费 fencing 达到本地 L1；训练全链路、真实执行器、MySQL、GPU、BM1688、预生产和生产放行未完成，平台整体仍不得超过本地 L1。

### RUN-0089 前端弹窗构建与类型检查收口（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第12章前端交互、第15.10节前端验收；交互契约 `UI-MODAL-01`。
- 工作包/角色：WP5前端统一体验；R08前端主责，R01产品、R09测试协作。
- 本轮目标：完成 RUN-0088 弹窗改造后的前端构建与类型检查收口，确认 `TS2589` 不再阻断本地交付。
- 实现内容：任务标签属性配置组件采用仓库既有兼容类型收窄方式处理 `useTranslation()` 深层泛型；运行时弹窗行为、接口、权限未改变。
- 测试命令及结果：前端 `type-check` 通过；前端构建通过；AICM/边端弹窗专项 `10 passed`；训练 Worker fencing 专项 `12 passed`；目标文件 `git diff --check` 通过。
- 本地、预生产、生产证据：仅本地证据；未连接生产浏览器、数据库、AICM、S3 或 BM1688。
- 风险与下一依赖：仍需桌面/窄屏真实生产入口验收；既有构建警告未扩大处理。
- 独立记录：`labelu/docs/7gai_run_0089_frontend_typecheck_and_modal_build.md`。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：前端本地构建和类型检查达到本地L1；生产浏览器真机和整体生产放行未完成。

### RUN-0088 详情抽屉统一改为居中弹窗与回归审计（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第12章前端交互、第15.10节前端可用性；交互契约`UI-MODAL-01`。
- 工作包/角色：WP5前端统一体验；R08前端主责，R01产品、R09测试协作。
- 本轮目标：清除仍在生产入口和 LabelU-Kit 任务配置组件中的右侧详情抽屉，统一为居中弹窗，并修正过时的端到端选择器。
- 实现内容：边端设备配置详情由`detailDrawerBackdrop/.drawer`改为`detailModalBackdrop/.detail-modal`；设备详情刷新、遮罩关闭、编辑跳转和窄屏布局保持原业务行为；任务标签属性配置由 Ant Design `Drawer`改为居中`Modal`；AICM和边端端到端测试同步新的弹窗选择器。
- 权限、接口和并发：不新增 API、数据库字段或权限；不改变设备租约、BM1688验证、AICM连接和任务配置逻辑；弹窗打开/关闭不增加请求。
- 测试命令及结果：`python -m pytest labelu/tests/e2e/test_aicm_publish_environments_frontend.py labelu/tests/e2e/test_edge_device_registry_frontend.py -q --disable-warnings --tb=short`，`10 passed`；生产页面和主前端源码扫描无`Drawer`、`StyledDrawer`、`drawer-backdrop`、`detailDrawer`残留。全量前端`type-check`仍在既有`useTranslation()`位置报告`TS2589`，未写成通过。
- 本地、预生产、生产证据：仅本地浏览器端到端和源码契约；未连接生产浏览器、数据库、BM1688、S3或AICM，未写生产数据。
- 风险与下一依赖：需单独定位前端全量`TS2589`类型递归并配置桌面/窄屏真实生产入口验收；历史设计稿仍有“底部抽屉”文字，但不属于生产路由。
- 独立记录：`labelu/docs/7gai_run_0088_modal_conversion_and_regression_audit.md`。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：生产入口详情弹窗契约达到本地L1；全量类型检查、生产浏览器和生产放行未完成。

### RUN-0087 生产探针与后端代理一致性只读核对（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第15章生产验收、第16章运维探针；运维契约`OPS-PROBE-PROXY-01`。
- 工作包/角色：WP0方案与基线、WP5测试与验收；R01产品、R03架构、R09测试主责，R10运维协作。
- 本轮目标：核对生产公网探针是否真正到达后端、`deploy.sh`是否已生成精确后端代理、现有测试是否覆盖“非SPA响应”和内容类型，避免将HTTP 200误写成健康或就绪通过。
- 生产只读证据：对`http://113.141.72.175:10000`执行GET；`/healthz`、`/readyz`、`/version`、`/api-docs/openapi.json`均为`200 text/html`、约2128字节、响应以SPA HTML开头，仍被前端回退接管；`/openapi.json`为`200 application/json`、约742162字节。未发送登录、写请求、发布、下载或设备执行请求。
- 本地代理核对：`deploy.sh`的`deploy_write_nginx_frontend_config()`已将`= /healthz`、`= /readyz`、`= /version`、`= /api-docs/openapi.json`和其他后端文档路径写入`proxy_pass http://127.0.0.1:${LABELU_BACKEND_PORT}`的精确规则；这只能证明脚本具备生成逻辑，不能证明生产Nginx已使用该配置。
- 测试覆盖核对：`labelu/tests/internal/adapter/routers/test_health.py`覆盖本地后端探针状态、数据库就绪失败和路由顺序；`test_openapi_docs.py`覆盖本地中文OpenAPI页面；`labelu/tests/scripts/test_staging_acceptance.py`仅用模拟客户端覆盖调用流程，未断言真实外部入口的`Content-Type`或拒绝SPA HTML；当前没有生产入口和生成Nginx配置的端到端内容类型验收。
- 实现内容：本轮只新增本记录并同步总方案、角色计划中的生产事实和未完成项；未修改业务代码、`deploy.sh`、数据库、生产Nginx配置或生产数据。
- 结论与建议：生产P0未完成项是让精确代理配置实际落地并在外部入口验证`healthz/readyz/version/api-docs/openapi.json`分别返回后端JSON/文档，而不是SPA HTML；上线前需保存Nginx生效配置、响应头、响应体前缀、状态码和时间戳证据。下一轮需在用户明确授权提交/部署后执行受控预生产/生产配置发布与只读验收，不能把配置脚本存在写成生产已修复。
- 本地、预生产、生产证据：本轮有生产只读HTTP证据和本地脚本/测试静态证据；没有修改生产，也没有完成配置发布、重载、预生产或浏览器真机验证。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：生产问题定位与本地代理契约达到本地L1；代理实际生效、预生产和生产放行未完成，平台整体仍不得超过L1。

### RUN-0086 独立训练 Worker fencing 最小切片（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章任务执行与第15.7节验收分层；执行契约`CONCURRENCY-EXEC-01`。
- 工作包/角色：WP2训练与边端执行；R04后端、R06模型算法、R07边端主责，R09测试协作。
- 本轮目标：在不切换生产Web训练消费路径的前提下，让独立训练Worker能够识别并保护当前YOLO/车牌训练子进程的运行PID，避免租约失效后子进程继续占用GPU。
- 实现内容：独立Worker默认运行器向YOLO/车牌子进程传递`runtime_update`；子进程启动后按当前owner/token条件写入`execution_runtime_pid`，条件写入失败立即终止并等待子进程；执行租约失效继续由已有回调终止训练进程；修复Python `inspect` 与 SQLAlchemy `inspect` 同名覆盖风险，分别使用显式别名；旧Web路径不传回调，`dedicated_worker_ready()`继续fail-closed。
- 测试命令及结果：`python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py labelu/tests/internal/application/service/test_yolo_train_queue.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short`，`45 passed`、零失败；目标文件`py_compile`和`git diff --check`通过。
- 权限、并发、失败和监控：不新增登录角色；运行PID只有持有执行租约的Worker可写；失租子进程被终止；旧Web训练行为保持兼容；训练状态、进度、权重路径、评估结果和车牌多阶段制品尚未全部token化，因此独立Worker仍不可用。
- 本地、预生产、生产证据：仅本地隔离测试和模拟子进程；未连接生产MySQL、GPU、BM1688、S3、AICM或真实训练节点，未切换生产执行路径。
- 下一依赖与负责人：R04/R06继续迁移训练状态、进度、`weights_path`、评估结果和制品登记的owner/token条件写入；R03/R09补真实MySQL锁等待、双进程接管、重启恢复、GPU释放和生产快照证据；在这些证据完成前不得打开独立训练Worker开关。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：子进程运行PID与失租终止达到本地L1；训练内部全链路fencing、真实GPU/数据库、预生产和生产放行未完成，平台整体仍不得超过L1。

### RUN-0085 模型发布、AICM 对账与平台下载治理（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第8章模型中心、第9章BM1688编译与真机验证、第11章权限与异步执行；证据契约 `MODEL-PUBLISH-DOWNLOAD-GOVERNANCE-01`。
- 工作包/角色：WP3模型编译与发布、WP4外部平台；R07边端算法主责，R04后端、R09测试协作。
- 本轮目标：核对标注人员发布权限、AICM模型ID与平台已发布目录/本地模型包的血缘关系、下载开关并发更新和新发布写链去项目化。
- 核对结论：AICM发布和ModelVersion发布均按任务创建者/有效协作者/系统管理员授权；发布成功回写`yolo_bm1688_export.published_model_id`，以同一AICM `model_set_id`登记`published_model_catalog`、AICM兼容别名和受控本地包，并在存在对应ModelVersion时回链`published_catalog_id`；历史缺少ModelVersion的目录记录保留但标记为兼容历史。新发布入口不使用项目参数，服务层仅保留旧历史读取兼容。
- 实现内容：下载开关更新必须携带`expected_download_enabled`，数据库条件更新检测过期值并返回409，关闭下载保留目录记录且阻断公共列表/下载；新发布路由去掉`project_id`写入口，避免继续产生项目维度数据；新增独立记录`labelu/docs/7gai_run_0085_model_publish_download_governance.md`。
- 权限、并发、失败和审计：标注人员可发布自己有任务权限且通过质量/BM1688门禁的模型；目录同步、别名和下载开关仍仅管理员；下载开关成功写结构化业务日志；AICM ID、目录ID、导出ID、训练任务和本地包摘要可追溯。
- 测试命令及结果：原发布目录/模型版本/历史项目兼容命令`32 passed, 117 warnings`；扩展执行AICM发布门禁、协作者发布、模型目录和无项目模型契约联合命令`158 passed, 122 warnings`；下载开关专项`22 passed`；目标Python文件`py_compile`和`git diff --check`通过。
- 本地、预生产、生产证据：仅本地SQLite和模拟目录/包；未连接生产MySQL、AICM、S3/MinIO或真实BM1688，未执行真实上传、下载、反向代理和并发管理员操作。
- 风险与下一依赖：需用生产只读导出核对AICM ID、导出记录、平台目录、本地包和ModelVersion回链；需在预生产/生产小流量验证大文件下载、代理超时、409并发冲突和关闭开关的实际效果。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：权限、血缘和下载开关契约达到本地L1；生产AICM/目录对账、真实下载和发布放行未完成。

### RUN-0084 BM1688 制品清单补齐实际 ONNX 导出契约（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章 BM1688 编译与真机验证；证据契约 `BM1688-EVIDENCE-ONNX-01`。
- 工作包/角色：WP3 模型编译与边端；R06 模型算法主责，R07 边端、R04 后端、R09 测试协作。
- 本轮目标：制品清单记录实际 ONNX 输入尺寸、opset、动态开关、简化开关和导出后端，并纳入稳定执行签名。
- 实现内容：`_persist_artifact_manifest()` 生成清单时合并实际导出契约字段；字段变化会改变 `execution_signature`，便于审计 PT→ONNX→三种 BModel 的输入条件。
- 数据/API/页面影响：不新增菜单、按钮、权限或接口；技术字段保留在模型制品/审计详情，普通用户页面不展示。
- 并发、失败和监控：继续复用现有执行租约条件写入和失败关闭；不改变任务领取、发布、下载控制或模型故障处置。
- 测试命令及结果：制品清单与 BM1688 编译专项 `10 passed`；目标文件 `py_compile` 通过；`git diff --check` 通过。
- 本地、预生产、生产证据：仅本地模拟清单和编译测试；未连接真实 TPU-MLIR、BM1688、BMRuntime/BMCV、固件、S3、AICM 或生产数据库。
- 风险与下一依赖：真实 Sophon 导出参数、工具链/固件摘要、三精度同批封存集、INT8 校准覆盖、真机 P95/短稳和生产小流量仍未验收。
- commit/push/deploy 状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4 结论：ONNX 导出证据可追溯达到本地 L1；真实工具链、设备和生产放行未完成。

### RUN-0083 边端租约不可用时失败关闭复用与状态回队（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章 BM1688 编译与真机验证、第11章并发与任务执行、第15.7节验收分层；执行契约 `EDGE-EXEC-FENCING-FAIL-CLOSED-01`。
- 工作包/角色：WP3 边端验证；R07 边端算法主责，R04 后端、R03 架构、R09 测试协作。
- 本轮目标：执行租约字段不可用时，边端 Worker 在 SSH 前失败关闭并安全退回队列，避免无 fencing 的设备副作用。
- 实现内容：新增 `_requeue_when_execution_fencing_unavailable()` 复用失败关闭函数；异常分支统一清理状态、设备地址和租约字段，记录 `queue_reason=execution_fencing_unavailable`，不触发 SSH、上传或远端清理。
- 同步收口样本包门禁：普通包真实 `skipped`、复制数不足或清单摘要漂移时禁止 `READY`；旧训练 `test/val` 包用 `blocking_skipped_count` 区分采样提示和阻断性跳过，阻断包不得上传或进入 SSH/BM1688。
- 调度器前门复用严格清单门禁：显式 `READY` 但清单损坏、摘要不一致或存在阻断性跳过的包，在设备分配前拒绝，不占用边端设备；无样本包历史手工任务保留兼容回队。
- 测试命令及结果：执行租约契约与真实回队状态专项 `6 passed`；与边端调度联合专项 `13 passed`；样本包与边端业务流专项 `14 passed`；样本包、调度器与边端业务流回归 `22 passed`；`py_compile` 和 `git diff --check` 通过。
- 权限、并发、失败重试、日志监控：不新增权限和菜单；失败关闭带任务编号日志；既有调度器可重新领取；设备释放避免并发误占用；不自动回滚已发布模型。
- 本地、预生产、生产证据：仅本地 SQLite、真实事务和模拟执行；未连接生产数据库、MySQL、SSH、BM1688、S3 或 AICM。
- 风险与下一依赖：正式八段迁移、MySQL 双进程接管、Ubuntu systemd、真实 BM1688 和生产小流量仍未完成；边端和独立训练 Worker 在这些证据完成前保持 fail-closed。
- commit/push/deploy 状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4 结论：本轮失败关闭与状态回队达到本地 L1，不代表生产放行。

### RUN-0082 训练 Worker 子进程租约闭环（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章任务执行与第15.7节验收分层；执行租约契约`CONCURRENCY-EXEC-01`。
- 工作包/角色：WP2训练与边端执行；R04后端、R07边端主责，R09测试协作。
- 本轮目标：降低独立训练 Worker 持有执行租约后，旧 YOLO/车牌训练子进程继续运行的风险。
- 实现内容：Worker默认运行器传递`ExecutionLeaseMonitor.ensure_current`；YOLO/车牌子进程等待改为0.5秒轮询，失租后终止并等待退出；旧Web训练路径不传租约回调，保持兼容；独立Worker总开关继续因训练内部历史状态写入未全量token化而fail-closed。
- 测试命令及结果：训练 Worker、训练队列和 fencing 契约专项`41 passed`；`py_compile`和`git diff --check`通过。
- 权限、并发、失败重试、日志监控：不新增权限；失租停止子进程并阻断终态写入；真实GPU、MySQL重启接管和systemd仍未测量。
- 本地、预生产、生产证据：仅本地SQLite/模拟子进程；未连接生产、未启动真实训练或修改生产数据。
- 风险与下一依赖：训练内部所有状态、进度、制品写入仍需统一token条件更新，正式第08段迁移和真实MySQL完成后才能开启独立Worker。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：失租终止达到本地L1；独立Worker生产放行未完成。

### RUN-0081 第三方 S3 训练导出与边端证据完整性（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第三方S3回流、训练冻结数据、BM1688验收包、并发性能；契约`DATA-S3-MATERIALIZE-01`、`EDGE-PACK-INTEGRITY-01`。
- 工作包/角色：WP2训练与数据、WP3模型编译与边端；R06模型算法主责，R04后端、R07边端、R09测试协作。
- 本轮目标：让第三方S3问题图片进入训练导出和边端验收时使用同一缓存物化能力，并阻止不完整或被篡改的验收包执行。
- 实现内容：新增统一`resolve_attachment_local_path()`，`prepare_yolo_dataset()`、自动评测校验/冻结和训练任务冻结均对`data_source_id`复用S3缓存，物化失败硬失败；边端创建入口在`skipped_count>0`或复制数不足时不写`READY`；清单新增源/打包图片SHA、标注XML SHA和整体`manifest_sha256`，执行前重新校验；制品清单新增稳定`execution_signature`和`evidence_manifest_sha256`；训练Worker增加20进程SQLite领取专项测试；测试夹具补齐边端执行租约字段，避免旧SQLite库误报缺列。
- 测试命令及结果：S3数据集/边端包/制品清单专项合计`17 passed`；训练Worker/边端/训练队列在新隔离库`run0082-edge-fresh.db`合计`50 passed`；自动评测与训练冻结专项在`run0082-auto-fresh.db`合计`28 passed`；目标文件`py_compile`和`git diff --check`通过。
- 权限、并发、失败重试、日志监控：不新增登录角色；S3物化、源文件丢失、清单摘要漂移均失败关闭；20进程只允许一个领取者；失败保留审计信息并可重试。
- 本地、预生产、生产证据：仅本地文件/模拟S3、隔离SQLite；未连接真实S3、自动评测冻结、MySQL、TPU-MLIR、BM1688或AICM。
- 风险与下一依赖：本地代码核对显示 `training_task.py` 和 `auto_training_pipeline_runtime.py` 已调用统一 `resolve_attachment_local_path()`，训练任务冻结和自动评测对 S3 缓存物化失败会硬失败；仍缺真实 S3 对象、凭证、生产缓存权限和训练/评测对账证据，不能把本地代码路径视为生产链路完成。真实 MySQL 并发、设备和生产 S3 仍未验收。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：本轮代码和隔离测试达到本地L1；生产训练、真机验收和外部平台放行未完成。

### RUN-0080 文档与轮次索引一致性核对（仅文档，本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案文档状态栏、角色交付基线第3章、交付台账第9.2节；文档治理`DOC-INDEX-01`。
- 工作包/角色：WP0方案与基线；R01产品主责，R03架构、R09测试协作。
- 本轮目标：核对总方案、角色交付计划、交付台账及独立轮次记录的`RUN-0079`/`RUN-0080`索引，避免最新状态、正式记录和“下一轮编号”互相矛盾。
- 核对结论：`RUN-0079`保留为“MySQL沙箱与生产只读边界核验”；本轮文档核对正式使用唯一的新编号`RUN-0080`。本轮之后研发记录使用`RUN-0081`起的唯一编号；不重编号历史记录。
- 实现内容：新增`labelu/docs/7gai_run_0080_document_index_audit.md`；同步主方案状态栏、角色计划当前交付段落、台账第9.2节的轮次表和最新轮次索引。未修改业务代码、数据库、生产配置、模型制品或生产数据。
- 验证命令及结果：`rg`核对`RUN-0079`/`RUN-0080`引用和正式标题；正式`RUN-*`标题无重复；三份文档中的最新轮次均为`RUN-0080`；`git diff --check`通过。
- 真实性限制：本轮只验证本地文档文本和路径引用，不新增生产、MySQL、S3/AICM、TPU-MLIR、BM1688、浏览器或并发证据；不提升业务验收等级。
- 下一依赖（按RUN-0080记录当时）：真实开发记录当时从`RUN-0081`开始；当前`RUN-0081`至`RUN-0085`已完成登记，下一轮真实开发记录应从`RUN-0086`开始。新增记录必须先检查台账唯一编号，再同步总方案和角色计划，避免再次出现索引滞后。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：文档索引一致性达到本地L1；不代表任何业务链路、迁移、设备或生产放行完成。

### RUN-0079 MySQL沙箱与生产只读边界核验（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章并发与任务执行、第15.7节验收分层；环境契约`MIG-MYSQL-READONLY-01`。
- 工作包/角色：WP2迁移与并发、WP3执行器；R03架构、R09测试、R10运维主责。
- 本轮目标：确认MySQL/容器隔离条件是否存在，并对生产入口和OpenAPI做只读核验，避免把HTTP 200或路由存在误写成生产功能通过。
- 环境结果：本机无`mysql`客户端；Docker Compose存在但Docker引擎不可连接；无法安全启动MySQL沙箱，因此`SHOW CREATE TABLE`、`EXPLAIN`、元数据锁、20并发和生产快照升级继续阻塞。
- 生产只读结果：`http://113.141.72.175:10000/openapi.json`返回HTTP 200 JSON，解析得到295条路由，包含BM1688/边端和模型相关接口；`/health`、`/tasks`虽返回200但内容为HTML入口，不能作为JSON健康或业务验收证据。未发送任何写请求、登录、发布、下载或设备执行请求。
- 改动文件：新增`labelu/docs/7gai_run_0079_mysql_and_production_readonly_boundary.md`；无业务代码、数据库、生产配置和制品变更。
- 权限、并发、失败和监控：无业务行为变化；继续使用MySQL演练安全闸门，生产可达性与功能验收明确分离。
- 测试与演练：`python labelu/scripts/rehearse_dispatch_lease_migration.py`通过（临时SQLite，候选`gai_p1_08`重复升级幂等）；`python labelu/scripts/rehearse_phase_one_migration.py`通过（临时SQLite，15租约字段/15索引候选结构）；生产只读OpenAPI解析通过。
- 真实性限制：没有MySQL沙箱、生产脱敏快照、真实Worker、真实SSH/BM1688、TPU-MLIR、S3/AICM或浏览器操作证据，不能升级L2/L3/L4。
- 下一依赖：R10提供同版本本机MySQL/预生产实例；R09执行锁等待、双进程接管、20并发、恢复和证据留档；在此之前不得把迁移或并发写成生产完成。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：环境边界和生产只读证据达到本地L1；MySQL、真实设备、外部平台和生产放行未完成，平台整体仍不得超过L1。

### RUN-0078 跨进程执行租约接管与旧Worker隔离（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章并发与任务执行、第15.7节验收分层；并发契约`EDGE-EXEC-FENCING-PROCESS-01`。
- 工作包/角色：WP3边端验证；R03架构、R04后端、R07边端算法主责，R09测试验收。
- 本轮目标：证明跨进程执行租约不是进程内锁的假象：新Worker可在旧租约过期后接管，旧Worker持有的旧token不能迟到写入。
- 实现内容：新增独立`spawn`进程测试辅助函数。进程A领取token=1并暂停；进程B在过期时间点接管并写入token=2；进程A随后按旧token写入返回`False`；最终数据库只保留进程B状态、owner和token=2。没有新增业务接口、页面、权限或迁移字段。
- 改动文件：`labelu/tests/internal/common/test_execution_fencing.py`；本轮方案和台账文档。
- 数据/API/页面影响：无菜单、按钮、HTTP API和用户权限变化；只验证现有execution lease条件更新在共享SQLite文件上的跨进程语义。
- 并发、失败和恢复：旧Worker不会覆盖接管者结果；本轮使用受控时间序列，不宣称SQLite锁模型等同MySQL。真实生产恢复仍需MySQL双进程和网络故障验证。
- 测试命令及结果：执行租约基础 + 跨进程专项`6 passed`；联合专项使用隔离库`run-0078-final-suite.db`，`91 passed`、零失败；`py_compile`和`git diff --check`待本轮文档后最终执行。
- 真实环境证据与限制：仅本地SQLite文件和两个独立Python进程；未执行MySQL锁等待、生产数据库、真实SSH/BM1688、TPU-MLIR、S3/AICM或20并发，不能升级为L2/L3/L4。
- 风险与下一依赖：必须在与生产同版本MySQL沙箱执行`SELECT FOR UPDATE`、锁等待、双进程抢占和20并发；再验证systemd重启后租约恢复，完成前不得切换独立Worker。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：跨进程token接管达到本地L1；MySQL、网络故障、真实设备和生产放行未完成，平台整体仍不得超过L1。

### RUN-0077 失租副作用前门禁与异常收尾保护（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第11章并发与任务执行、第15.7节验收分层；执行契约`EDGE-EXEC-FENCING-SIDE-EFFECT-01`。
- 工作包/角色：WP3边端验证；R03架构、R04后端、R07边端算法主责，R09测试验收。
- 本轮目标：执行租约失效后，旧Worker在发起SSH连接、回传日志和远端清理前都必须停止，不能继续产生设备副作用。
- 实现内容：在`ssh_connect`之前增加`_ensure_manual_execution_current`；异常收尾的远端日志拉取和成功清理/失败保留也增加相同检查，失租时记录`skipped_stale_lease`而不继续调用远端操作。新增源码契约断言，锁定“fencing检查在SSH连接之前”的顺序。
- 改动文件：`labelu/internal/application/service/yolo_edge_validation.py`、`labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py`、本轮方案和台账文档。
- 数据/API/页面影响：不新增页面、按钮、接口、权限或数据库字段；失租任务不覆盖接管者终态，保留既有陈旧任务恢复扫描。远端日志/清理跳过状态只写入当前执行摘要，不改变模型发布和下载控制。
- 并发、失败和恢复：租约续期失败、token被接管或过期时，SSH连接和后续收尾副作用均被阻断；恢复仍依赖既有陈旧`PROCESSING`扫描，不自动回滚模型、不自动撤回已发布制品。
- 测试命令及结果：源码契约 + 成功/失败业务流`6 passed`；联合专项使用隔离库`run-0077-final-suite.db`，`90 passed`、零失败；`py_compile`和`git diff --check`待本轮文档后最终执行。
- 真实环境证据与限制：仅本地SQLite、多Session和模拟SSH；未执行真实失租并发、MySQL锁等待、SSH/BM1688、TPU-MLIR、S3/AICM或生产数据库，不能升级验收等级。
- 风险与下一依赖：仍需在MySQL沙箱用双进程模拟租约接管，证明接管瞬间旧Worker不会连接设备；补20并发、网络中断和远端清理幂等证据。正式第08段迁移和真实设备放行前不得切换独立Worker。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：失租副作用前门禁达到本地L1；真实双进程、MySQL、设备和生产放行未完成，平台整体仍不得超过L1。

### RUN-0076 边端成功/失败终态与租约释放专项（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第11章并发与任务执行、第15.7节验收分层；异常契约`EDGE-EXEC-OUTCOME-01`。
- 工作包/角色：WP3边端验证；R03架构、R04后端、R07边端算法主责，R09测试验收。
- 本轮目标：验证真实执行租约链在远端成功和远端失败两种结果下均能提交正确终态，且不遗留设备执行租约；同时保留失租旧Worker不能写入的 fencing 语义。
- 实现内容：将`test_yolo_bm1688_edge_business_flow`参数化为远端退出码0/1两条真实业务流；成功断言`COMPLETED`和执行状态`ok`，失败断言`FAILED`和执行状态`fail`，两条路径均断言最终进度为`finalize`、owner为空、expires存在、token保留。未新增页面、按钮、接口或生产迁移。
- 改动文件：`labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py`；执行事务修复和测试兼容字段沿用RUN-0075，台账及本轮独立记录同步更新。
- 数据/API/页面影响：不改变用户权限、菜单、HTTP接口和模型发布/下载语义；异常结果保存在边端验证任务`result_json`和错误字段中，设备释放与结果回写在执行租约约束下完成。
- 并发、失败和恢复：远端异常不自动重试、不触发模型回滚；失败任务进入`FAILED`，旧worker失租时仍不覆盖新worker，交给既有陈旧任务恢复扫描。真实双进程抢占和MySQL锁等待仍未验证。
- 测试命令及结果：真实业务流参数化专项`2 passed`；联合专项使用隔离库`run-0076-final-suite.db`，`86 passed`、零失败；`py_compile`和`git diff --check`待本轮文档后最终执行。
- 真实环境证据与限制：仅本地SQLite、多Session、模拟SSH退出码；未连接真实SSH/BM1688、TPU-MLIR、S3/AICM、生产MySQL或生产任务，不能升级为L2/L3/L4。
- 风险与下一依赖：补充执行中失租后不再发起SSH/上传的端到端测试，并在MySQL沙箱做双进程接管、锁等待和20并发；正式第08段迁移和真实设备放行前不得切换独立Worker。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：成功/失败终态和租约释放达到本地L1；失租副作用阻断、MySQL、真实设备和生产放行未完成，平台整体仍不得超过L1。

### RUN-0075 边端执行期租约终态事务闭环（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第11章并发与任务执行、第15.7节验收分层；执行契约`EDGE-EXEC-FENCING-TX-01`。
- 工作包/角色：WP3边端验证；R03架构、R04后端、R07边端算法主责，R09测试协作。
- 本轮目标：移除边端完整业务流对`ExecutionFencingUnavailable`的测试降级，使用真实ORM字段验证执行租约领取、条件进度写入、成功终态提交和租约释放。
- 根因与实现：真实链复现任务长期停留`PROCESSING`。原因是领取租约后的初始写入、成功终态和失败终态虽然带owner/token条件，却没有位于显式提交事务中；`session_scope()`关闭时回滚。现将三处“进度 + 状态/结果”写入统一放入`begin_transaction(db)`，保证同一事务提交；测试夹具为旧SQLite补齐三项execution字段，但不替代正式生产迁移。
- 改动文件：`labelu/internal/application/service/yolo_edge_validation.py`、`labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py`、`labelu/tests/conftest.py`、本轮方案和台账文档。
- 数据/API/页面影响：不新增或修改页面、按钮、接口、权限和业务表；只改变边端执行记录的事务提交语义。成功后状态、结果和最终进度同时提交，`execution_lease_owner`释放为空，过期时间保留，fencing token保留递增值。测试库兼容补列仅在`LABELU_SKIP_MIGRATIONS=true`下使用。
- 并发、失败和监控：旧worker仍只能按当前owner/token写入；租约丢失继续拒绝终态覆盖。失败终态也进入显式事务，避免异常任务永久停留`PROCESSING`。未改变30秒续租、900秒租期和设备分配逻辑。
- 测试命令及结果：首次移除模拟后稳定复现`1 failed`且状态为`PROCESSING`；修复后同一真实fencing业务流`1 passed`。全新隔离库`run-0075-final-suite.db`执行execution fencing、样本包、调度、自动训练派发、边端路由和完整业务流联合专项，`76 passed`、零失败；断言完成状态、最终进度、owner释放、expires存在和token为1。
- 真实环境证据与限制：仅本地SQLite、真实SQLAlchemy多Session和模拟SSH硬件边界；未执行MySQL行锁/锁等待、双进程续租接管、真实SSH、TPU-MLIR或BM1688。不能升级为L2/L3或写成生产完成。
- 风险与下一依赖：需要增加执行中失租、SSH异常、结果导入异常三类真实Session测试，并在MySQL沙箱验证条件更新和20并发；正式第08段迁移进入生产前不能开启强制fencing。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：边端成功终态执行租约事务达到本地L1；失败接管、MySQL、真实设备和生产放行未完成，平台整体仍不得超过L1。

### RUN-0074 手工边端验证优先使用已提交样本包清单（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章 BM1688 编译与真机验证、第11章并发与任务执行、第15.7节验收分层；样本包消费契约`EDGE-PACK-MANIFEST-02`。
- 工作包/角色：WP3边端验证；R07边端算法主责，R03架构、R04后端、R09测试协作。
- 本轮目标：确保手工边端执行使用本次实际上传的不可变样本包清单，避免旧任务根目录中的历史`manifest.json`覆盖新签名包，造成类别契约、样本范围和评估结果错配。
- 实现内容：`_run_manual_job_sync`在构建边端上传包后优先读取返回的`staging/manifest.json`；仅当暂存包缺少清单时，才回退到`result_json.pack.manifest_path`，最后兼容历史任务根目录清单。构建函数原有的签名包和历史任务兼容逻辑不变。
- 权限、并发和失败处理：不改变标注人员/管理人员权限，不新增请求或数据库表；执行器仍必须通过`READY`和清单存在门禁。读取失败时不会连接SSH或上传，沿用现有失败收口和租约释放。
- 测试命令及结果：手工业务流`1 passed`；联合专项`71 passed`；`py_compile`、`git diff --check`需在本轮最终汇总再次执行。
- 真实性限制：只在隔离SQLite和本地模拟SSH边界验证；没有连接真实S3/AICM、TPU-MLIR、BM1688、生产MySQL或生产设备，不能写成生产验收通过。
- 风险与下一依赖：手工历史任务仍保留旧目录兼容路径，需在真实生产任务抽样中核对`result_json.pack.manifest_path`、staging包和边端回传类别契约一致；后续应补真实设备失败回写和包目录孤儿清理观察。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：手工样本包清单来源和本地业务链达到L1；真实BM1688、跨进程执行租约、生产设备及生产放行未完成，平台整体仍不得超过L1。

### RUN-0073 手工边端验证样本包两阶段准备与严格执行门禁（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章、第11章和第15.7节；执行契约`EDGE-PACK-2PHASE-01`。
- 工作包/角色：WP3边端验证；R07边端算法主责，R03架构、R04后端、R09测试协作。
- 本轮目标：手工创建路径也必须先完成不可变样本包准备，再允许设备执行；缺少`READY`和有效`manifest.json`时不连接SSH、不上传、不执行BM1688。
- 实现内容：手工样本包准备、签名、失败回写和严格执行门禁接入；边端可视化证据优先读取签名包目录，旧任务根目录仅作为历史兼容路径；旧业务流测试改为读取`packs/<签名>/manifest.json`。
- 测试命令及结果：手工边端可视化证据`1 passed`；legacy BM1688边端业务流`1 passed`；边端样本包、调度、自动训练与BM1688组合专项通过；本轮联合结果详见RUN-0074。
- 真实性限制：只完成本地L1，未执行真实SSH、BM1688、TPU-MLIR、生产MySQL、S3/AICM或浏览器验收。
- 下一依赖：补真实设备失败回写、执行期fencing接管和包目录孤儿清理；完成前不得把手工边端Worker写成生产切换完成。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：样本包两阶段契约达到本地L1，真实设备和生产放行未完成。

### RUN-0072 YOLO/车牌独立训练Worker安全边界与第08段租约收口（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第8章训练、第11章并发与任务执行、第15.7节验收分层；执行器契约`WORKER-CUTOVER-TRAIN-01`。
- 工作包/角色：WP4训练执行；R03架构、R04后端、R06模型算法、R09测试、R10运维协作。
- 本轮目标：在不形成第09段迁移、不让Web与独立Worker双重消费的前提下，完成YOLO检测、车牌检测、车牌识别训练领取和执行期租约的本地边界审计。
- 实现内容：删除训练执行第09段候选并将训练租约字段/索引合并到第08段；训练Worker readiness从只看ORM属性改为检查真实数据库列；领取同时过滤未过期queue/execution租约，状态、GPU分配和失败终态使用owner/token条件更新，重启根据`last.pt`决定续训。
- 安全结论：现有YOLO/车牌训练子进程仍存在直接更新`YoloTrainJob`状态的旧逻辑，外层token不能阻止失租后的子进程覆盖接管者。因此即使开关开启、数据库字段齐全，`dedicated_worker_ready()`仍强制fail-closed，Web旧路径继续消费；完成子进程全链路fencing和失租终止前不得切换。
- 变更文件：`labelu/internal/application/service/yolo_train_worker.py`、`labelu/internal/application/service/yolo_train_queue.py`、`labelu/internal/domain/models/yolo_train_job.py`、`labelu/scripts/yolo_train_worker.py`、第08段候选迁移、训练Worker专项测试及迁移演练夹具。
- 测试命令及结果：训练Worker、执行期fencing、旧队列、迁移候选和阶段演练合计`48 passed`；`py_compile`和`git diff --check`通过。
- 真实性限制：未执行真实GPU训练、暂停/继续/停止、Ubuntu systemd、MySQL锁等待、20并发、生产数据库或真实模型制品；本轮不能标记训练Worker已生产切换。
- 下一依赖：把子进程状态/终态写入绑定execution token，并在失租时终止子进程；完成真实GPU双作业、重启接管和systemd演练后再评估开关灰度。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：训练Worker领取/fencing契约达到本地L1；独立训练执行器、真实GPU和生产放行未完成，WP4及平台整体不得超过L1。

### RUN-0071 一期候选迁移生产基线闭锁与真实快照前置（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：迁移重建方案第2.2至2.3节、第4章；迁移治理`MIG-U0B-BASELINE-GUARD-01`。
- 工作包/角色：WP2/U0B数据库迁移；R03架构主责，R04后端、R09测试、R10运维协作。
- 本轮目标：阻止把空库 bootstrap 或手写最小前置表误写成生产`h7i8j9k0l1m2`结构证据；在真实脱敏快照取得前，候选迁移只允许离线审计和隔离演练。
- 实现内容：新增`phase_one_candidate_baseline_guard.py`和`check_phase_one_candidate_baseline.py`。缺少快照、快照版本不为`h7i8j9k0l1m2`、或候选声明创建快照已有表时均失败闭锁；增加`b2c3d4e5f6a7`创建`data_source`的祖先证据检查。第04段候选删除`media_asset.scene_id`及其索引，数据源密钥字段明确只承载应用层加密密文并记录`secret_ref`/轮换待对账阻塞。
- 测试命令及结果：`python -m pytest labelu/tests/alembic/test_phase_one_candidate_baseline_guard.py labelu/tests/alembic/test_phase_one_candidate_skeleton.py labelu/tests/alembic/test_phase_one_candidate_chain_rehearsal.py -q --disable-warnings --tb=short`，`11 passed`；`python labelu/scripts/rehearse_phase_one_candidate_chain.py`通过（8段、32张模拟基线前置表、0个`project_id`列、0个项目外键、逆序无候选残留）；`py_compile`和`git diff --check`待最终汇总执行。
- 真实性限制：空库入口会按当前ORM建表并stamp当前head，不能代表h7历史结构；候选链脚本仍使用手写最小前置表，未证明真实生产表可执行。已确认生产73表和祖先迁移存在`data_source`，其余表/列/索引必须等待脱敏生产快照逐项对账。
- 未完成与边界：未执行空MySQL、SHOW CREATE TABLE、字符集/默认值/行数对账、在线DDL锁等待、生产快照副本升级或生产迁移；正式`labelu/alembic_labelu/versions`未修改，未连接生产、未提交、未推送、未部署。
- 下一依赖：R03/R10取得h7生产脱敏快照并生成8段逐表差异清单；R04按差异把候选`create_table`重写为已存在表的兼容增补；R09补空MySQL和快照副本升级/降级证据。完成前U0B保持阻塞，平台不得连接生产启动。
- L0/L1/L2/L3/L4结论：基线闭锁与候选内部结构演练达到本地L1；真实基线结构、MySQL、快照和生产验收未完成，U0B及平台整体不得超过L1。

### RUN-0070 自动边端样本包事务拆分与 READY 门禁（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 工作包/角色：WP3 边端验证；R03 架构、R04 后端、R07 边端算法、R09 测试协作。
- 本轮目标：将自动边端验证拆成“样本包准备”和“设备领取/真机执行”两个已提交阶段；不新增设备 Agent 或业务表，不连接生产。
- 实现内容：自动派发使用独立 Session 原子领取 `queue_lease_*` 并先提交；样本包在事务外写入签名版本 staging 目录，完成后通过单次同文件系统 rename 发布到不可变目录；`result_json.pack` 写入 `state=READY`、`pack_signature`、manifest 路径和统计信息时使用 owner、PENDING、租约未过期条件更新。数据库提交失败不会删除旧 READY 指针，新目录只会作为待清理孤儿保留。
- 签名与兼容：签名包含导出/作业、样本/任务映射、任务配置、标注内容摘要、样本更新时间、附件路径/摘要/大小；自动任务没有显式 READY 和有效 manifest 时不可调度，手工历史任务保留已有 manifest 兼容路径。
- 调度边界：scheduler 只领取 READY 作业；`_run_manual_job_sync` 缺 READY 时释放设备分配并回到 PENDING，不连接 SSH、不上传、不执行真机副作用；自动 dispatch 不在 outbox 外层事务内 enqueue 或启动执行线程，交由已提交 READY 的轮询调度。
- 测试命令及结果：边端打包、调度、自动恢复组合 `25 passed`；自动派发提交屏障、失败残留清理、幂等重试、旧包恢复、签名变化和双进程租约竞争专项通过（当前相关筛选 `6 passed`）；`py_compile`、`git diff --check`通过。全量自动派发文件单独运行仍受共享测试库既有 schema 漂移（`task.dataset_id`、执行租约列缺失）影响，未归因于本轮逻辑。
- 未完成与边界：本轮未完成 Ubuntu/systemd、空 MySQL/锁等待、真实 BM1688/SSH、20 并发和生产灰度；旧进程在目录切换窗口崩溃后的孤儿 retired 目录仍需运维扫描回收。当前结论仅本地 L1，不得宣称生产完成。
- commit/push/deploy 状态：未提交、未推送、未部署；等待用户明确说“提交代码”。

### RUN-0068 一期去项目化空库闭包与八段候选执行演练（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第15.7节迁移验收、迁移附件第2至7章；迁移治理`MIG-U0B-CHAIN-02`。
- 工作包/角色：WP2/U0B数据库迁移；R03架构主责，R04后端、R09测试、R10运维协作。
- 本轮目标：在不修改正式`labelu/alembic_labelu/versions`、不连接生产的前提下，把隔离八段候选从全部失败关闭推进为可执行的空SQLite结构演练，验证一期白名单、去项目维度闭包和第08段Worker租约DDL。
- 实现内容：第01段从当前一期ORM白名单复制临时表结构，显式剔除所有`project_id`列和指向`project`的外键，并把BM1688/边端五个租约/fencing字段留给第08段；第08段实际增加两表共10列和10个索引。BM1688扫描索引调整为`(status,pipeline_origin,auto_training_round_id,queue_lease_expires_at,id)`以覆盖真实Worker的自动轮次非空过滤；边端保持现有调度器的`(status,queued_at,id)`，不虚构独立Edge Worker。
- 演练结果：新增`rehearse_phase_one_candidate_chain.py`，在临时SQLite依次执行8段升级、逆序降级；升级后为103张表、`project_columns=0`、`project_foreign_keys=0`，降级后无业务表残留。八段仍保持唯一单父链，父版本从生产基线`h7i8j9k0l1m2`开始，候选head为`gai_p1_08`。
- 真实性限制：第01段使用当前ORM动态复制表结构，第02至07段仍是带阻塞说明的显式空操作；该做法只用于验证空库外键闭包和第08段候选DDL，不是不可变、可审计、可上线的生产迁移。它没有替代37个旧修订的逐段重写，也没有证明现有生产表可无损升级。
- 测试命令及结果：候选骨架、全链演练、第08段租约和迁移预演四个专项共`13 passed`；候选唯一链审计、`py_compile`和`git diff --check`通过。
- MySQL与生产边界：未执行空MySQL、`SHOW CREATE TABLE`、`EXPLAIN`、元数据锁、在线DDL、生产快照副本、字符集/行数对账或恢复演练。边端索引当前只证明SQLite结构存在，MySQL列序和选择性仍需真实执行计划决定。
- 下一依赖：把第01段集中创建的103张表按既定业务归属拆回第01至07段不可变DDL；逐项重写生产基线后的37个旧迁移并处理21张源ORM项目外键；完成Task、附件、数据源、专家回流和`model_version_id`兼容字段，再进入空MySQL和脱敏生产快照演练。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：空SQLite去项目化闭包和候选执行演练达到本地L1；正式八段DDL、MySQL、生产快照与生产迁移均未完成，平台整体不得超过L1。

### RUN-0069 执行器开关一致性、BM1688资源上限与训练Worker边界审计（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章并发与任务执行、第15.8节并发性能；运维契约`WORKER-CUTOVER-OPS-02`。
- 工作包/角色：WP3 BM1688编译验证、WP4训练执行；R10运维主责，R03架构、R04后端、R07边端、R09测试协作。
- 配置风险与修复：API unit现在无论独立训练/BM1688 Worker启用或关闭，都在`EnvironmentFile`之后显式注入对应`true/false`。因此旧`.env`残留`LABELU_AUTO_TRAINING_WORKER_COMMAND_ENABLED=true`或`LABELU_BM1688_DEDICATED_WORKER_ENABLED=true`不能再造成“API停止旧消费但独立Worker未启动”的无人消费状态。
- BM1688资源边界：BM1688 Worker unit增加可配置`MemoryMax`、`CPUQuota`和`TasksMax`，默认分别为`16G`、`400%`、`1024`，并与interval、租约、批量数一起做正值/格式校验。该默认值只是防止无界占用的初始上限，不是生产容量结论；真实模型编译峰值、同机服务余量和TPU-MLIR子进程数量测量后必须再标定。
- 训练Worker真实边界：现有`training.worker.command.v1`独立Worker只承接已注册的通用容器适配器；`yolo_detection`、`plate_detection`和`plate_recognition`仍由`yolo_train_queue -> request_train_start -> _start_yolo_train_job_immediate`在Web/看门狗进程内启动。本轮没有把这一现状写成“YOLO训练已切换”，也没有用开关强制切换。
- 下一实现前置：YOLO/车牌训练独立化必须同时迁移GPU槽位领取、queue/execution租约与fencing、暂停/继续/停止、训练日志和进程回收；在这些契约完成前，训练Worker保持默认关闭，容器适配器能力与YOLO主链分开验收。
- 测试命令及结果：训练Worker运行时与自动派发专项`26 passed`；Git Bash执行`bash -n deploy.sh`、BM1688 Worker、训练Worker和scheduler三套unit契约均`OK`；`git diff --check`通过。
- 本地、预生产和生产证据：仅本地Python与shell契约；未在Ubuntu执行`systemd-analyze verify`、服务启停、journal、SIGTERM或进程死亡后接管，未执行真实YOLO训练、TPU-MLIR编译、MySQL锁等待和20并发。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：配置一致性和BM1688资源上限达到本地L1；YOLO独立训练执行器仍未实现，生产执行器放行仍未完成，平台整体不得超过L1。

### RUN-0066 BM1688 独立 Worker systemd 运维落地（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：独立 Worker 切分方案第5、7节；运维契约`WORKER-CUTOVER-OPS-01`。
- 工作包/角色：WP3 BM1688编译验证与发布；R10运维主责，R03架构、R04后端、R07边端算法、R09测试协作。
- 实现内容：`deploy.sh`新增默认关闭的`labelu-bm1688-worker.service`，仅在`LABELU_ENABLE_BM1688_WORKER=1`时生成、enable和启动；同步向API unit和Worker unit注入`LABELU_BM1688_DEDICATED_WORKER_ENABLED=true`，确保旧Web派发器停止启动自动编译线程。支持interval、派发租约秒数、每轮数量三个正数参数，并纳入启动、重启、停止、reset-clean和部署检查清单。
- unit约束：使用现有运行环境文件和独立TMPDIR；启用`NoNewPrivileges`、`PrivateTmp`、`ProtectSystem=strict`、`ProtectHome`，只开放运行目录、应用数据、TPU临时目录和TPU-MLIR目录写权限；入口固定为`python -m labelu.scripts.bm1688_worker`。
- 测试命令及结果：使用`D:\\Git\\bin\\bash.exe`执行`bash -n deploy.sh`、`test_deploy_bm1688_worker_units.sh`、既有训练Worker与调度器unit契约，三个脚本均`OK`；直接调用Windows系统`bash`因本机WSL缺少`/bin/bash`失败，已改用Git Bash完成真实shell解析和测试。
- 进程级并发证据：使用`multiprocessing`的`spawn`模式启动两个不同PID、独立engine/session的Worker，并通过跨进程屏障保证双方完成候选扫描后同时领取；无租约和过期租约场景均为`candidates=2, claimed=1, skipped=1, executed=1`且副作用标记只有一行，未过期租约场景双方均不领取、不执行并保留原owner；专项`7 passed`。该证据证明本地SQLite进程级互斥，不替代MySQL锁等待和生产执行器验收。
- 本地、预生产和生产证据：仅本地shell静态/unit生成契约；未调用`systemctl`、未写`/etc/systemd/system`、未启动Worker、未连接生产数据库/TPU-MLIR/BM1688。
- 风险和下一依赖：仍需Ubuntu预生产执行unit生成、`systemd-analyze verify`、服务启停、journal、SIGTERM长任务和重启接管验证；正式迁移与真实工具链放行前禁止打开生产开关。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。

### RUN-0065 BM1688 独立 Worker 最小运行入口与双消费者隔离（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章并发与任务执行、第15.7节验收分层；Worker迁移契约`WORKER-CUTOVER-01`。
- 工作包/角色：WP3 BM1688编译验证与发布；R07边端算法主责，R03架构、R04后端、R09测试、R10运维协作。
- 本轮目标：把 BM1688 自动编译从“只能由 Web 线程启动”切出一个可灰度的独立 Worker 入口，同时阻止独立 Worker 与 API/Outbox 旧派发器双重消费。
- 实现内容：新增 `bm1688_worker_runtime.py` 和 `scripts/bm1688_worker.py`；Worker 只扫描 `PENDING`、自动训练来源且 `pipeline_origin` 为 `AUTO_QUALITY/MANUAL_DIAGNOSTIC` 的导出，原子领取 `queue_lease_*`，提交领取事务后统一调用 `_run_bm1688_export_sync`（由该入口继续分流车牌检测/复合车牌）；成功或失败后只释放本 Worker 自己的交接租约，长任务仍由 execution fencing 保护。新增 `LABELU_BM1688_DEDICATED_WORKER_ENABLED=true` 开关；开关开启时 `dispatch_bm1688_export` 直接延迟，不领取、不启动 Web 线程。
- 测试命令及结果：BM1688 Worker、自动派发和执行期 fencing 专项通过（当前组合`18 passed`，前置完整链路`69 passed`）；覆盖默认关闭、候选范围、原子领取、领取事务先提交、统一入口、成功/失败释放租约和独立 Worker 开启时旧派发器不抢任务；`py_compile`和`git diff --check`通过。
- 并发与性能边界：仅本地 SQLite/测试会话；尚未完成两个独立进程真实竞争、MySQL锁等待、TPU-MLIR长编译、systemd服务安装、20并发和生产灰度。开关默认关闭，不能据此宣称生产已切换。
- 本地、预生产和生产证据：未连接生产数据库、TPU-MLIR、BM1688、S3或AICM；未启动 systemd Worker，未执行生产任务。
- 风险和下一依赖：R09补真实双进程和过期接管；R10补 systemd 单元、资源限制、日志和重启恢复；R03/R04完成正式八段迁移后再打开生产开关。训练 Worker和边端 Worker仍未切换，边端必须先拆样本包事务。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：BM1688独立 Worker 入口和双消费者隔离达到本地 L1；真实独立进程、工具链、设备、MySQL和生产放行未完成，平台整体仍不得超过 L1。

### RUN-0064 独立 Worker 切分审计与迁移协议（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章并发与任务执行、第15.7节验收分层；Worker迁移契约`WORKER-CUTOVER-01`。
- 工作包/角色：WP3 BM1688编译验证与发布、WP4训练执行；R03架构、R04后端、R07边端算法、R10运维协作。
- 本轮结论：训练、BM1688、边端当前均由 Web/看门狗进程内线程最终启动；BM1688可直接复用现有派发租约和 execution fencing 切出独立 Worker；边端必须先把样本包准备与执行启动拆成两个已提交事务，不能直接复制 BM1688 切法。
- 产出文档：`labelu/docs/7gai_independent_worker_cutover.md`，明确 API/Worker职责边界、三类租约不可混用、命令幂等字段、systemd服务切分和分阶段迁移顺序。
- 本轮未改业务代码、未连接生产、未启动 systemd Worker；独立 Worker实现列入下一轮，先完成只读扫描和 SQLite 双进程演练。
- 验收边界：文档审计达到 L1；尚未达到独立进程执行、20并发、空MySQL、TPU-MLIR、BM1688真机、边端SSH或生产灰度验收。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。

### RUN-0062 BM1688/边端 Worker 接入执行期 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章 BM1688 编译与真机验证、第11章并发与任务执行、第15.7节验收分层；并发契约`CONCURRENCY-EXEC-01`。
- 工作包/角色：WP3 BM1688编译验证与发布；R07边端算法主责，R03架构、R04后端、R09测试、R10运维协作。
- 本轮目标：把 RUN-0061 的执行期租约契约接入 BM1688 编译和边端验证两个真实执行函数，阻止失去 token 或租约过期的旧 worker 继续写进度、制品、结果和终态。
- 实现内容：`_run_bm1688_export_sync` 和 `_run_manual_job_sync` 在字段已迁移时领取独立 execution lease，启动 `ExecutionLeaseMonitor` 定时续租，长阶段副作用前做当前性校验，阶段进度、制品清单、边端结果和成功/失败终态改为 owner/token/未过期条件写入；租约接管或续租失败时停止后续副作用。数据库仍未完成迁移时，仅对明确的缺字段异常保留旧兼容路径；锁等待、连接错误等异常不再被当成兼容。条件写入支持复用业务当前事务，避免监控器另开会话造成锁竞争。
- 修改文件清单：`labelu/internal/common/execution_fencing.py`、`labelu/internal/application/service/yolo_bm1688_export.py`、`labelu/internal/application/service/yolo_edge_validation.py`、`labelu/tests/internal/common/test_execution_fencing.py`及 BM1688 进度回写契约测试。
- 测试命令及结果：执行期 fencing、Worker fencing、BM1688 自动恢复、边端自动恢复和调度专项组合为`51 passed`；另行运行训练/BM1688/边端集成组合时发现共享工作树已有迁移字段/旧项目夹具导致`21 failed`，未归因于本轮事务修复，已保留为既有迁移阻塞；`python -m py_compile`通过；`git diff --check`通过。
- 并发与性能边界：本地测试覆盖兼容路径、执行期字段存在时的条件接入和静态保护；尚未覆盖真实双进程接管、MySQL 锁等待、长时间 TPU-MLIR/BM1688 编译、SSH 断线恢复、20 并发副作用次数和真实设备。
- 本地、预生产和生产证据：仅隔离 SQLite/本地单元测试；正式八段迁移、生产数据库字段、TPU-MLIR、BM1688、边端 SSH、S3、AICM 均未连接，未写生产任务或模型制品。
- 风险和下一依赖：R03/R10必须先把候选 execution 字段合入唯一八段迁移并完成空 MySQL/生产快照副本演练；R09补双进程、租约续期失败、断线接管和20并发证据；迁移未放行前不能把本地 fencing 结果写成生产独占保证。
- LabelU应用版本恢复办法：本轮未写生产数据、设备配置或已发布模型；若验证失败，先停用执行期 fencing 开关/回到兼容路径并修复本地代码，不涉及外部平台模型召回。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：通用、边端及车牌专用 BM1688 Worker 的本地 fencing 接入达到 L1；正式迁移、真实工具链/设备、跨进程并发和生产验收未完成，平台整体仍不得超过 L1。

### RUN-0061 BM1688/边端执行期租约与 fencing 契约（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章并发与任务执行、第15.7节验收分层；并发契约`CONCURRENCY-EXEC-01`。
- 工作包/角色：WP3 BM1688编译验证与发布；R07边端算法主责，R03架构、R04后端、R09测试、R10迁移协作。
- 本轮目标：为 BM1688 导出和边端验证补齐独立于 `queue_lease_*` 的执行期 owner、到期时间和递增 fencing token，形成可复用的原子领取、续租、条件写入和释放契约。
- 明确不做：不接入生产数据库、第三方 S3/AICM、TPU-MLIR 或真实 BM1688；不复制候选迁移到正式 Alembic 目录；本轮不提交、不推送、不部署。
- 实现内容：新增`labelu/internal/common/execution_fencing.py`，提供执行期租约领取、续租、当前性校验、带 owner/token/未过期条件更新和安全释放；接管时 token 递增，旧 worker 的续租和终态写入返回失败。`YoloBm1688Export`和`YoloEdgeValidationJob`新增`execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token`三字段，继续与调度交接租约和边端设备分配租约分离。
- 候选迁移：扩展`labelu/docs/7gai_phase_one_migration_candidates/gai_p1_08_add_dispatch_leases.py`，为两张目标表增加执行期三字段和两个索引；升级/降级保持幂等，仅位于候选目录。`labelu/scripts/rehearse_dispatch_lease_migration.py`同步检查执行期字段和索引。
- 修改文件清单：`labelu/internal/common/execution_fencing.py`、`labelu/internal/domain/models/yolo_bm1688_export.py`、`labelu/internal/domain/models/yolo_edge_validation_job.py`、`labelu/docs/7gai_phase_one_migration_candidates/gai_p1_08_add_dispatch_leases.py`、`labelu/scripts/rehearse_dispatch_lease_migration.py`、`labelu/tests/internal/common/test_execution_fencing.py`。
- 测试命令及结果：`python -m pytest labelu/tests/internal/common/test_execution_fencing.py labelu/tests/alembic/test_dispatch_lease_migration_candidate.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py -q --disable-warnings --tb=short`，`11 passed`、零失败；`python labelu/scripts/rehearse_phase_one_migration.py`隔离 SQLite 演练通过，报告为两张表共10个租约字段（每表5个）和8个索引（每表4个）；相关模块`py_compile`及`git diff --check`通过。
- 并发与性能边界：SQLite隔离库覆盖同一租约竞争、到期接管、token递增、旧 worker 拒绝写入、续租和释放；尚未覆盖双进程真实竞争、MySQL锁等待/执行计划、20并发、长任务定时续租线程及真实副作用调用次数。
- 服务接入状态：本轮只落地通用契约、ORM字段和候选DDL，尚未把`_run_bm1688_export_sync`与`_run_manual_job_sync`的启动、心跳、制品落盘和终态写入全部改为条件更新；该接入列为下一轮RUN-0062，迁移字段未进入生产前不得开启强制 fencing。
- 本地、预生产和生产证据：仅本地隔离 SQLite 和单元测试；未执行正式迁移、空 MySQL、生产快照副本、TPU-MLIR、BM1688、边端 SSH、S3 或 AICM。
- 风险和下一依赖：R03/R10需将字段合入唯一八段迁移并完成空库/生产快照演练；R07/R04下一轮接入两个 worker 的执行期 claim/renew/conditional update，旧 worker 失效时必须停止编译、上传和结果导入；R09补双进程、过期 worker 和20并发证据。
- LabelU应用版本恢复办法：本轮未写生产数据、设备配置或已发布模型，无需恢复；不能以模型回滚替代故障处置，发布问题仍按人工关闭后续下载并发布替代版本。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：通用执行期 fencing 契约和隔离迁移候选达到本地 L1；两个实际 worker 接入、正式迁移、真实工具链/设备、并发压测和生产验收未完成，平台整体仍不得超过 L1。

## 1. 文档目的

本台账记录《7GAI进化平台菜单级产品与业务闭环方案》的实际研发、联调、测试、提交、部署和生产验收过程。主方案定义“要建设什么”，本台账回答“由谁建设、改了什么、验证到什么程度、还有什么没有完成”。

任何页面可见、接口存在、单元测试通过或本地模拟成功，都不能单独记为生产完成。每轮产出必须同时记录角色、工作包、需求编号、变更文件、数据/API/页面影响、测试证据、真实环境证据、风险、遗留项、下一依赖和提交部署状态。

系统使用人员仍只有“标注人员”和“管理人员”。下列角色全部是研发交付职责，不是系统中的新用户角色。

## 2. 需求真源和冲突处理

1. 产品与业务真源：`docs/系统方案文档/7GAI进化平台菜单级产品与业务闭环方案.md`。
2. 工程约束真源：仓库根目录 `AGENTS.md`。
3. 实际状态真源：当前代码、数据库迁移、测试、运行配置、真实页面/API、真实S3/AICM和真实BM1688证据。
4. `.planning/7gai-platform-button-acceptance.md` 是旧方向的本地验收记录，其中项目、部署中心、设备Agent、OTA和模型回滚内容与当前主方案冲突，只能作为历史差异输入，不能继续作为开发需求。
5. 发生冲突时，先停止对应模块写入，记录冲突，再由产品负责人和架构负责人按主方案作出唯一决定；禁止前端、后端或算法人员各自保留一套解释。

## 3. 十个责任角色

| 编号 | 角色 | 核心责任 | 主要验收责任 |
|---|---|---|---|
| R01 | 产品与业务负责人 | 冻结菜单、页面、按钮、弹窗、角色权限、状态和一期边界 | 业务口径唯一，标注人员与管理人员流程可执行 |
| R02 | 用户体验与界面设计 | 中文界面、居中弹窗、表格密度、等待/失败/空状态、桌面和窄屏 | 无抽屉、无遮挡、无双滚动，交互状态齐全 |
| R03 | 技术架构负责人 | 核心对象、状态机、接口契约、单一真源、执行器、迁移和跨域约束 | 全链可追踪，无第二套项目/发布/回滚状态机 |
| R04 | 平台后端工程师 | 任务、协作权限、标签契约、数据版本、训练/模型记录、目录和下载权限 | 五组权限矩阵、幂等、历史兼容和新链无项目依赖 |
| R05 | 集成与自动化后端工程师 | S3、AICM、Outbox、回调、回流任务、自动计划和异常任务 | 真实数据包、精确模型对齐、重复通知不重复消费 |
| R06 | 模型算法工程师 | YOLO11、预标注、数据划分、防泄漏、训练、指标和模型门禁 | 真实训练可复现，指标和规则证据完整 |
| R07 | BM1688边端算法工程师 | PT到ONNX、TPU-MLIR、三种BModel、校准、真机验证和运行包 | 三种制品和真实BM1688同批验证通过 |
| R08 | 前端工程师 | 六个菜单、按钮、弹窗、权限状态、进度、日志和跨菜单跳转 | 页面调用真实API，权限和异常状态与后端一致 |
| R09 | 测试与质量工程师 | 功能、权限、故障、性能、安全、迁移、开源兼容和真实设备测试 | 独立测试证据，P0/P1问题清零 |
| R10 | 运维数据库与安全工程师 | systemd执行器、Nginx、迁移、备份恢复、监控、密钥、制品和依赖治理 | 可部署、可监控、可恢复、无敏感配置泄漏 |

## 4. 六个开发工作包

| 工作包 | 名称 | 主责角色 | 协作角色 | 完成边界 |
|---|---|---|---|---|
| WP0 | 基线、架构与迁移 | R03、R10 | R01、R09 | 四层基线、脏工作树归属、单一迁移链、执行器边界和旧对象隔离完成 |
| WP1 | 任务、标注与数据版本 | R04、R08 | R01、R02、R09 | 任务权限、标签契约、预标注、人工确认和DatasetVersion完成 |
| WP2 | 训练与模型管理 | R06、R04 | R08、R09 | 数据防泄漏、训练执行器、PT、ModelVersion和训练/泛化门禁完成 |
| WP3 | BM1688编译验证与发布 | R07 | R04、R08、R10、R09 | ONNX、三种BModel、真机证据、AICM发布和下载控制完成 |
| WP4 | S3回流与自动进化 | R05 | R06、R04、R08、R09 | 真实S3首轮闭环完成；二期满足放行证据后才开放长期自动计划 |
| WP5 | 系统管理与生产放行 | R09、R10 | R01、R03及各模块主责 | 回归、迁移、压测、安全、恢复、监控和生产小流量验收完成 |

固定业务依赖链为：

```text
Task
-> DatasetVersion
-> TrainingJob
-> ModelVersion
-> ONNX/FP32/FP16/INT8 BModel
-> BM1688真机证据
-> AICM发布记录/已发布模型目录
-> 第三方S3问题数据回流
```

任何工作包都不得复制这条链中的业务对象或自行创建第二套状态真源。

## 5. 验收等级

| 等级 | 含义 | 最低证据 |
|---|---|---|
| L0 | 需求和契约冻结 | 菜单、按钮、弹窗、API、权限、状态、数据结构和验收用例无歧义 |
| L1 | 本地实现完成 | 代码、专项测试、差异归属、无越权和无重复写证据 |
| L2 | 跨角色联调完成 | 前后端、算法、执行器、失败重试、幂等、并发和重启恢复证据 |
| L3 | 预生产真实链路完成 | 生产脱敏数据及适用的真实S3、AICM、BM1688证据；模拟结果不能替代 |
| L4 | 生产验收完成 | 小流量、监控、容量、备份恢复、审计和业务对象对账通过 |

每个角色负责的工作至少达到L3才可标记“角色验收通过”；整个平台只有WP5达到L4后才能标记“方案完成”。

## 6. 文件所有权和并行规则

1. 允许并行开展只读分析、测试设计和文档审查。
2. 代码编辑按文件所有权串行处理；同一个文件同一时间只能有一个主责角色修改。
3. `labelu/main.py`、`labelu/internal/adapter/routers/__init__.py`、`labelu/internal/domain/models/__init__.py`、`labelu/internal/common/config.py`、`labelu/tests/conftest.py`、前端 `routes.tsx` 和迁移head由R03统一修改或明确授权。
4. 不创建多个长期本地版本，不使用stash隐藏产出，不重置或覆盖现有未提交修改。
5. 每轮开始和结束都记录基线commit、工作树摘要和目标文件清单。发现其他来源的新变化时立即停写并重新核对。
6. 未经用户明确说“提交代码”，不得commit、push或部署；本地代码可以在验证后暂时领先。
7. `.env*`、生产配置、密钥和连接信息必须单独审查，禁止进入交付记录正文或提交内容。

## 7. 当前阶段0基线

记录时间：2026-09-17，时区：Asia/Shanghai。

| 项目 | 当前事实 | 处理决定 |
|---|---|---|
| 分支和提交 | `codex/local-snapshot-20260710`，HEAD `146610b38` | 作为本轮文件归属盘点起点，不代表干净发布基线 |
| 工作树 | 默认Git状态为493项：202项已跟踪修改、1项已跟踪删除、290项未跟踪目录/文件；展开未跟踪目录后实际登记637个文件路径 | WP0先建立文件级归属，完成前不开展多人并行代码修改 |
| 热点文件 | main、路由注册、领域模型注册、配置、测试夹具、前端路由均有本地变化 | 由R03独占整合，其他角色只提交分析或领域建议 |
| 项目/发布残留 | project、deployment、edge_agent路由和对象仍存在，大量模型含`project_id` | 历史只读迁移；新写入口、OTA、设备发布和回滚按主方案隔离 |
| Web内重任务 | 启动期和服务内仍存在训练、编译、验证、自动训练、预标注线程/定时器 | 依次迁移到训练、编译、边端验证和通用集成执行器 |
| 旧本地验收文档 | 仍包含项目、部署中心、Agent、OTA和回滚目标 | 标记为历史参考，不作为当前验收依据 |
| 已跟踪删除文档 | 专家回流联调手册处于删除状态 | 未确认归属前不恢复、不继续删除、不纳入本轮产出 |

### 7.1 生产迁移链审计结论

本轮只读取迁移文件和生产迁移版本，没有执行升级、降级或结构写入。

| 审计项 | 实际结果 | 工程决定 |
|---|---|---|
| 生产当前版本 | `h7i8j9k0l1m2` | 作为新迁移后缀的唯一生产起点 |
| 本地代码head | `z4a5b6c7d8e9` | 当前不能直接作为生产升级目标 |
| 迁移图 | 130个唯一revision、1个base、1个head、父链完整 | 图本身没有分叉或断链，但不代表业务内容可上线 |
| 生产之后的本地迁移 | 共37个，均为未跟踪文件且生产未执行 | 不增加第38个“清理迁移”；从生产版本重建这37个迁移表达的有效能力 |
| 必须排除 | 项目/项目成员、设备发布控制面、设备Agent、OTA、组织/场景层级、设备规则发布和签名 | 对应新表、新字段、新路由和写入口不进入目标版本 |
| 保留但重写 | 数据集与数据版本、模型版本与制品/质量证据、工作流/Outbox/租约、专家回流难例、标注复核、主动学习、供应方/导入、数据标签契约 | 全部去掉`project_id`依赖，并复用现有Task、用户权限和模型发布链 |

在迁移后缀重建、空库升级测试、生产快照升级演练和回退演练完成前，当前本地应用禁止连接生产库启动，禁止部署。

## 8. 第一批实施单元

| 单元 | 内容 | 主责 | 状态 | 验收条件 |
|---|---|---|---|---|
| U0A | 637个实际文件路径归属：保留、补齐、隔离、废弃、生成物 | R03、R09 | 进行中：自动登记及迁移/冲突大类复核完成，待逐组最终复核 | 每个文件有工作包、来源、冲突和最终处置结论 |
| U0B | 冻结共享热点、单一迁移head和环境配置审查 | R03、R10 | 进行中：迁移图与生产版本审计完成；U0B-01数据/标签去项目化只读审计完成，确认ORM、迁移、训练链和前端仍处于不一致中间态 | 共享文件所有权明确，迁移无分叉，敏感值不进入提交 |
| U0C | 一期自动训练计划硬封锁 | R05、R04、R08、R09 | 本地实现达到L1：共享训练执行器、训练后恢复和质量证据恢复旁路已封锁；待跨执行器、重启和真实环境验收 | 写按钮隐藏；写/扫描/触发接口403；历史只读；数据库和队列零新增；手工链不受影响 |
| U0D | 项目、设备发布、OTA和回滚写入口下线 | R03、R04、R08、R09 | 本地实现已达到L1：禁用能力已在路由和服务入口双层门禁；生产已有板端主动拉取验证通道按RUN-0012恢复并与SSH统一为真机验证适配器；前端收口为六菜单、两角色、无项目入口和居中弹窗；新业务去项目依赖及真实环境验收待完成 | OpenAPI不暴露禁用写接口；直接调用无数据变化；SSH和板端主动拉取只产生真机质量证据；历史可只读查询 |
| U0E | 四类独立执行器和租约契约 | R03、R10及领域主责 | 待开始 | Web不执行重任务；重启不丢不重；编译节点和设备租约隔离 |

U0A和U0B完成前，不启动依赖新表或改变迁移链的业务代码编辑。为阻止当前冲突代码误写生产，可先实施不依赖新表的安全封锁单元（当前为U0C），但必须独立测试并保持不启动、不部署。

## 9. 每轮产出记录模板

### RUN-XXXX 轮次标题

- 日期和时区：
- 方案章节与需求编号：
- 工作包/实施单元：
- 主责角色/协作角色/验收角色：
- 基线commit与工作树摘要：
- 本轮目标：
- 明确不做：
- 输入和前置依赖：
- 菜单、页面、按钮和弹窗变化：
- API、权限和错误码变化：
- 数据表、迁移和制品变化：
- 状态机、幂等键、租约和并发限制：
- 修改文件清单：
- 测试命令及通过/失败/跳过数量：
- 本地、预生产和生产证据：
- 真实性限制：
- 性能数据：
- 风险和遗留项：
- 下一依赖与负责人：
- LabelU应用版本恢复办法：
- 文档同步位置：
- commit/push/deploy状态：
- L0/L1/L2/L3/L4结论：

### 9.1 本轮团队开发记录补充模板

并行开发时，除上面的通用字段外，每条记录还必须补充以下三项，避免把多个智能体或多个工作包的结果混写：

```text
- 记录编号：RUN-XXXX（必须唯一；并行完成的记录按实际写入时间顺序分配，不复用已使用编号）
- 参与人员/智能体：主责、协作、复核；说明是否只读分析
- 文件所有权确认：本轮可写文件、发现的其他来源变化、是否发生冲突及处理方式
- 证据原文：命令、环境变量/隔离库、通过/失败/跳过数量、日志或页面路径
- 当前未完成项：明确“未完成”、阻断原因、负责人、下一步和放行条件
```

记录只允许陈述已经执行的动作和证据；计划、推测、模拟器结果和页面存在性必须单独标注为“目标”或“本地 L1”，不得写入生产完成结论。

### 9.2 2026-09-18 台账一致性核对与当前未完成项

本轮只修改交付文档，未修改业务代码、数据库、生产配置或制品。核对结果如下：

| 核对项 | 当前事实 | 处理结果 |
|---|---|---|
| 基线清单数量 | `labelu/docs/7gai_baseline_file_inventory.csv` 共 638 行，其中 1 行表头、637 条文件记录；状态为 202 个修改、1 个删除、434 个未跟踪 | 统一正文使用“637 条文件记录（638 行含表头）”，不再把行数和文件数混用 |
| 轮次编号 | 并行追加曾造成轮次重复 | 迁移预演保留为 `RUN-0057`；执行并发审计至通用 Worker 接入使用 `RUN-0058` 至 `RUN-0062`；八段候选链为 `RUN-0063`；独立 Worker 切分审计为`RUN-0064`；BM1688 独立 Worker 最小入口为`RUN-0065`；跨进程与 systemd 验收为`RUN-0066`；第08段Worker迁移审计为`RUN-0067`；空SQLite去项目化闭包演练为`RUN-0068`；执行器配置与训练边界审计为`RUN-0069`；自动边端样本包为`RUN-0070`；迁移基线闭锁为`RUN-0071`；训练Worker安全边界为`RUN-0072`；手工边端两阶段样本包为`RUN-0073`；手工清单来源收口为`RUN-0074`；边端执行租约事务闭环为`RUN-0075`；成功/失败终态专项为`RUN-0076`；失租副作用前门禁为`RUN-0077`；跨进程租约接管为`RUN-0078`；MySQL沙箱与生产只读边界核验为`RUN-0079`；文档与轮次索引一致性核对为`RUN-0080`；第三方S3训练与边端证据为`RUN-0081`；训练Worker子进程租约为`RUN-0082`；边端租约不可用时失败关闭复用与状态回队为`RUN-0083`；BM1688 制品清单实际 ONNX 导出契约为`RUN-0084`；模型发布、AICM对账、平台下载并发和新写链去项目化为`RUN-0085`；独立训练 Worker fencing 最小切片为`RUN-0086`；生产探针与后端代理一致性只读核对为`RUN-0087`；详情抽屉统一改为居中弹窗与回归审计为`RUN-0088`，后续只能从 `RUN-0089` 继续，不复用编号 |
| 角色基线 | 原文“台账已记录到 RUN-0065”滞后 | 已同步为 `RUN-0080`，并补充执行期 fencing、BM1688 独立 Worker、跨进程与 systemd 验收、第08段Worker迁移、自动/手工边端样本包、迁移基线闭锁、训练Worker安全边界、签名清单来源、终态事务、成功/失败专项、失租副作用门禁、跨进程接管和本轮文档索引核对 |
| 文档链接 | `docs/04-边端验证与BM1688/README.md` 指向主方案、角色基线和交付台账 | 已按仓库相对路径核验三条链接均存在；不复制 BM1688 规则正文 |

最新轮次索引：`RUN-0057` 迁移预演；`RUN-0058` 执行并发审计；`RUN-0059` 训练/编译/评估确认弹窗；`RUN-0060` 入口品牌中文化；`RUN-0061` 执行期 fencing 契约；`RUN-0062` 通用 Worker 接入；`RUN-0063` 八段迁移候选链；`RUN-0064` 独立 Worker 切分审计；`RUN-0065` BM1688 独立 Worker 最小入口；`RUN-0066` 跨进程与 systemd 验收；`RUN-0067` 第08段Worker迁移审计；`RUN-0068` 空SQLite去项目化闭包演练；`RUN-0069` 执行器配置与训练边界审计；`RUN-0070` 自动边端样本包；`RUN-0071` 迁移基线闭锁；`RUN-0072` 训练Worker安全边界；`RUN-0073` 手工边端两阶段样本包；`RUN-0074` 手工清单来源收口；`RUN-0075` 边端执行租约事务闭环；`RUN-0076` 成功/失败终态专项；`RUN-0077` 失租副作用前门禁；`RUN-0078` 跨进程租约接管；`RUN-0079` MySQL沙箱与生产只读边界核验；`RUN-0080` 文档与轮次索引一致性核对；`RUN-0081` 第三方S3训练与边端证据；`RUN-0082` 训练Worker子进程租约；`RUN-0083` 边端租约不可用时失败关闭复用与状态回队；`RUN-0084` BM1688 制品清单实际 ONNX 导出契约；`RUN-0085` 模型发布、AICM对账、平台下载并发和新写链去项目化；`RUN-0086` 独立训练 Worker fencing 最小切片；`RUN-0087` 生产探针与后端代理一致性只读核对；`RUN-0088` 详情抽屉统一改为居中弹窗与回归审计。历史记录按各工作包实际追加位置保留，不再移动代码证据；新记录从 `RUN-0089` 开始。

当前仍未完成：基线 637 条记录尚有 624 条待人工复核；生产后 37 个迁移不能直接上线，21 张项目外键表和八段重建链未完成生产快照演练；四类独立执行器和正式 systemd 托管未完成；真实 TPU-MLIR/BM1688、S3/AICM、浏览器真机、20 并发、备份恢复和生产小流量验收未完成。以上均保持“未完成”，不因本轮文档修正升级验收等级。

轮次更正：`RUN-0089` 为前端弹窗构建与类型检查收口，`RUN-0090` 为训练结果 Outbox fencing 传递补齐；后续从 `RUN-0091` 继续。上方历史索引保留原始审计文本，不改变各轮次独立记录。

### 9.3 RUN-0090 后续开发计划与验收边界（计划，不代表已完成）

`RUN-0090`及后续轮次按以下顺序推进；每项必须先完成前置证据，不能并行把本地L1结果写成生产可用：

| 优先级 | 工作项 | 主责/协作 | 开始前置 | 通过证据 | 未通过时的边界 |
|---|---|---|---|---|---|
| P0 | 生产探针和中文OpenAPI代理实际生效 | R10主责，R03/R09验收 | 用户明确提交/部署授权；预生产最终Nginx配置可渲染 | `nginx -t`、受控重载、`/healthz`/`/readyz`/`/version`/`/api-docs/openapi.json`返回后端JSON或文档，状态码、Content-Type、首字符、版本和时间戳留档；生产小流量只读复核 | 继续视为SPA接管，不能接入监控探针或宣布生产健康通过 |
| P0 | YOLO/车牌独立训练Worker全链路fencing | R04/R06/R07主责，R03/R09验收 | 第08段真实迁移字段和索引完成候选审计 | 状态、进度、权重路径、评估结果、车牌制品均带owner/token条件；MySQL双进程、失租终止、重启接管、GPU释放和systemd证据通过 | Worker开关保持关闭，继续由现有Web路径承载，不切生产 |
| P0 | 前端全量类型和浏览器真机 | R08主责，R09验收 | 先隔离既有`TS2589`并配置可用浏览器环境 | 目标工程类型检查无新增错误；桌面/窄屏任务、设备详情、属性配置、模型发布弹窗真实点击、焦点、滚动和关闭证据齐全 | 仅保留本地L1，不宣称“全站弹窗/生产入口已验收” |
| P0 | 八段迁移和生产基线副本 | R03主责，R09/R10协作 | 取得生产脱敏快照和同版本MySQL | 唯一head、逐表DDL、21张项目外键、行数/摘要、锁等待、回滚恢复和备份演练通过 | 不执行生产DDL；空SQLite或手写基线只能作为候选演练 |
| P1 | 真实S3/AICM/BM1688闭环与容量 | R05/R06/R07主责，R09/R10验收 | P0探针、迁移和执行器边界通过 | 真实S3包、训练、三精度编译、真机P95/短稳、AICM ID/目录/本地包对账、下载409和20并发证据 | 自动训练保持关闭；模型问题只允许禁止本平台后续下载和替代发布 |

轮次索引更正：`RUN-0091` 已记录全量回归基线（`2372 passed, 47 skipped, 58 failed`），下一轮为 `RUN-0092`；上方历史索引保留原始审计文本，不改变各轮次独立记录。

统一放行规则：`RUN-0089`只能记录实际执行的本地或受控环境证据；计划、模拟器、HTTP 200、路由存在和单元测试不能替代生产证据。任何P0未完成时，平台整体不得超过本地L1，不能提交、部署或开放新生产写链。

轮次索引更正：`RUN-0092` 已记录 YOLO 数据集快照和最终结果 fencing，`RUN-0093` 已记录训练生命周期与车牌结果 fencing；下一轮从 `RUN-0094` 开始。上方历史索引保留原始审计文本，不改变已完成轮次的独立记录。

### 9.4 RUN-0093 后续开发计划与验收边界（计划，不代表已完成）

| 优先级 | 工作项 | 主责/协作 | 必须提供的证据 | 未完成时的边界 |
|---|---|---|---|---|
| P0 | epoch 中间指标、阶段文件和 test-val 评估世代保护 | R04/R06；R09 | 独立操作编号或版本条件写入；旧进程/并发评估不能覆盖新结果；本地双 Session 和过期操作专项 | 只读文件状态，不能宣称评估结果可并发生产使用 |
| P0 | 远程训练状态和多阶段传输 fencing | R04/R06；R03/R09 | 准备、上传、轮询、失败、收敛和制品导入均带 owner/token；重试幂等 | 远程训练 Worker 保持关闭或只走旧兼容路径 |
| P0 | MySQL 双进程接管与重启恢复 | R03/R07/R09/R10 | 同版本 MySQL、脱敏生产快照、锁等待、失租终止、systemd 重启、GPU 释放 | 独立训练 Worker 继续 fail-closed，不切生产 |
| P1 | 真实 GPU、TPU-MLIR、BM1688 训练/编译/真机链路 | R06/R07；R09 | 三种 BModel、同一封存验收集、真机加载/精度/P95/短稳和失败包证据 | 只保留本地模拟和代码契约，不开放生产写链 |

RUN-0093 独立记录见 `labelu/docs/7gai_run_0093_training_fencing_expansion.md`。本轮所有证据仍为本地 L1，未提交、未推送、未部署。

轮次索引更正：`RUN-0094` 已记录 test-val 评估结果乐观并发 fencing；下一轮从 `RUN-0095` 开始。上方历史索引保留原始审计文本，不改变已完成轮次的独立记录。

### 9.5 RUN-0094 后续开发计划与验收边界（计划，不代表已完成）

| 优先级 | 工作项 | 主责/协作 | 必须提供的证据 | 未完成时的边界 |
|---|---|---|---|---|
| P0 | MySQL 条件更新和双评估竞争 | R03/R04；R09/R10 | 同版本 MySQL、执行计划、锁等待、两个真实评估并发只保留一个最新结果 | SQLite 结果只记本地 L1，不宣称生产并发通过 |
| P0 | epoch 阶段文件世代保护 | R04/R06；R09 | 阶段文件包含操作世代；旧训练进程写入被拒或隔离；重启恢复不回写旧阶段 | 训练页面继续以数据库状态为准，旧文件不能作为权威结论 |
| P1 | 远程训练状态和制品导入 fencing | R04/R06/R07；R03/R09 | 准备、上传、轮询、失败、收敛和制品导入均带 owner/token，重试幂等 | 远程训练执行器继续保持受控或关闭 |

RUN-0094 独立记录见 `labelu/docs/7gai_run_0094_test_val_optimistic_fence.md`。本轮所有证据仍为本地 L1，未提交、未推送、未部署。

轮次索引更正：`RUN-0095` 已记录远程训练状态与制品导入 fencing；下一轮继续处理远程文件世代、MySQL 双进程接管和独立 Worker 切换。上方历史索引保留原始审计文本，不改变已完成轮次的独立记录。

### 9.6 RUN-0095 后续开发计划与验收边界（计划，不代表已完成）

| 优先级 | 工作项 | 主责/协作 | 必须提供的证据 | 未完成时的边界 |
|---|---|---|---|---|
| P0 | 远程 phase/日志/制品文件世代保护 | R04/R06；R03/R09 | 每次派发生成操作世代和执行签名；旧节点文件不被新轮次读取；重试和清理幂等 | 远程文件只作为当前 token 有效时的临时输入，不能作为独立终态依据 |
| P0 | MySQL 双进程接管和远程 Worker 重启恢复 | R03/R04/R09/R10 | 同版本 MySQL、锁等待、租约续期/接管、节点重启、GPU 释放和 systemd 日志 | 远程训练保持 fail-closed，不切生产独立消费路径 |
| P1 | 真实 GPU 远程训练与混合负载 | R06/R07/R10；R09 | 长文件上传、真实训练、轮询、制品导入、标注页面 P95、容量与告警证据 | 不开放自动训练计划和生产小流量 |

RUN-0095 独立记录见 `labelu/docs/7gai_run_0095_remote_training_fencing.md`。本轮所有证据仍为本地 L1，未提交、未推送、未部署。

轮次索引更正：`RUN-0097` 已记录远程制品内容摘要校验；下一轮处理带密钥的 HMAC/签名、真实 MySQL 双进程接管和节点重启恢复。上方历史索引保留原始审计文本，不改变已完成轮次的独立记录。

### 9.7 RUN-0097 后续开发计划与验收边界（计划，不代表已完成）

| 优先级 | 工作项 | 主责/协作 | 必须提供的证据 | 未完成时的边界 |
|---|---|---|---|---|
| P0 | 远程 payload/spec/phase/result HMAC 或签名 | R03/R04；R09/R10 | 受管密钥、轮换、签名校验、篡改和重放拒绝；密钥不进入页面或日志 | SHA256 只能作为一致性校验，不能作为生产防篡改证据 |
| P0 | MySQL 双进程接管和节点重启恢复 | R03/R04/R09/R10 | 真实 MySQL 锁等待、租约续期/接管、旧 token/旧摘要拒绝、systemd 日志和 GPU 释放 | 远程训练继续 fail-closed，不切独立生产消费 |
| P1 | 真实远程 GPU 与混合负载 | R06/R07/R10；R09 | 长文件传输、训练、轮询、制品导入、重启恢复、标注页面 P95 和容量证据 | 不开放自动训练计划和生产小流量 |

RUN-0097 独立记录见 `labelu/docs/7gai_run_0097_remote_artifact_digest.md`。本轮所有证据仍为本地 L1，未提交、未推送、未部署。

## 10. 交付记录

### RUN-0001 建立角色工作包和实施台账

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案15.9、15.10；目标治理GOV-001。
- 工作包/实施单元：WP0/U0A。
- 主责角色/协作角色/验收角色：R03；R01、R09、R10；用户最终确认。
- 基线commit与工作树摘要：`146610b38`；493项现有变化。
- 本轮目标：建立10个责任角色、6个工作包、5级验收和每轮记录格式；识别并行开发风险。
- 明确不做：不修改业务代码，不修改生产数据，不提交、不推送、不部署，不认领此前493项变化为本轮成果。
- 修改文件清单：`labelu/docs/7gai_delivery_ledger.md`。
- 本地证据：主方案责任边界、当前Git分支/HEAD/工作树数量、关键路径和旧本地验收文档均已只读核对。
- 真实性限制：尚未完成493项逐文件归属，尚未执行本地功能测试或真实外部链路验证。
- 风险和遗留项：未跟踪迁移、共享热点文件、环境配置和旧方向代码可能互相冲突。
- 下一依赖与负责人：完成U0A文件级归属和U0B共享文件冻结，R03/R09/R10负责。
- LabelU应用版本恢复办法：本轮未改应用，无需恢复；不得解释为模型回滚。
- commit/push/deploy状态：未提交、未推送、未部署。
- L0/L1/L2/L3/L4结论：治理框架达到L0；业务实现未进入验收。

### RUN-0002 生成本地变化文件级基线清单

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案15.3、15.9、15.10；基线治理BASE-001。
- 工作包/实施单元：WP0/U0A。
- 主责角色/协作角色/验收角色：R03；R09、R10；R01复核冲突方向。
- 基线commit与工作树摘要：`146610b38`；默认状态493项，展开未跟踪目录后登记637个实际文件路径。
- 本轮目标：为现有变化建立可检查的文件状态、内容摘要、建议工作包、主责角色和初始处置意见。
- 明确不做：不读取或输出环境变量内容；不删除生成物；不恢复已删除文档；不批准任何冲突代码进入目标版本。
- 数据表、迁移和制品变化：没有数据库或制品写入；只登记51个迁移候选并要求串行复核。
- 修改文件清单：`labelu/docs/7gai_baseline_file_inventory.csv`、`labelu/docs/7gai_delivery_ledger.md`。
- 本地证据：637行清单均包含Git状态、路径、是否存在、大小、SHA256、工作包、角色、初始处置、原因和复核状态；其中测试资产133个、前端资产98个、迁移候选51个、现方案冲突候选39个、生成物候选106个、已删除文件1个。
- 真实性限制：分类依据路径和名称自动生成，尚未逐文件判断业务行为；“冲突候选”和“生成物候选”都不能据此自动删除。
- 风险和遗留项：未跟踪迁移、项目/部署/Agent旧方向、共享热点文件和环境配置需要优先人工复核。
- 下一依赖与负责人：按WP0冲突候选、迁移、敏感配置、生成物、WP1至WP4的顺序完成最终归属；R03/R09/R10负责。
- LabelU应用版本恢复办法：本轮未改应用，无需恢复；不得解释为模型回滚。
- commit/push/deploy状态：未提交、未推送、未部署。
- L0/L1/L2/L3/L4结论：文件登记达到L0；U0A尚未完成最终归属，不能进入业务代码并行开发。

### RUN-0003 生产迁移链审计与一期自动训练计划硬封锁

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案一期自动进化边界、中文页面与居中弹窗约束；安全治理`GATE-U0C-001`、迁移治理`MIG-U0B-001`。
- 工作包/实施单元：WP0/U0B、WP0/U0C，影响WP2、WP3和WP4的自动链路。
- 主责角色/协作角色/验收角色：R03、R05；R04、R08、R10；R09验收。
- 基线commit与工作树摘要：`146610b38`；工作树原有大量用户改动，本轮未重置、暂存、删除或恢复任何不相关文件。
- 本轮目标：确认生产与本地迁移差异；将一期自动训练计划变为历史只读；阻断HTTP、服务直调、事件、看门狗、训练队列、BM1688恢复和边端验证恢复的自动写入，同时保留手工训练、手工编译和手工验证。
- 明确不做：不重建37个迁移文件，不启动应用，不连接生产库，不执行真实训练/编译/设备验证，不修改生产数据，不提交、不推送、不部署。
- 菜单、页面、按钮和弹窗变化：React训练页页签改为“历史自动训练计划”，自动计划详情与报告详情由抽屉改为居中弹窗；旧训练页显示一期历史只读说明，隐藏自动计划、评估集、运行设置和诊断编译等写按钮，运行开关只读。
- API、权限和错误码变化：新增统一写门禁，先认证再判断功能开关；31个自动训练写端点统一返回HTTP 403和错误码`57122`，提示“自动训练计划一期暂未开放，历史记录仅供查看”；GET历史查询保留；写端点从一期OpenAPI文档隐藏，但旧地址继续返回明确403，避免绕过和客户端误用。
- 数据表、迁移和制品变化：没有执行数据库迁移或制品生成。生产版本为`h7i8j9k0l1m2`，本地head为`z3a4b5c6d7e8`；生产之后存在37个未跟踪迁移，必须从生产版本重建，不允许原样上线。
- 状态机、幂等键、租约和并发限制：功能关闭时看门狗在打开数据库前退出，任务/标注/训练就绪事件不再产生自动训练事件；自动来源训练作业不进入派发，自动训练关联的BM1688和边端验证恢复不启动线程；手工来源继续按现有GPU队列、导出互斥锁和设备调度规则运行。
- 主要修改文件：`labelu/internal/common/config.py`、`error_code.py`、`adapter/routers/auto_training.py`、`application/service/auto_training_plan.py`、`auto_training_watchdog.py`、`task.py`、`sample.py`、`task_training_ready.py`、`yolo_train_queue.py`、`yolo_bm1688_auto_compile.py`、`yolo_bm1688_export_auto_resume.py`、`yolo_edge_validation_auto_resume.py`、`frontend/extensions/pages/yolo_training.html`、React `training.tsx`及对应专项测试。
- 测试迭代记录：最初只读路由用例错误使用重复`/v1`前缀导致10项失败，修正为实际`API_V1_STR`后通过；BM1688两个旧用例使用未明确字段的`MagicMock`被识别成自动来源，改为显式手工来源并补充自动/手工成对用例；一期OpenAPI旧用例仍要求写接口可见，改为验证“历史读可见、写控制隐藏”。
- 最终测试证据：U0C后端、事件、任务、训练队列、BM1688、边端恢复和旧页面专项组`278 passed`；React训练页`28 passed`；`git diff --check`无空白错误。
- 未通过证据：React工程`pnpm run type-check`仍有1项既有错误，位于`ListAttribute.fancy/index.tsx:115`，`TS2589`类型实例化过深；该文件不属于U0C，本轮未修改。
- 本地、预生产和生产证据：只有隔离SQLite测试和前端单测证据；没有预生产或生产证据。
- 真实性限制：没有启动页面做浏览器视觉验收，没有真实数据库、队列、GPU、TPU-MLIR或BM1688运行证据，不能标记L2、L3或L4。
- 性能数据：未做吞吐压测；门禁路径通过“数据库会话前退出、查询侧过滤自动来源、禁止新线程”降低关闭状态下的并发占用。后续仍需对训练队列、导出恢复和边端设备租约进行并发压测。
- 风险和遗留项：独立审计发现共享训练Worker、YOLO自动续训、边端统一调度、质量证据恢复和部分服务直调仍可能推进历史自动作业；标注人员页签可见性和历史查询的项目成员语义也未收口。另有37个生产后迁移未重建、设备发布残留和前端全量类型检查未绿。
- 下一依赖与负责人：R04/R05先封锁共享执行器和服务直调旁路，R08修正角色可见性，R09补齐重启恢复、自动/手工成对和数据库零增长测试；通过后才能恢复L1结论。之后由R03/R10重建生产后迁移，R03/R04进入U0D。
- LabelU应用版本恢复办法：本轮没有发布版本，无生产回滚动作；如本地门禁实现需撤销，只能按本轮文件清单做代码修正，禁止使用模型“回滚”概念。
- 文档同步位置：本台账第7.1节、第8节和本记录；文件归属继续维护在`7gai_baseline_file_inventory.csv`。
- commit/push/deploy状态：未提交、未推送、未部署，等待用户明确说“提交代码”后才进入提交流程。
- L0/L1/L2/L3/L4结论：迁移风险和U0C需求矩阵达到L0；U0C专项回归虽通过，但因独立审计仍有后台旁路，暂不判定L1；跨执行器联调、真实环境和生产验收均未完成。

### RUN-0004 封锁自动训练后台旁路并保留人工训练链

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案1.9、8.11.4、15.2；安全治理`GATE-U0C-002`。
- 工作包/实施单元：WP0/U0C，影响WP2、WP3和WP4的共享执行链。
- 主责角色/协作角色/验收角色：R05、R04；R03、R06、R07；R09独立复核。
- 基线commit与工作树摘要：`146610b38`；保留工作树既有大量修改和并行成果，没有重置、暂存、删除或恢复无关文件。
- 本轮目标：补齐RUN-0003发现的共享执行器旁路，确保一期关闭自动训练计划时，任何具有自动来源、自动计划或自动轮次血缘的历史作业都不会被领取、续跑、补评估或生成下游结果，同时保持人工统一训练命令和人工训练后处理正常运行。
- 明确不做：不开放自动计划，不重建迁移链，不启动应用，不连接生产库，不执行真实GPU训练、TPU-MLIR编译或BM1688验证，不提交、不推送、不部署。
- 菜单、页面、按钮和弹窗变化：本轮没有新增界面变化，继续沿用RUN-0003的一期历史只读页和居中弹窗约束。
- API、权限和错误码变化：没有新增外部API；门禁下沉到共享Outbox领取、训练Worker执行前复核、训练完成后恢复扫描和质量证据恢复，防止绕过HTTP 403直接推进历史自动作业。
- 数据表、迁移和制品变化：没有新增表、迁移或制品；过滤掉的自动命令和结果Outbox保持原状态，不写租约、不增加尝试次数、不产生新的结果Outbox。
- 状态机、幂等键、租约和并发限制：Outbox候选查询和原子抢占更新使用同一组领取谓词，避免两个Worker在查询与更新窗口领取已被排除的自动事件；一期关闭时同时按`queue_source=AUTO`、`auto_training_plan_id`非空、`auto_training_round_id`非空识别自动血缘；Worker执行、训练后恢复和质量证据恢复在实际调用下游前再次检查，抵御查询后对象变化；人工血缘继续按原租约和幂等规则执行。
- 主要修改文件：`application/service/auto_training_outbox.py`、`training_worker_runtime.py`、`yolo_train.py`、`yolo_quality_evidence_auto_recovery.py`及对应四个专项测试文件。
- 测试命令及结果：执行`test_training_worker_runtime.py`、`test_auto_training_outbox.py`、`test_yolo_quality_evidence_auto_recovery.py`、`test_yolo_train_stability.py`，结果`47 passed`；只有既有SQLAlchemy和Pydantic弃用警告。目标文件`git diff --check`无空白错误。
- 本地、预生产和生产证据：隔离SQLite测试证明自动命令、自动结果、训练后恢复和质量证据恢复均不会推进，人工成对场景正常完成；没有预生产或生产证据。
- 真实性限制：尚未执行多进程真实Worker竞争、进程崩溃重启、数据库前后全表计数、真实训练队列或生产旁路核验，因此本轮不能标记L2、L3或L4。
- 性能数据：本轮没有做容量压测；领取过滤在数据库查询和原子更新中完成，不对每条候选做JSON字符串扫描，避免在高并发领取时引入全表`LIKE`；后续需验证索引命中、批量领取吞吐和多Worker锁等待。
- 风险和遗留项：一期历史计划的管理人员可见性、旧项目成员查询语义、完整数据库零增长和跨四类执行器重启恢复仍需单独验收；生产后37个迁移仍未重建。
- 下一依赖与负责人：R09补齐跨执行器重启、并发竞争和数据库零增长测试；R03/R10继续U0A、U0B迁移与文件归属；完成后由R03/R04进入U0D下线项目、设备发布、OTA和模型回滚写入口。
- LabelU应用版本恢复办法：本轮没有发布应用版本，无生产回滚动作；如本地实现需调整，按本轮目标文件做前向修正，不能把模型“回滚”作为处理方案。
- 文档同步位置：本台账第8节和本记录；主方案业务边界不变。
- commit/push/deploy状态：未提交、未推送、未部署，等待用户明确说“提交代码”后才进入提交流程。
- L0/L1/L2/L3/L4结论：U0C需求与本地实现达到L1；跨执行器联调、真实设备链路和生产验收未完成，L2至L4均未通过。

### RUN-0005 项目和设备发布残留只读审计

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案10.9、15.3.5、一期负向验收第24项；清理治理`CLEAN-U0D-001`。
- 工作包/实施单元：WP0/U0D，影响WP1至WP5的授权、页面和发布真源。
- 主责角色/协作角色/验收角色：R03、R04；R01、R07、R08、R10；R09独立审计。
- 基线commit与工作树摘要：`146610b38`；本轮只读审计没有修改业务文件，没有覆盖并行变化。
- 本轮目标：逐项确认项目/业务空间、组织/场景、设备发布、EdgeAgent、OTA和模型回滚是否仍有可执行入口；区分必须保留的任务协作、BM1688验证设备、边端验证作业、AICM发布和模型下载控制。
- 明确不做：不删除历史表或迁移，不调用写接口，不连接生产，不执行设备操作，不修改路由，不提交、不推送、不部署。
- 菜单、页面、按钮和弹窗结论：React主导航仍暴露“部署中心”，页头仍强制选择项目；部署页仍提供创建、推进和回滚；模型详情仍存在第二套本地假发布按钮及抽屉；已发布模型旧页虽已有下载开关，但详情仍是抽屉。这些均与六个一级菜单、无项目维度、无设备发布/回滚和统一居中弹窗要求冲突。
- API、权限和错误码结论：当前项目/成员、组织/场景、设备发布状态机、发布回滚、EdgeAgent注册/心跳/能力、OTA清单/分片/校验/激活/回滚和内部假发布均已注册并进入OpenAPI；它们不是只读历史。后续旧写地址统一返回“该能力已下线”且不得写库，禁用写接口不进入一期OpenAPI；历史GET仅供管理人员审计并从公开OpenAPI隐藏。
- 必须保留的真链：任务协作接口；`/edge-devices`验证设备配置；`/yolo-edge-validation/jobs`边端验证作业；AICM配置、环境、可发布检查、发布和发布任务；已发布模型目录、禁止下载/恢复下载和下载校验。Board Agent外部路由和旧单机SSH设置在迁移到统一验证设备后关闭，不能演变为设备发布Agent。
- 数据表、迁移和制品结论：`project/project_member`、组织/场景、`deployment_release/target/observation/transition_audit`及`edge_agent/release/deployment`历史表一期先保留，禁止破坏性删表；生产后迁移以项目迁移为祖先，必须通过前向迁移逐步解除新业务依赖，不能直接删除中间迁移。
- 状态机、幂等键、租约和并发风险：当前设备注册表采用整份JSON读改写且锁晚于读取，多进程更新可能丢失；已发布模型下载开关缺少版本/CAS，并发修改为最后写入覆盖；AICM发布缺少“导出ID+精度+环境”可确认的幂等保护，双击或并发可能重复发布。这三项必须分别补事务锁或行模型、条件更新和唯一幂等键。
- 审计文件范围：后端路由注册、project/organization/deployment/edge_agent/model_version/board_agent/yolo_train路由及服务；React平台`modules.ts`、`layout.tsx`、`deployments.tsx`、`models.tsx`；OpenAPI和相关路由测试。
- 本地证据：直接从当前应用`app.openapi()`生成路由清单并与路由源码、前端入口和测试断言交叉核对；未运行任何写数据库用例。
- 真实性限制：没有连接生产核对历史表行数、真实调用方、Nginx路由或外部脚本，因此当前只能证明本地代码暴露，不能直接删除生产对象。
- 性能数据：没有压测；已识别工作台先全量读取再内存分页、设备整份JSON更新和并发发布/下载控制三个性能与一致性风险，列入U0D/U0E专项。
- 风险和遗留项：项目编号已渗透数据集、训练、模型、回流、工作流和权限判断，不能只隐藏项目菜单或把字段设空；旧SSH与统一设备、Board Agent与验证执行器、AICM发布与内部假发布需要逐一合并真源。
- 下一依赖与负责人：R03/R04先实现旧写接口硬门禁和OpenAPI负向测试；R08同步移除项目选择器、部署中心和回滚/假发布按钮并把抽屉改为弹窗；随后按任务创建者、协作者和管理人员重建保留模块授权；R07回归BM1688设备、SSH验证和AICM发布；R09验证调用旧接口数据库零变化。
- LabelU应用版本恢复办法：本轮只读，无需恢复；后续只能恢复LabelU应用自身版本或数据库备份，不能表述为模型回滚。
- 文档同步位置：本台账第8节和本记录；主方案15.3.5保持为产品真源。
- commit/push/deploy状态：未提交、未推送、未部署。
- L0/L1/L2/L3/L4结论：U0D需求矩阵和残留清单达到L0；尚未实施，L1至L4均未通过。

### RUN-0006 U0C全入口收口与本地组合验收

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案一期自动进化边界；安全治理`GATE-U0C-003`。
- 工作包/实施单元：WP0/U0C，覆盖训练、BM1688编译后恢复、边端验证、质量证据和管理人员历史查询。
- 主责角色/协作角色/验收角色：R03、R04、R05；R06、R07、R08；R09验收。
- 基线commit与工作树摘要：`146610b38`；保留工作树既有修改，没有重置、暂存、删除或恢复无关文件。
- 本轮目标：完成RUN-0003和RUN-0004遗留旁路收口；统一使用`queue_source=AUTO`、自动计划编号、自动轮次编号三项血缘识别；证明关闭一期自动训练计划时后台不再续训、领取评估任务、补质量证据或分配BM1688设备，同时人工链继续运行。
- 明确不做：不开放自动训练计划，不修改生产数据库，不执行迁移，不运行真实GPU训练、TPU-MLIR编译或BM1688真机任务，不提交、不推送、不部署。
- 菜单、页面、按钮和弹窗变化：标注人员不显示“历史自动训练计划”页签，带该查询参数进入时自动回到“训练作业”；管理人员保留历史查询；计划和报告详情继续使用居中弹窗，不使用抽屉。
- API、权限和错误码变化：一期关闭时非管理人员的自动训练历史GET返回403；31个写端点仍统一返回`403/57122`并从OpenAPI隐藏；服务直调、自动轮次占用、评估请求生成和评估任务领取均在访问数据库前执行同一门禁。
- 数据表、迁移和制品变化：没有新增表、迁移或制品；被过滤的历史自动Outbox保持原状态，不写租约、不增加尝试次数、不生成结果事件。
- 状态机、幂等键、租约和并发限制：YOLO续训、训练队列、训练Worker、训练后处理、质量证据恢复、BM1688恢复和边端统一调度均采用“候选查询过滤+实际执行前复核”；Outbox候选查询和原子领取更新复用同一谓词，避免并发领取窗口；过滤使用结构化血缘列，不解析JSON或使用`LIKE`。
- 主要修改文件：`auto_training_plan.py`、`auto_training_evaluator.py`、`auto_training_outbox.py`、`training_worker_runtime.py`、`yolo_train.py`、`yolo_train_queue.py`、`yolo_train_auto_resume.py`、`yolo_bm1688_export_auto_resume.py`、`yolo_edge_validation_auto_resume.py`、`yolo_edge_validation_scheduler.py`、`yolo_quality_evidence_auto_recovery.py`、React `training.tsx`及对应专项测试。
- 测试命令及结果：14个后端门禁、事件、Outbox、训练、编译、边端和质量恢复专项文件组合执行`186 passed`；React训练页`29 passed`；旧训练页契约`17 passed`；本轮最终合计`232 passed`。目标文件`git diff --check`无空白错误。
- 本地、预生产和生产证据：隔离SQLite和前端单测证明管理/标注角色可见性、自动/人工成对场景、数据库零新增和自动Outbox零领取；没有预生产或生产证据。
- 真实性限制：没有启动本地完整页面做浏览器视觉验收，没有执行多进程压测、生产数据库快照演练、真实GPU、TPU-MLIR、BM1688、S3或AICM链路；前端全量类型检查仍有RUN-0003记录的非U0C既有`TS2589`错误。
- 性能数据：本轮只验证了查询侧过滤和原子领取正确性，没有吞吐数值；进入L2前必须补充历史自动Outbox大表扫描、并发Worker领取、训练队列、编译互斥和边端设备租约压测。
- 风险和遗留项：U0A文件归属和U0B生产后37个迁移重建尚未完成；项目、设备发布、Agent、OTA和回滚写入口仍由U0D处理；本轮不能作为生产可用证明。
- 下一依赖与负责人：R03/R10完成U0A、U0B；R09在预生产补多进程、重启和容量测试；随后进入U0D。用户明确说“提交代码”后才可提交并按流程部署、生产验证。
- LabelU应用版本恢复办法：本轮没有发布应用版本，不存在生产应用回滚；如本地门禁需调整，按本轮文件清单前向修正。已发布模型仍只支持禁止下载，不使用模型回滚概念。
- 文档同步位置：本台账RUN-0006；主方案一期边界不变。
- commit/push/deploy状态：未提交、未推送、未部署。
- L0/L1/L2/L3/L4结论：U0C需求、代码和本地组合验收达到L1；未完成跨执行器真实联调、预生产和生产验收，L2至L4未通过。

### RUN-0007 U0D前端入口收口与生产只读对照

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第1章六个一级菜单、第2.2节角色菜单、第3章菜单关联、第15.3.5节历史路由迁移、一期负向验收第24项；清理治理`CLEAN-U0D-001`。
- 工作包/实施单元：WP0、WP5/U0D前端入口与生产事实对照。
- 主责角色/协作角色/验收角色：R08前端工程师主责；R01产品、R02体验、R03架构协作；R09测试验收。
- 基线commit与工作树摘要：继续基于`146610b38`及RUN-0002登记的脏工作树；本轮只修改既有或未跟踪的U0D前端文件、专项测试和本台账，不清理其他来源变化。
- 本轮目标：把本地未发布的八菜单壳收敛为方案规定的六菜单；删除顶部项目选择器；按两类用户收敛导航；隔离设备发布和通用工作流页面；对照生产真实页面和OpenAPI，避免误删BM1688真机验证设备与正式SSH真机验证通道。生产仍存在的外部Board Agent接口只作为历史差异记录，不进入一期目标方案。
- 明确不做：不修改模型详情页及其发布逻辑；不删除历史页面文件、数据库表或迁移；不改训练、编译、真机验证、AICM、S3业务数据；不调用生产写接口；不提交、不推送、不部署。
- 菜单、页面、按钮和弹窗变化：管理人员导航固定为“工作台、任务与标注、训练中心、模型中心、数据回流、系统管理”；标注人员只显示前四项。产品名统一为“7GAI 进化平台”。页头删除“当前项目”选择器和项目数量，改为当前菜单名称及说明。`/deployments`只跳转模型中心，`/workflows`只跳转系统管理，不再装载原页面。该批次没有新增抽屉；模型详情和其他存量抽屉由后续独立单元统一改为居中弹窗。
- API、权限和错误码变化：本轮前端不新增写API。导航管理员判定复用现有`/users/me`的`is_task_superuser`及内置管理账号兼容逻辑；后端仍必须独立返回真实403，不能依赖隐藏菜单。旧地址跳转会删除URL中的`project_id`。生产`openapi.json`只读核对显示版本1.4.5，存在`edge-devices`、`board-agent`、`edge-ssh-validation`等现行接口，但目标方案关闭外部Board Agent，只保留`edge-devices`、`yolo-edge-validation`和SSH正式真机链；生产不存在本地新增的project、deployment、edge-agent、workspace和workflow平台接口。
- 数据表、迁移和制品变化：无数据库、迁移、训练模型或边端制品变化。
- 状态机、幂等键、租约和并发限制：本轮不修改业务状态机。移除前端设备发布和工作流装载后，不再产生对应列表轮询；页头暂不装载仍依赖项目编号的通知组件，避免手动刷新绕过禁用状态产生无项目参数请求，待全局告警契约完成后再复用。后端写门禁、并发零写和历史只读仍是U0D后续验收项。
- 修改文件清单：`frontend/labelU-Kit/apps/frontend/src/pages/platform/modules.ts`、`layout.tsx`、`routeEntries.tsx`、`index.tsx`、`ResourcePage.tsx`、未路由历史页`pages/deployments.tsx`与`pages/workflows.tsx`的共享类型隔离、`frontend/labelU-Kit/apps/frontend/src/routes.tsx`、`src/utils/product.ts`、四个前端专项测试文件、`labelu/tests/e2e/test_7gai_platform_shell_frontend.py`和本台账。
- 测试命令及通过/失败/跳过数量：Vitest导航/布局/路由/标题专项`13 passed`；真实Vite浏览器专项`3 passed`，覆盖训练入口和两条旧地址跳转；`git diff --check`目标文件无空白错误。全量`tsc --noEmit`只剩既有`ListAttribute.fancy/index.tsx:115 TS2589`，本轮引入的4个菜单类型错误已清零。
- 本地、预生产和生产证据：本地Vite运行在`http://127.0.0.1:3017`，Playwright在1024x768真实浏览器验证通过。生产只读浏览器核对`http://113.141.72.175:10000/tasks`仍为LabelU 1.4.5任务列表，显示任务列表/模型训练/标注统计/数据集分析现有入口，没有本地八菜单壳、项目选择器、部署中心或柔性工作流；生产OpenAPI通过HTTP只读取得上述接口差异。未在生产执行按钮、表单或写请求。
- 真实性限制：生产浏览器只核对任务首页，不能代表全部生产页面；本轮没有预生产环境、生产部署、真实写接口零变化对账、并发压测或数据库快照验证。生产没有新增平台路由也不等于本地U0D后端门禁已完成。
- 性能数据：移除两个一级菜单及其页面加载后，导航不会触发对应模块请求；未做首屏包体对比、并发压测或长列表容量测试。
- 风险和遗留项：训练、模型、数据回流和系统管理内部仍大量依赖`project_id`；未路由历史页和旧E2E用例仍包含设备发布/回滚语义，须在U0A所有权确认后安全隔离或删除；页面抽屉尚未全部改为弹窗；标注人员直接访问管理页面的服务端403仍需验证；前端全量类型检查有一项既有失败。
- 下一依赖与负责人：R03/R04完成项目、部署、EdgeAgent、OTA和工作流写接口硬门禁及OpenAPI隐藏；R04按任务创建者/协作者/管理人员重建保留模块授权；R08继续解除业务页面`project_id`依赖并统一弹窗；R09执行直接地址、数据库零变化、角色越权和并发测试；R07回归真实BM1688设备和SSH正式真机验证链不受影响，并确认外部Board Agent不再接收新任务。
- LabelU应用版本恢复办法：本轮未发布，无生产应用恢复动作。若本地入口需修正，按本轮文件清单前向修正；不得恢复设备发布或模型回滚概念。
- 提交、推送、部署状态：均未执行，等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：U0D前端入口子项达到L1；U0D整体仍未达到L1，L2至L4均未通过。

### RUN-0008 U0D服务层硬门禁、两角色系统管理与弹窗统一

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第1章六个一级菜单、第2.2节两类用户、第3章菜单关联、第10.9节发布与禁止下载、第15.3.5节历史路由迁移和一期负向验收第24项；清理治理`CLEAN-U0D-001`。
- 工作包/实施单元：WP0、WP3、WP4、WP5/U0D，覆盖服务门禁、真机验证真源、模型发布真源和前端系统管理。
- 主责角色/协作角色/验收角色：R03架构、R04后端、R08前端；R01产品、R06模型工程、R07边端工程协作；R09测试独立验收。
- 基线commit与工作树摘要：继续基于`146610b38`及RUN-0002登记的脏工作树；保留用户和并行角色的既有变化，未重置、恢复、暂存或清理无关文件。
- 本轮目标：让已取消的项目/组织、设备发布、EdgeAgent、OTA、Board Agent队列、设备作业和内部假发布即使被绕过HTTP路由直接调用服务，也会在访问数据库前统一拒绝；同时保留BM1688编译、SSH真机验证、AICM发布、本平台已发布模型目录和下载控制。
- 明确不做：不破坏性删除历史表、迁移或历史查询；不关闭SSH真机验证和为其复用的BModel打包方法；不使用“模型回滚”概念；不连接或写入生产数据库；不提交、不推送、不部署。
- 输入和前置依赖：RUN-0005的写入入口清单和RUN-0007的六菜单前端收口；本轮不依赖新数据表，可在U0A/U0B完成前作为安全封锁单元实施。
- 菜单、页面、按钮和弹窗变化：系统管理实际默认页只保留“账号与角色”和“标注与数据治理”，只展示“管理人员”和“标注人员”两类平台角色；删除不可达的创建项目、项目成员、组织及项目归属页面死代码；模型详情删除调用内部`model-versions/{id}/publish`的“发布模型”按钮；平台通用详情、算法能力、模型详情、工作流详情、设备验签和数据集版本治理统一使用居中弹窗，不使用抽屉。
- API、权限和错误码变化：新增公共一期契约`phase_one_contract.py`；禁用服务入口统一返回HTTP 403、`err_code=30004`和中文提示“该能力已下线，请使用任务、训练、BM1688 编译、真机验证和模型发布主流程”；门禁覆盖项目写入、组织/场景创建、DeploymentRelease全部写流程、EdgeAgent注册/心跳/能力/OTA、设备作业、Board Agent队列和内部假发布。
- 数据表、迁移和制品变化：没有新表、迁移或模型制品；历史GET仅供管理人员审计，已取消对象暂不破坏性删除；删除BM1688编译完成后自动创建旧Board Agent验证队列的两处路径。
- 状态机、幂等键、租约和并发限制：禁用路径在获取数据库对象、分配租约或创建队列前退出，不进入旧状态机；正式真机验证以`yolo-edge-validation`和SSH通道为主链；本轮未改动AICM发布幂等、已发布模型下载开关CAS和长列表分页，三项仍列入U0E/后续性能专项。
- 修改文件清单：`common/phase_one_contract.py`；project、organization、deployment、edge_agent、edge_device_job、board_agent、model_version服务和对应路由；`yolo_bm1688_export.py`；React平台ResourcePage、algorithms、admin、datasets、deployments、models、workflows页面及对应专项测试。
- 测试命令及通过/失败/跳过数量：新增服务直调反向测试、React七个专项文件和正式保留链曾分别取得`3 passed`、`35 passed`、`116 passed`；本轮最终统一门禁与OpenAPI核心契约重新验证为`11 passed`。旧项目、组织、部署、EdgeAgent和通用工作流测试在RUN-0009按一期契约重写后，最终组合回归为`25 passed`。没有失败或跳过；仅有既有弃用警告。
- 未通过证据：React工程全量`tsc --noEmit`仍被既有`ListAttribute.fancy/index.tsx:115`的`TS2589: Type instantiation is excessively deep`阻断；该文件不属于U0D，本轮未修改。
- 本地、预生产和生产证据：本轮只有隔离SQLite单测和React组件测试；沿用RUN-0007的生产只读对照，没有新的生产写入、部署或预生产验收。
- 真实性限制：尚未在浏览器中完成系统管理两角色、居中弹窗和模型详情无假发布按钮的视觉验收；未使用真实BM1688设备、S3、AICM或生产数据库。
- 性能数据：未压测。服务门禁通过“数据库访问前退出”降低禁用能力的并发成本；系统账号当前一次最多读取200条后前端分页，后续必须改为服务端分页并验证长列表性能。
- 风险和遗留项：训练、模型、数据集、数回流和柔性工作流内部仍有`project_id`依赖；未路由历史页仍包含设备发布/OTA/回滚文案；旧测试仍有期待已取消能力可写的用例；全量类型检查仍未通过。
- 下一依赖与负责人：R03/R04按任务创建者、`TaskCollaborator`和管理人员解除保留业务的项目依赖；R08删除未路由页面死代码并完成浏览器验收；R09更新旧契约测试、执行并发零写和长列表压测；R07用真实BM1688设备回归SSH验证；R06/R07联调AICM发布和本平台下载控制。
- LabelU应用版本恢复办法：本轮未发布，无生产恢复动作。如果本地门禁实现需调整，只能对本轮目标文件做前向修正；已发布模型发现问题时由人工“禁止下载”，没有模型回滚机制。
- 文档同步位置：本台账第8节、RUN-0008和U0D专项测试；主方案业务边界不变。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”后才进入提交流程。
- L0/L1/L2/L3/L4结论：U0D需求、服务门禁、前端入口和本地组合回归达到L1；去项目真源、多进程并发、真实BM1688/S3/AICM联调、预生产和生产验收未完成，L2至L4未通过。

### RUN-0009 U0D旧测试契约收口与最终本地负向验收

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第1章六个一级菜单、第2.2节两类用户、第10.9节禁止下载而非模型回滚、第15.3.5节历史路由迁移；清理治理`CLEAN-U0D-001`。
- 工作包/实施单元：WP0、WP5/U0D最终本地验收。
- 主责角色/协作角色/验收角色：R09测试与质量主责；R03架构、R04后端、R07边端、R08前端协作；R01复核产品边界。
- 基线commit与工作树摘要：继续基于`146610b38`和RUN-0002脏工作树清单；本轮只重写五个已取消能力的旧测试并更新交付记录，没有恢复任何旧成功路径。
- 本轮目标：把仍要求项目、组织、场景、设备发布、Agent、OTA、模型回滚和通用工作流实例写入成功的旧测试改为一期真实契约；证明禁用能力统一拒绝、历史记录只读且数据库零变化。
- 明确不做：不删除历史表和历史记录；不改变正式任务、训练、BM1688编译、SSH真机验证、AICM发布和已发布模型下载链；不连接生产库，不启动后端，不提交、不推送、不部署。
- API、权限和错误码变化：没有新增API。项目/组织/场景/设备发布/Agent/OTA/通用工作流实例写入口必须返回HTTP 403、`err_code=30004`和统一中文提示；对应路由不出现在一期OpenAPI。工作流只保留Outbox查询、重试和派发接口。
- 数据表、迁移和制品变化：没有迁移或制品。测试通过隔离SQLite预置历史项目、组织、场景和设备发布记录，只验证管理人员历史只读；每组禁用调用均对相关表执行前后计数和关键字段不变断言。
- 状态机、幂等键、租约和并发限制：禁用入口必须在数据库访问、状态推进、租约分配和队列创建前失败。旧DeploymentRelease灰度/回滚状态机、Agent OTA状态机和通用工作流实例状态机不再有成功测试契约；Outbox内部幂等、失败重试和派发基础能力继续由原有专项测试负责。
- 修改文件清单：`test_project.py`、`test_organization_scene.py`、`test_deployment.py`、`test_edge_agent_protocol.py`、`test_workflow.py`，并同步本台账和基线清单。
- 测试命令及通过/失败/跳过数量：统一门禁和OpenAPI核心契约`11 passed`；项目、组织、部署、Agent、工作流、统一门禁和OpenAPI最终组合`25 passed`；`git diff --check`无空白错误，仅有目标文件既有LF/CRLF提示。全部使用`.tmp`独立SQLite，未连接生产数据库。
- 本地、预生产和生产证据：只有本地L1证据；没有预生产或生产写入验证。沿用RUN-0007生产只读事实，不能据此声明生产已完成下线。
- 真实性限制：部署历史测试使用占位`model_version_id=1`且测试SQLite未启用外键约束；迁移链重建和生产快照演练必须使用完整模型血缘记录。真实BM1688、S3、AICM和并发压测仍未执行。
- 性能数据：禁用服务直调使用“禁止数据库访问对象”证明门禁先于数据库；HTTP用例证明数据库零新增。尚未执行多进程并发和长列表压测。
- 风险和遗留项：保留业务对象仍有大量`project_id`依赖；生产后37个未执行迁移尚未重建；Outbox真实第三方网络派发、真机验证、发布和下载控制需后续联调。
- 下一依赖与负责人：R03/R10进入U0B迁移后缀重建；R04按任务协作权限解除保留模块的项目依赖；R09在迁移空库升级、生产快照演练后重新执行全量回归。
- LabelU应用版本恢复办法：本轮未发布，无生产应用恢复动作；已发布模型出现问题时只允许人工禁止后续下载，不存在模型回滚。
- 文档同步位置：本台账RUN-0007、RUN-0008、RUN-0009和`7gai_baseline_file_inventory.csv`。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：U0D负向契约和本地组合验收达到L1；L2至L4未通过。

### RUN-0010 U0B生产后迁移链重建审计与实施契约

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第15.3节历史兼容与迁移、第15.9节一期边界；迁移治理`MIG-U0B-001`。
- 工作包/实施单元：WP0/U0B分析与L0契约冻结。
- 主责角色/协作角色/验收角色：R03架构、R10数据库与运维主责；R04后端、R05集成、R06模型、R07边端协作；R09测试验收。
- 基线commit与工作树摘要：`146610b38`；生产基线版本沿用已记录的`h7i8j9k0l1m2`，本轮没有重新连接生产；本地版本目录仍有37个未跟踪迁移。
- 本轮目标：逐个判断37个迁移的保留、重写、拆分和排除；定义从生产版本开始的唯一线性后缀、ORM空库规则、性能约束和快照演练验收条件。
- 明确不做：本轮不修改或删除37个迁移，不执行upgrade/downgrade，不连接生产库，不启动应用，不修改业务数据，不提交、不推送、不部署。
- 分析结论：不能追加第38个清理迁移；重建为“数据与标签、算法与模型、内部执行、导入媒体、问题样本、预标注预测、三类规则、兼容字段索引”8段线性后缀。12个旧方向迁移整体排除，其余保留重写或拆分，所有保留新表去除`project_id`。
- 数据和模型决定：复用现有`yolo_bm1688_export`，增加nullable模型版本关联，不创建第二套模型优化状态机；FP32、FP16、INT8是同一模型版本的三条BModel制品；问题样本按外部包、AICM模型ID、已发布模型目录ID和摘要对齐。
- ORM和空库发现：当前`env.py`与`run_migrate.py`在空库直接执行`Base.metadata.create_all()`，当前ORM共注册118张表，其中35张是取消对象或仍通过`project_id`依赖项目。只替换迁移会让空库重新生成取消表，因此U0B必须同步重构领域模型并建立共享的一期表筛选函数。
- 性能和并发决定：大表按nullable列、在线索引、分批回填、最终约束分阶段执行；设置短元数据锁等待；禁止迁移引用可变应用常量和执行全表同步更新；Outbox、租约、Provider、S3包、问题样本和主动学习必须有并发幂等证据。
- 只读验证命令及结果：Alembic当前仍为单一head `z3a4b5c6d7e8`；从`h7i8j9k0l1m2`到当前head还原出37个修订；本地ORM注册表数为118，项目依赖或取消对象清单为35张。命令未执行数据库连接和结构写入。
- 修改文件清单：新增`labelu/docs/7gai_u0b_migration_rebuild_plan.md`，更新本台账；没有修改迁移和业务代码。
- 本地、预生产和生产证据：只有本地文件、迁移图和ORM元数据只读证据；生产版本号来自此前基线，可能随外部部署变化，正式实施前必须再次只读确认。
- 风险和遗留项：历史只读表与一期空库表必须采用同一ORM中的明确标记或独立元数据，不能靠文件名约定；生产快照副本、空MySQL、真实表规模和DDL锁时间尚未取得。
- 下一依赖与负责人：R03/R10先实现共享一期表选择和8段迁移骨架，R04同步去除保留领域模型的项目外键，R09建立空库/生产版本升级契约；每段完成后独立记录和回归。
- LabelU应用版本恢复办法：本轮只有文档，不涉及应用恢复；迁移演练失败使用测试库重建或快照副本恢复，不得表述为模型回滚。
- 文档同步位置：`7gai_u0b_migration_rebuild_plan.md`和本台账RUN-0010。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：U0B重建方向和验收契约达到L0；实现与所有环境验收尚未开始。

### RUN-0011 U0B-01数据与标签契约去项目化只读审计

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第2章两类用户、第5章任务与数据、第15.3.5节项目残留清理、第15.8节并发性能；迁移治理`MIG-U0B-001`、数据权限`DATA-U0B-01`。
- 工作包/实施单元：WP0/U0B-01分析与L0契约冻结，影响WP1任务与数据版本、WP2训练与模型管理。
- 主责角色/协作角色/验收角色：R03架构、R04后端主责；R01产品、R05集成、R06模型、R08前端协作；R09测试、R10数据库验收。
- 基线commit与工作树摘要：`146610b38`；沿用637个实际文件路径的脏工作树基线；本轮没有恢复、删除、覆盖或暂存其他来源修改。
- 本轮目标：逐层核对`Dataset`、`DatasetVersion`、`DatasetLabelContract`的领域字段、权限、路由、训练调用、模型血缘、前端请求和并发风险，确定无项目维度后的唯一实现契约。
- 明确不做：不编辑业务代码和迁移，不启动后端，不连接本地或生产数据库，不调用写接口，不执行训练、编译、边端验证、提交、推送或部署。
- 当前中间态结论：三个ORM对象已经不含`project_id`，但现有生产后迁移仍为三张表创建非空项目外键且没有删除步骤；统一训练、任务绑定、模型版本和训练Worker仍读取已不存在的`Dataset.project_id`或`DatasetVersion.project_id`。该状态不能启动、部署或验收。
- 字段和权限契约：`Dataset.owner_id`是持续治理责任人，`created_by`是首次创建动作审计人；自动数据集允许owner为任务创建者、created_by为实际触发训练的协作者。版本和标签契约的`created_by/frozen_by/reviewed_by`只记录动作人，不作为持续授权依据。管理人员可查看和治理全部；标注人员可创建数据集，查看自己的数据集和当前可访问任务关联的数据集；只有owner或管理人员可修改数据集、标签契约及人工治理版本；任务协作者可基于可访问任务触发系统自动生成训练版本，但不能取得共享数据集治理权。
- 已确认权限缺口：`_dataset_or_raise(..., manage=True)`当前没有执行owner/管理人员判断，导致任意可读用户都能修改、评审或冻结；第三方任务按现有生产规则对认证标注人员共享，必须保留查看、标注和发起训练能力，但不能因此获得标签契约和数据版本治理权。统一训练的显式`dataset_version_id`和非YOLO分支还可能绕过任务成员前置鉴权。
- 关联对象失效规则：任务软删除后保留数据集和不可变版本，owner仍可审计；协作者被移除或其最后一个活动关联任务被删除后，应立即失去该数据集的读取和新训练权限；已创建或运行中的训练作业不自动取消。多任务合并必须逐任务鉴权，不能以数据集可见代替任务可见。
- 前端结论：数据集页、训练页、`ResourcePage`仍受`usePlatformProject`、`project_id`请求参数和项目查询键控制，无项目时列表和按钮空转；目标实现改为当前用户可见资源查询，创建、候选任务、标签契约、版本和训练请求均不携带项目参数，并保持现有居中弹窗。
- 并发与性能结论：数据集列表采用相关`EXISTS`，多任务/多协作者不会产生重复行，保留该结构；候选任务当前全量加载、内存分页并逐任务查询，存在N+1和数据量线性放大，必须改成数据库count/分页和当页批量统计。版本号、契约修订号的`max+1`、自动数据集先查后建、清单先写文件后插数据库均存在竞态和孤儿文件风险。
- 迁移决定：不追加第38个清理迁移。三张表去项目字段、访问索引和自动数据集幂等键必须并入RUN-0010确定的8段重建链第一段；生产基线`h7i8j9k0l1m2`后不再创建项目表或项目外键。历史项目字段仅在既有生产历史表中只读保留，不参与新权限和幂等键。
- 必须保留的既有行为：数据版本接入、校验、待评审、冻结、退役状态机；人工评审后冻结；冻结版本不可修改；标签顺序、标签快照和双签名；全标签/单标签训练；已完成且质量合格样本准入；系统生成清单、SHA256和训练/验证/测试划分；相同样本集合重复操作复用冻结版本；一期仅允许矩形框。
- 文件级实施范围：数据集/标签契约领域、命令、响应、路由、服务和持久化；任务绑定与训练候选；统一训练命令、路由、Worker和模型版本血缘；React数据集页、训练页和资源页；第一段重建迁移及对应后端、前端、空库和升级测试。
- 验收测试设计：owner/协作者/非关联标注人员/管理人员/第三方共享任务权限矩阵；显式版本和自动版本同一任务鉴权；移除协作者与任务软删即时失权；多任务列表不重复且total准确；20并发同一训练请求仅生成一个自动数据集、一个契约修订和一个相同冻结版本且无孤儿清单；页面URL、请求体、查询键和中文文案无项目维度。
- 测试命令及通过/失败/跳过数量：本轮为只读代码路径审计，没有执行测试；测试证据为零，不能据此宣称实现完成。
- 本地、预生产和生产证据：仅有当前工作树代码、迁移文件和页面调用路径证据；没有运行期、预生产或生产证据。生产版本号沿用此前只读基线，正式迁移前必须重新核对。
- 性能数据：未压测；已识别候选查询N+1、`max+1`竞态、自动数据集重复创建和孤儿清单四类并发瓶颈，后续以20并发和有界SQL数量作为专项验收。
- 风险和遗留项：第一段重建迁移、空库一期表选择、生产快照升级、统一训练跨适配器鉴权和前端去项目化均未实现；当前本地应用继续禁止连接生产库启动或部署。
- 下一依赖与负责人：R03/R10完成空库一期表选择及第一段重建迁移；R04落实治理权限、任务鉴权和幂等；R08移除数据集/训练页项目依赖；R09按权限与并发矩阵独立验收。
- LabelU应用版本恢复办法：本轮没有应用修改或发布，无生产恢复动作；后续迁移只在空库和生产快照副本演练，失败时销毁测试库或从快照副本重建，不使用模型回滚概念。
- 文档同步位置：本台账RUN-0011；实现细节继续同步`7gai_u0b_migration_rebuild_plan.md`。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”后才进入提交流程。
- L0/L1/L2/L3/L4结论：数据与标签去项目化字段、权限、关联失效、并发和验收契约达到L0；代码、测试、真实环境和生产验收尚未完成，L1至L4均未通过。

### RUN-0012 U0D板端主动拉取验证通道纠偏与并发领取保护

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第9.8.2节BM1688编译与真机验证、第15.3.5节历史兼容、一期验收第23至24项；清理治理`CLEAN-U0D-001`、边端验证`EDGE-U0D-02`。
- 工作包/实施单元：WP0、WP3/U0D真机验证保留链纠偏。
- 主责角色/协作角色/验收角色：R07边端算法、R04后端主责；R01产品、R03架构协作；R09测试独立验收。
- 基线commit与工作树摘要：继续基于`146610b38`和RUN-0002脏工作树清单；只修改板端验证路由/服务/领取持久化、对应契约测试、主方案和本台账，没有恢复设备发布、OTA或回滚能力。
- 本轮目标：纠正RUN-0005至RUN-0009把现有板端主动拉取验证通道整体当作设备控制面下线的错误；以生产现有接口、服务实现和BM1688既有回归测试为依据，恢复该通道并明确与SSH验证的主次和边界。
- 产品与技术结论：SSH/SFTP是一期受控设备的默认正式通道；网络策略不允许平台主动连接时，设备配置可选择“板端主动拉取”。代码内部`board_agent`只表示设备侧验证执行程序，不是人员角色，不在产品页面显示英文，也不是模型下发、设备发布、空中升级或回滚代理。同一个验证任务只能选择一个通道。
- API和页面契约：平台用户通过`/yolo-bm1688-exports/{export_id}/board-validation`创建和查询板端验证，GET/POST恢复进入正式OpenAPI；设备侧`/board-agent/jobs/*`继续使用设备令牌，只供内部执行程序调用并从公开OpenAPI隐藏；旧`/edge-device-jobs`仍统一返回403/30004。页面统一显示“板端主动拉取”或“边端验证执行程序”，不显示Agent。
- 状态机、幂等和并发：设备领取由“先查询、再修改”改为带原状态条件的原子更新；多个设备同时领取时只有一个更新成功，失败者重新选择候选。已超时的处理中任务仍可按原规则重新领取。该修正只保护领取竞争，设备租约、每任务短期令牌和请求板端验证的并发幂等仍列入U0E，不据此声称完整并发验收通过。
- 修改文件清单：`adapter/routers/board_agent.py`、`adapter/routers/yolo_train.py`、`application/service/board_agent.py`、`adapter/persistence/crud_yolo_board_validation.py`、`test_phase_one_retired_control_plane.py`、`test_openapi_docs.py`、新增`test_board_agent_retained.py`、主方案和本台账。
- 测试命令及通过/失败/跳过数量：使用全新隔离SQLite运行旧控制面门禁、板端保留通道、OpenAPI和原失败的统一状态接口，共`15 passed`、0失败、0跳过；仅有既有SQLAlchemy/Pydantic弃用警告。
- 本地、预生产和生产证据：本轮代码证据为本地L1；生产依据沿用此前只读核对到的`/board-agent/*`、板端状态接口、配置和历史记录，没有执行生产写入、真实设备领取、SSH、S3或AICM操作。
- 真实性限制：未用真实BM1688跑三精度模型，未做多进程/多设备压力测试；当前设备令牌仍是静态配置，尚未达到主方案要求的每任务短期令牌；不能标记L2、L3或L4。
- 下一依赖与负责人：R07/R10在U0E把SSH和板端主动拉取接入统一验证任务、设备配置快照和租约；R04实现创建验证任务幂等与短期任务令牌；R09执行双设备并发领取、超时重领、断网恢复和真实BM1688三精度验证。
- LabelU应用版本恢复办法：本轮未发布，无生产恢复动作；若本地实现需修正，采用前向修复。已发布模型有问题时仍只允许人工禁止下载并发布替代版本，不存在模型回滚。
- 文档同步位置：主方案第9.8.2节、本台账第8节和RUN-0012。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：板端主动拉取保留契约、HTTP门禁纠偏和单库原子领取达到L1；统一设备租约、短期令牌、真实设备和生产验收未完成，L2至L4未通过。

### RUN-0013 前端翻译类型递归错误收口

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第15.10节前端可用性与中文化、第15.11节研发验收；前端基线质量`FE-TYPE-001`。
- 工作包/实施单元：WP5/U0D前端基础兼容。
- 主责角色/协作角色/验收角色：R08前端主责；R03架构协作；R09测试验收。
- 本轮目标：修复生产二开前端在 TypeScript 4.8 下调用国际化钩子时的深层类型递归，避免基础类型错误阻断页面构建和后续菜单/弹窗验收。
- 修改范围：在标注配置、样本卡片、样本列表、标注页、协作人员弹窗和任务统计组件中，将国际化钩子通过本地兼容类型显式收窄为中文键值函数；不改变翻译键、页面行为、接口或业务权限。
- 修改文件清单：`frontend/labelU-Kit/apps/frontend/src/pages/tasks.[id].edit/partials/AnnotationConfig/formConfig/customFancy/ListAttribute.fancy/index.tsx`、样本页`index.tsx`、`sliderCard/index.tsx`、`slideLoader/index.tsx`、`CollaboratorModal/index.tsx`和`Statistical/index.tsx`。
- 测试命令及结果：R04/R08同步补齐任务更新命令的数据集字段后，`pnpm --filter @labelu/frontend type-check`全量通过；任务/标注相关Vitest共`12 files、60 passed`；`git diff --check`无空白错误。
- 真实性限制：未启动浏览器做视觉回归；类型检查和逻辑专项通过不代表六菜单、居中弹窗、窄屏、长文本和焦点行为已完成视觉验收。
- 下一依赖与负责人：R09继续执行浏览器六菜单、协作弹窗、标注画布和窄屏验收；R04/R08在U0B组合回归中验证任务更新的数据集/标签契约字段真实保存并可重载。
- commit/push/deploy状态：未提交、未推送、未部署；无生产写入，等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：翻译类型兼容、全量类型检查和相关前端逻辑专项达到L1；浏览器、预生产和生产验收未完成，U0D整体仍不能超过L1。

### RUN-0014 U0B任务数据集绑定去项目化与项目归属写入口补漏

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第2章两类用户、第5章任务与数据、第15.3.5节项目残留清理；数据权限`DATA-U0B-01`、清理治理`CLEAN-U0D-001`。
- 工作包/实施单元：WP1/U0B任务和数据集绑定；WP0/U0D项目写入口补漏。
- 主责角色/协作角色/验收角色：R04后端、R08前端主责；R03架构协作；R09测试验收。
- 本轮目标：让标注人员不依赖项目创建/编辑任务并绑定有权使用的数据集；补齐被旧测试暴露的任务归属项目写接口，确保无项目方案在HTTP和服务直调层均成立。
- 权限和关联规则：管理人员可绑定任意有效数据集；数据集所有者可绑定自己的数据集；已是该数据集关联任务创建者或协作者的用户可复用该数据集创建任务；无关标注人员拒绝；第三方共享任务可访问任务，但不因此自动获得新任务绑定数据集的治理权限。
- API和页面变化：任务创建不再自动创建默认项目；任务创建/编辑数据集下拉查询当前用户可访问数据集，不传项目编号；任务更新命令正式包含数据集、标签契约和标签范围字段。`/tasks/project-assignment`与`/tasks/{task_id}/project`从一期OpenAPI隐藏，服务访问数据库前统一返回403/30004，历史项目字段只读保留。
- 修改文件清单：任务命令/响应、路由、服务、持久化与测试；前端任务类型、任务编辑和数据集选择；项目归属写门禁及OpenAPI负向测试。完整文件归属继续以基线清单为准。
- 测试命令及结果：数据集绑定专项`6 passed`；任务、板端验证、旧控制面和OpenAPI跨模块组合使用全新隔离SQLite得到`48 passed`；前端全量`tsc --noEmit`通过；任务/标注相关Vitest`60 passed`；`git diff --check`无空白错误。
- 并发和性能限制：本轮没有实现数据版本号、契约修订号、自动数据集和清单文件的并发幂等；这些仍由U0B迁移和服务并发单元处理。数据集候选查询的服务端分页/N+1优化也尚未验收。
- 本地、预生产和生产证据：只有本地L1证据；未连接生产数据库，未执行生产写入、迁移、部署或浏览器生产验证。
- 风险和遗留项：Task ORM和历史接口仍保留`project_id`兼容字段；U0B迁移后缀、空库一期表筛选、训练/模型链去项目依赖未完成，当前工作树仍不可部署。
- 下一依赖与负责人：R03/R10完成第一段迁移和空库引导；R04继续训练入口与模型血缘去项目化；R09执行权限矩阵、并发创建和生产快照副本升级验收。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：任务数据集绑定与项目归属写门禁达到本地L1；迁移、并发、预生产和生产验收未完成，L2至L4未通过。

### RUN-0015 U0B空库一期表引导与迁移入口专项

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第15.3节历史兼容与迁移、第15.9节一期边界；迁移治理`MIG-U0B-001`。
- 工作包/实施单元：WP0/U0B空库引导和迁移入口。
- 主责角色/协作角色/验收角色：R03架构、R10数据库/运维主责；R04后端协作；R09测试验收。
- 本轮目标：验证空SQLite建库和迁移入口统一使用一期表白名单，避免取消的项目、组织、设备发布、Agent和OTA对象因`Base.metadata.create_all()`重新出现。
- 修改范围：迁移环境、运行迁移脚本、数据库表筛选函数和空库专项测试；未连接生产、不执行生产升级、不删除历史表。
- 当前发现：空库专项已通过，但领域模型中仍有22个项目外键依赖，第一段生产后缀尚未完成，不能直接生成生产升级迁移，也不能部署去项目化训练/模型链。
- 测试命令及结果：`$env:LABELU_TEST_DB_NAME='migration-empty-20260917-a.db'; python -m pytest labelu/tests/alembic/test_empty_database_upgrade.py labelu/tests/internal/common/test_run_migrate.py -q`，`4 passed`、0失败、0跳过；仅有既有弃用警告。
- 性能和并发：本轮仅验证空库结构和迁移入口，不包含真实表规模、DDL锁等待、生产快照升级或并发启动；迁移锁与同连接建表/版本写入仍需专项验收。
- 真实性限制：没有使用生产数据库、生产快照副本或MySQL真实实例，不能标记L2至L4。
- 下一依赖与负责人：R03/R10继续清理22个项目外键并完成8段唯一后缀；R04同步调整受保留模型；R09补空MySQL、外键闭包、并发启动、部分DDL失败和生产快照对账测试。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：空库一期引导和入口契约达到L1；生产迁移链、模型依赖清理、快照升级和性能验收未完成，L2至L4未通过。

### RUN-0016 U0B统一训练页面去项目化

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第1章六菜单、第7章训练中心、第15.3.5节项目残留清理；训练前端`TRAIN-U0B-01`。
- 工作包/实施单元：WP2/U0B统一训练页面。
- 主责角色/协作角色/验收角色：R08前端主责；R01产品、R04后端协作；R09测试验收。
- 本轮目标：让训练中心按当前登录用户和任务权限工作，不依赖项目选择器、项目查询键或项目请求参数，同时保持一期自动训练计划只读门禁和居中弹窗要求。
- 页面和请求变化：训练目录、可训练任务、训练汇总和作业列表请求不再携带`project_id`；创建训练请求体不再发送项目编号；作业列表设置为无需项目上下文；查询缓存键改为无项目稳定键；新建训练按钮只依赖登录态；训练详情、历史自动计划和报告移除项目文案；创建与详情均继续使用居中弹窗。
- 修改文件清单：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/training.tsx`和对应`training.test.tsx`。
- 测试命令及结果：训练页面Vitest`1 file、30 passed`；前端全量`pnpm --filter @labelu/frontend type-check`通过。
- 真实性限制：本轮只证明前端请求和类型契约；后端统一训练入口、DatasetVersion冻结、全标签/单标签训练、初始权重兼容和训练Worker仍需在U0B后续实现与真实训练验收。
- 下一依赖与负责人：R04解除训练路由、服务、Worker和模型血缘的项目依赖；R06用真实YOLO11基础权重和兼容历史PT分别验证全标签/单标签训练；R09执行浏览器创建、刷新、失败恢复和权限矩阵。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：统一训练页面去项目化达到L1；后端、Worker、真实训练和生产验收未完成，L2至L4未通过。

### RUN-0013A U0B-01A数据集查看与治理权限分离

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第2.2节两类用户、第5章任务/标签/数据版本、第15.3.5节去项目化；数据权限`DATA-U0B-01A`。
- 工作包/实施单元：WP0/U0B-01A安全收口，影响WP1数据集治理与WP2任务触发训练。
- 主责角色/协作角色/验收角色：R04后端主责；R03架构、R06模型协作；R09测试验收。
- 基线commit与工作树摘要：`146610b38`；继续保留既有脏工作树，没有重置、恢复、暂存或覆盖无关文件；本单元目标文件在当前工作树中均为未跟踪新链文件。
- 本轮目标：落实RUN-0011已冻结的“可查看不等于可治理”权限，保留任务协作者触发系统训练快照的能力，并将旧项目型数据集测试改为两类用户和任务协作权限测试。
- 明确不做：不重建迁移，不修改统一训练其他适配器，不改React页面，不解决并发幂等，不启动服务，不连接生产，不提交、不推送、不部署。
- API和权限变化：新增数据集治理权限判断；管理人员或`owner_id`责任人可修改数据集、创建/冻结标签契约、手工登记/评审/冻结/退役版本；任务创建者、当前协作者和第三方共享任务用户只有查看权限，不能调用公开治理写接口。自动训练内部调用`create_version_from_tasks(..., auto_freeze=True)`按任务访问权生成不可变快照，公开接口仍要求owner或管理人员。
- 数据和迁移变化：没有新增字段、表或迁移；`owner_id`继续作为治理权限主体，`created_by/frozen_by/reviewed_by`继续作为动作审计字段。数据库迁移与ORM不一致问题仍由U0B第一段重建迁移解决。
- 页面、按钮和弹窗变化：本轮没有前端变化；数据集和训练页仍有项目依赖，尚未达到页面可用标准。
- 修改文件清单：`labelu/internal/application/service/dataset.py`、`labelu/tests/internal/adapter/routers/test_dataset.py`、`labelu/tests/internal/adapter/routers/test_dataset_label_contract.py`、本交付台账。
- 测试迭代记录：首次执行数据集与标签契约组合测试得到`3 failed, 8 passed`，原因是`auto_freeze`判断误放到普通手工版本登记函数并触发`NameError`；修正为手工登记始终要求治理权限、自动快照仅在真正的任务生成入口放宽后，再次执行相同测试得到`11 passed`。
- 测试命令及通过/失败/跳过数量：`python -m pytest labelu/tests/internal/adapter/routers/test_dataset.py labelu/tests/internal/adapter/routers/test_dataset_label_contract.py -q --disable-warnings`，最终`11 passed`、零失败、零跳过；存在115条既有弃用警告。目标文件`git diff --check`无空白错误。
- 覆盖的行为：无项目参数创建数据集与数据版本；owner和管理人员治理；协作者查看但公开写入403；无关联用户查看403且列表无数据；管理人员跨责任人列表；标签契约创建/冻结与响应不含`project_id`；OpenAPI数据集创建/列表不含项目参数。
- 本地、预生产和生产证据：只有进程隔离SQLite API测试；没有真实MySQL、前端、训练执行器、预生产或生产证据。
- 真实性限制：该测试使用`Base.metadata.create_all()`，不能证明当前Alembic升级链可用；未覆盖显式数据版本绕过、非YOLO适配器、协作者移除即时失权、第三方共享任务治理隔离和20并发幂等。
- 性能数据：本轮未压测；权限判断对读取使用相关`EXISTS`，治理写入只判断责任人/管理人员，不增加全表扫描。
- 风险和遗留项：迁移仍创建项目非空字段；统一训练、任务/模型血缘和前端仍存在项目依赖；版本号/契约修订号和自动数据集创建竞态尚未处理。因此本单元不得独立部署。
- 下一依赖与负责人：R03/R10完成U0B空库选择与第一段重建迁移；R04继续统一训练任务鉴权和幂等；R08完成数据集/训练页去项目化；R09增加协作者移除、第三方共享、显式版本绕过和并发测试。
- LabelU应用版本恢复办法：本轮未发布；若本地实现需要调整，只做目标文件前向修正，不使用模型回滚或生产数据库降级。
- 文档同步位置：本台账RUN-0013；主方案口径不变。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：数据集查看/治理权限子单元达到本地L1；U0B整体仍停留在L0，L2至L4均未通过。

### RUN-0014A WP1数据集页面去项目化与中文弹窗链

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第1章六个菜单、第2.2节两类用户、第3章菜单关联、第5章任务与数据、第15.3.5节去项目化；前端约束`UI-WP1-001`。
- 工作包/实施单元：WP1前端数据集页面收口。
- 主责角色/协作角色/验收角色：R08前端主责；R01产品、R02体验、R03架构、R04后端协作；R09测试验收。
- 基线commit与工作树摘要：`146610b38`；保留工作树其他并行成果；本轮只修改数据集页面、资源页类型声明、页面专项测试和本台账。
- 本轮目标：让“数据集管理”不再依赖项目选择器，直接按当前账号可见任务/责任人从真实API加载；新建数据集、标签契约和训练版本继续使用已有居中弹窗，页面文案全部改为中文业务语义。
- 明确不做：本轮不改训练页、后端训练路由、迁移、项目历史只读查询、模型发布和设备验证；训练页仍有项目依赖，不能据此宣称整个平台去项目化完成。
- 页面与按钮变化：移除数据集页`usePlatformProject`、`project_id`请求参数和项目查询键；新建数据集按钮不再因没有项目而禁用；标签契约、版本治理和来源任务弹窗按数据集ID调用；“当前项目暂无任务/项目管理员”改为“暂无可用任务/数据集责任人或管理人员”。所有页面弹层继续使用居中`Modal`，本轮没有新增抽屉。
- API与查询变化：`fetchDatasets(page,size)`调用`GET /v1/datasets?page=&size=`；`fetchSourceTasks(datasetId,page,size)`调用`GET /v1/tasks?dataset_id=&status=FINISHED&page=&size=`；创建请求不带项目字段；React Query键改为数据集/版本/契约自身键；`ResourcePage`以`needsProject={false}`启用当前登录用户查询。
- 并发与性能：沿用服务端分页，数据集列表不再等待项目列表请求；来源任务查询仍限制100条并按数据集/完成状态过滤，后续后端需补数据库分页和总数优化。弹窗打开才加载标签、来源任务和版本，避免首屏N+1请求。
- 修改文件清单：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/datasets.tsx`、`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/datasets.test.tsx`、`frontend/labelU-Kit/apps/frontend/src/pages/platform/ResourcePage.tsx`。
- 测试命令及通过/失败/跳过数量：`pnpm exec vitest run src/pages/platform/pages/datasets.test.tsx`，`9 passed`、零失败、零跳过；既有React/Ant Design/jsdom警告未影响结果。`pnpm exec tsc --noEmit --pretty false`未通过，唯一报错为既有`pages/tasks.[id].edit/index.tsx:449`向旧`UpdateCommand`传入`dataset_id`，与本轮数据集页面无关，需由任务页面负责人修正后才能标记前端全局类型通过。
- 本地、预生产和生产证据：只有前端Vitest组件/请求断言；未启动前端、未做浏览器截图、未联调真实API、未连接生产。
- 真实性限制：`ResourcePage`的详情组件仍保留通用`projectId`参数以兼容尚未迁移模块；训练页、模型页和若干旧路由仍拼接项目参数；后端任务列表是否接受`dataset_id`且不要求项目需由WP1后端联调确认。
- 风险和遗留项：数据集页面虽已不依赖项目，但当前ORM/迁移不一致和训练链硬编码仍阻止生产启动；浏览器级响应式和弹窗视觉验收未完成；全局类型检查被任务编辑页既有错误阻塞。
- 下一依赖与负责人：R04/R03确认任务列表与数据集绑定接口；R08继续训练页去项目化并清理ResourcePage详情传参；R02/R09执行桌面/窄屏浏览器验收；U0B迁移链完成后再做真实MySQL联调。
- LabelU应用版本恢复办法：本轮未发布，无生产恢复动作；前端问题采用前向修正，不使用模型回滚或生产数据库降级。
- 文档同步位置：本台账RUN-0014；主方案菜单与权限口径不变。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：数据集页面无项目调用和中文居中弹窗专项达到本地L1；全局类型、真实API、浏览器、预生产和生产验收未完成，WP1与平台整体L2至L4均未通过。

### RUN-0017 U0B-01B一期空库建表范围显式化与闭包清单

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：迁移重建方案第4章ORM和空库引导、第7章空库验收；数据库基线`DB-U0B-01B`。
- 工作包/实施单元：WP0/U0B-01B空SQLite一期表范围。
- 主责角色/协作角色/验收角色：R03架构、R10数据库主责；R04后端协作；R09测试验收。
- 本轮目标：解决`env.py`、`run_migrate.py`和应用`init_tables()`三条空库入口各自直接执行全量`Base.metadata.create_all()`的问题，防止新空库继续创建已取消的项目、组织、场景、设备发布、设备代理、OTA、模型优化第二状态机和设备规则签名表。
- 设计结论：新增唯一共享的一期表范围模块，三条空库入口只调用`create_phase_one_tables()`；历史生产库遗留表不删除、不降级，仍由迁移和只读审计处理。共享模块同时提供外键闭包诊断函数，发现一期表指向范围外表时输出具体`来源表->目标表`，不静默删除字段或外键。
- 并发与性能：一期表名计算为进程内元数据集合运算，只发生在启动/迁移阶段；建表顺序固定，避免不同入口形成不一致表集。本轮未改变MySQL连接池、业务查询或后台领取并发行为。
- 修改文件清单：新增`labelu/internal/common/phase_one_schema.py`、新增`labelu/tests/alembic/test_phase_one_schema_scope.py`；修改`labelu/internal/common/db.py`、`labelu/alembic_labelu/env.py`、`labelu/alembic_labelu/run_migrate.py`、`labelu/tests/alembic/test_empty_database_upgrade.py`和本交付台账。
- 测试命令及通过/失败/跳过数量：`python -m pytest labelu/tests/alembic/test_empty_database_upgrade.py labelu/tests/alembic/test_phase_one_schema_scope.py -q --disable-warnings`，`5 passed`、零失败、零跳过、115条既有弃用警告；目标文件`git diff --check`无空白错误。
- 覆盖行为：共享范围排除遗留控制面；闭包诊断能识别`task->project`类残留外键；真实Alembic空SQLite升级创建任务、数据集等一期表，不创建全部已登记的退休表；重复`upgrade head`保持同一表集和版本号；当前21张一期表的项目外键形成精确测试清单（`model_version`已由后续模型血缘改造移除），后续只能随去项目化逐项缩小，不能通过调整白名单静默隐藏。
- 本地、预生产和生产证据：只有隔离临时SQLite证据；未执行空MySQL、生产快照副本迁移、生产服务启动或写入。
- 真实性限制：当前ORM仍有`task`、训练、回流、工作流、预测、主动学习等表保存项目外键，模型版本的项目外键已先行移除；SQLite可在未启用外键校验时创建这些引用，但MySQL不能把它视为完整闭包。因此本轮只达到“一期表范围入口统一”，尚未达到迁移方案要求的完整去项目化。
- 风险和遗留项：必须在八段重建链中逐段移除项目外键，之后启用`assert_phase_one_foreign_key_closure()`硬门禁；空MySQL和生产快照副本测试未完成前，禁止连接生产库启动或部署。当前范围已采用显式一期白名单，未来新增ORM表默认不会进入空库，必须经过方案和迁移评审后显式加入。
- 下一依赖与负责人：R03/R10完成第一段数据与标签迁移及ORM去项目外键；R04继续统一训练入口和任务鉴权；R09在外键闭包清零后执行空MySQL、唯一head和生产快照副本升级对账。
- LabelU应用版本恢复办法：本轮未发布；本地失败时删除专项临时SQLite重跑。生产已有遗留表不受空库范围函数影响，不执行表删除或模型回滚。
- 文档同步位置：本台账RUN-0017、`7gai_u0b_migration_rebuild_plan.md`第4章；实现入口为`internal/common/phase_one_schema.py`。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：共享空库入口和空SQLite退休表排除达到本地L1；完整外键闭包、空MySQL、快照迁移和生产验收未通过，U0B整体仍不得超过L1。

### RUN-0018 U0B-01模型仓库与训练Worker血缘去项目化

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第3章模型中心、第7章训练中心、第9章BM1688编译与真机验证、第15.3.5节去项目化；模型血缘`MODEL-U0B-01`、边端制品门禁`EDGE-BM1688-01`。
- 工作包/实施单元：WP2/U0B-01模型仓库、训练Worker结果落库；协同WP3边端质量门禁、WP5模型中心前端。
- 主责角色/协作角色/验收角色：R04后端主责；R03架构、R06模型算法、R07边端、R08前端协作；R09测试、R10数据库验收。
- 本轮目标：模型版本、外部模型登记、训练作业登记、Worker自动生成模型和模型列表不再要求项目编号；标注人员可登记、补充证据并发布自己创建的模型，管理人员可管理全部模型；模型发布后保留本平台下载记录，问题模型只能人工禁止下载，不引入回滚。
- 领域和接口变化：`ModelVersion`移除`project_id`外键、项目内版本唯一约束改为算法全局版本号并增加训练作业幂等约束；创建/外部登记命令拒绝旧项目字段；模型响应不再返回项目字段；`/model-versions`与`/models`列表按当前用户可见模型查询，已发布模型可全员查看；发布接口恢复为正常模型仓库能力，不再被已取消的设备控制面门禁拦截。
- 权限和血缘规则：管理人员可查看和管理全部；标注人员只能管理自己创建的模型，训练作业登记必须具备任务访问权；已发布模型允许全员查看/下载；模型必须关联冻结`DatasetVersion`、算法定义和训练作业（外部模型保留来源登记），父模型只能沿同一算法链复用；第三方任务的共享访问不授予数据集治理权。
- BM1688门禁：编译链仍按PT先导出ONNX，再由锁定版本TPU-MLIR生成FP32、FP16、INT8三份BM1688 BModel；转换门禁必须有READY ONNX，边端/发布门禁对BM1688必须同时检查三种精度BModel，三份制品属于同一模型版本，不拆成三个业务模型。
- Worker变化：`model_lineage`只要求`algorithm_id`和冻结`dataset_version_id`，移除项目一致性判断；父模型按算法一致性校验；模型键改为`算法标识:vN`；训练结果仍以训练作业唯一幂等，历史任务的项目字段不再作为新模型权限依据。
- 修改文件清单：`labelu/internal/domain/models/model_version.py`、`application/command/model_version.py`、`application/response/model_version.py`、`adapter/persistence/crud_model_version.py`、`adapter/routers/model_version.py`、`application/service/model_version.py`、`application/service/training_worker_persistence.py`；新增模型无项目契约专项测试`labelu/tests/internal/application/service/test_model_version_no_project.py`。
- 前端同步：训练页去项目化由R08完成，修改`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/training.tsx`及专项测试；训练目录、候选任务、汇总、作业列表和创建请求均不传项目编号，弹窗保持居中。
- 测试命令及结果：模型路由、无项目契约、Worker无项目血缘、空库迁移组合`python -m pytest labelu/tests/internal/adapter/routers/test_model_version.py labelu/tests/internal/application/service/test_model_version_no_project.py labelu/tests/alembic/test_empty_database_upgrade.py labelu/tests/alembic/test_phase_one_schema_scope.py -q --disable-warnings`为`14 passed`；训练页`pnpm exec vitest run src/pages/platform/pages/training.test.tsx --run`为`30 passed`；训练前端`pnpm run type-check`通过。自动训练Worker旧项目夹具仍需单独迁移，不把未迁移的历史测试当作新链路证据。
- 性能和并发限制：模型版本号仍需在第一段生产迁移中增加数据库级并发生成方案，当前`max+1`只做了算法维度收口，尚未完成20并发验证；列表查询使用创建者/任务协作者相关`EXISTS`和已发布全局可见，尚未完成大数据量MySQL执行计划压测。
- 迁移与环境限制：模型ORM项目外键已移除，但生产后迁移链仍有历史模型表结构，必须并入既定八段重建链，不能追加清理迁移；一期仍有21张表存在项目外键，空MySQL、生产快照副本升级、唯一head和迁移锁未完成。
- 本地、预生产和生产证据：本轮新增本地隔离SQLite/服务直调与前端单测；对生产`http://113.141.72.175:10000/health`、根页和`/openapi.json`做了未登录只读核验，均为HTTP 200，OpenAPI路由存在。未连接生产数据库、未执行生产写入、未运行真实TPU-MLIR、BM1688、SSH/板端验证、AICM发布或S3回流；HTTP 200和路由存在不代表端到端功能已验收。
- 风险和遗留项：旧前端模型中心、模型下载控制、三精度真实产物校验、版本号并发、生产迁移和真实Worker并发仍未完成；模型路由与Worker基础测试已在RUN-0020迁移到无项目契约，但不等于生产迁移完成；当前工作树不可提交、不可部署。
- 下一依赖与负责人：R04/R06补齐模型与Worker新契约测试、训练作业/数据版本真实血缘和并发版本号；R07补三精度BM1688真机证据；R08清理模型中心页面项目参数并实现发布记录/本平台下载列表；R03/R10将模型表字段变更纳入第一段迁移；R09完成组合回归和生产快照副本验收。
- LabelU应用版本恢复办法：本轮未发布；模型发布后不提供回滚，问题模型由管理人员或发布人员人工禁止后续下载并发布替代模型；本地失败采用前向修正，不降级生产数据库。
- 文档同步位置：本台账RUN-0018；迁移细节继续同步`7gai_u0b_migration_rebuild_plan.md`，文件归属同步基线清单。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：模型仓库新契约、权限方向、BM1688三精度门禁和训练Worker去项目化达到本地L1；旧测试迁移、并发、真实编译/真机、迁移链、预生产和生产验收未通过，U0B整体仍不得超过L1。

### RUN-0019 自动训练并发锁与旁路审计

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第7章训练中心、第8章自动训练、第15.8节并发性能；并发治理`CONC-U0B-02`。
- 工作包/实施单元：WP2/U0B自动训练调度、训练作业物化、Worker执行和Outbox旁路审计。
- 主责角色/协作角色/验收角色：R04后端主责；R03架构、R06算法、R07边端协作；R09并发测试、R10数据库验收。
- 审计范围：只读检查自动训练计划触发、自动轮次、训练作业物化、Outbox、调度租约、训练/BM1688/边端派发、Worker command幂等和周期扫描；未改代码、未连接生产、未执行生产写入。
- 已确认可靠部分：Outbox同一行的原子领取、租约和心跳基础逻辑可复用；单条事件不会被两个消费者同时认领。但不同幂等键表达同一业务动作时，现有实现没有业务级去重。已有相关单元回归共`47 passed`，只证明单行Outbox、基础fencing和部分边端状态契约，不证明真实副作用只执行一次。
- 必须修正的竞态：自动轮次是“先查签名、再按数量生成轮次号、再插入”，唯一冲突没有回读同一轮次；训练任务物化在插库前落清单文件，失败会留孤儿；训练、BM1688和边端派发直接读取`PENDING/QUEUED`后产生副作用，没有数据库条件抢占；Worker先查结果幂等键再执行，两个不同Outbox行可能同时执行同一command；同一逻辑重复事件会产生重试和死信噪声。训练队列`drain_db_pending_train_jobs`还会扫描全部`PENDING`作业直接补发，与自动派发器形成真实旁路；API进程启动的BM1688/边端自动恢复也主要依赖进程内锁，可能绕过数据库领取路径跨进程重复执行。
- 租约旁路：Watchdog只在周期开始取得30秒租约，后续回流、Outbox、到期计划和全表同步没有逐批续租/检查fencing token；一轮超过租约后，旧进程仍可能继续写，新进程已接管。
- 性能瓶颈：Watchdog对自动训练、编译、边端和发布对象做无分页全表扫描，并逐条N+1同步；业务量上升会拉长周期，进一步放大租约失效和重复派发风险。应按主键分页，每批不超过20至50条，并记录扫描耗时、批次大小和租约剩余时间。
- 下轮实现要求：每个业务动作增加数据库条件抢占状态（含DISPATCHING和fencing token）；唯一冲突按业务签名回读并返回原记录；manifest先写临时文件，事务成功后原子改名，失败清理；每批/每条副作用前续租并校验token；Worker增加command claim唯一表或等价原子抢占；训练队列drain只允许补发带过期lease且条件抢占成功的作业；BM1688/边端自动恢复也必须先条件UPDATE抢占恢复租约后执行，或仅处理人工作业；重复事件测试验证同签名只产生一条round/job/export/edge和一次真实execute调用。现有`queue_lease_owner/queue_lease_expires_at`字段尚未被派发写入和条件校验，不能视为跨进程租约。
- 验收设计：20并发同签名、20并发同command、双调度进程租约过期接管、派发失败重试、清单写入失败清理、分页扫描超时、进程重启恢复；断言数据库记录数、真实副作用调用次数、fencing拒绝和最终状态，而非只断言Outbox已处理。
- 测试结果：本轮只读审计补充运行既有自动训练/Worker/边端专项共`47 passed`；没有新增20并发测试，当前不能声明自动训练并发通过，也不能声明生产可用。
- 文档同步位置：本台账RUN-0019；实现计划并入U0B后续并发单元，不追加独立迁移。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：并发风险清单和验收标准达到L0审计；修正、20并发、预生产和生产验收均未完成，自动训练仍不得超过L0。

### RUN-0020 WP2训练Worker测试契约迁移到无项目一期链路

- 日期和时区：2026-09-17，Asia/Shanghai。
- 方案章节与需求编号：主方案第7章训练中心、第3章模型血缘、第15.3.5节去项目化；训练结果落库治理`MODEL-U0B-01A`。
- 工作包/实施单元：WP2/U0B-01模型仓库与训练Worker血缘。
- 主责角色/协作角色/验收角色：R04后端主责；R03架构、R06算法、R07边端协作；R09测试验收。
- 本轮目标：把训练Worker持久化专项测试从已取消的“跨项目数据集”语义迁移到一期真实契约：训练结果只能通过算法定义和冻结`DatasetVersion`生成`ModelVersion`；模型不再携带项目字段；未冻结版本只能记录延迟原因，不能生成模型。
- 明确不做：不修改生产数据，不重建迁移链，不新增项目兼容字段，不执行真实训练、TPU-MLIR、BM1688、AICM或S3链路，不提交、不推送、不部署。
- API、权限和数据契约变化：本轮仅更新测试夹具和断言，不新增外部接口；成功血缘夹具使用无项目`Dataset`、冻结数据版本、训练作业和算法定义；移除对`ModelVersion.project_id`的断言。原“跨项目数据集拒绝”用例改为“非冻结数据版本拒绝模型物化”，与一期数据版本门禁一致。
- 并发与性能：本轮没有改变运行时代码；模型物化仍依赖训练作业唯一约束和算法版本号生成，20并发版本号验证仍属于RUN-0019遗留项；测试未覆盖多进程抢占、租约续期和真实文件原子改名。
- 修改文件清单：`labelu/tests/internal/application/service/test_training_worker_persistence.py`、本交付台账。
- 测试命令及通过/失败/跳过数量：`python -m pytest labelu/tests/internal/application/service/test_training_worker_persistence.py labelu/tests/internal/application/service/test_training_worker_runtime.py -q --disable-warnings`，`22 passed`、零失败、零跳过；未出现新增错误。
- 本地、预生产和生产证据：仅隔离SQLite服务层测试；未启动生产服务，未连接生产数据库，未执行任何生产写入；没有预生产、GPU、TPU-MLIR、BM1688、SSH/板端、AICM或S3证据。
- 真实性限制：本轮只消除旧测试对项目字段的错误依赖，不能代表21张ORM项目外键已清零，也不能代表训练执行器已具备跨进程幂等；旧迁移链仍需从生产基线重建。
- 风险和遗留项：训练作业模型血缘的真实创建入口、算法维度版本号并发、manifest临时文件原子改名、Worker command条件抢占仍未实现或未验证；模型中心前端发布记录和本平台下载列表仍待R08完成。
- 下一依赖与负责人：R04/R03继续清理训练、任务、回流和工作流的项目外键；R04/R09实现20并发同签名和command claim测试；R07提供真实三精度BModel与BM1688证据；R08完成模型中心发布记录/下载列表。
- LabelU应用版本恢复办法：本轮未发布，无生产恢复动作；本地测试回退只需前向修正该专项测试，不使用模型回滚或生产数据库降级。
- 文档同步位置：本台账RUN-0020；模型迁移边界继续同步`7gai_u0b_migration_rebuild_plan.md`第3、4章。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：训练Worker无项目测试契约和本地专项回归达到L1；运行时代码并发、迁移、真实边端链路、预生产和生产验收未完成，WP2和平台整体不得超过L1。

### RUN-0021 WP3模型中心前端去项目化与发布目录

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第3章模型中心、第9章BM1688编译与发布、第10章平台内模型下载；前端契约`MODEL-FE-U0B-01`。
- 工作包/实施单元：WP3/U0B模型中心前端。
- 主责角色/协作角色/验收角色：R08前端主责；R04后端、R06模型、R07边端、R01产品协作；R09测试验收。
- 本轮目标：模型中心不再依赖项目选择器；标注人员可登记外部模型和发布自己有权限的模型，管理人员按后端权限管理全部；页面展示训练数据血缘、BM1688编译状态、发布记录，并提供平台保留模型列表与下载入口。
- 菜单、按钮和弹窗变化：模型中心请求统一使用`/v1/models`；新增“已发布模型”视图，展示平台模型ID、AICM模型集ID、芯片/精度、发布人、可用状态和下载按钮；模型详情的登记制品、高级检查和外部模型登记全部使用居中弹窗；发布按钮调用统一模型仓库发布接口，不再保留项目归属提示。
- 模型门禁：前端发布条件与一期一致，必须通过训练、泛化、编译、边端、发布五项检查，并同时具备READY的BM1688 FP32、FP16、INT8三种BModel；删除RKNN、BM1684、RK1808等非一期入口和文案。
- API与数据影响：模型列表不传`project_id`；外部模型登记请求不传项目字段，冻结数据集版本从无项目数据集列表加载；发布调用`POST /v1/model-versions/{id}/publish`；已发布列表调用`GET /v1/published-models`，下载地址调用`GET /v1/published-models/{model_id}/download-url`。未修改后端、迁移或生产数据。
- 并发和性能：模型列表和已发布列表均使用分页/固定大小；发布按钮使用React Query mutation防止重复点击，后端仍需保留发布幂等和唯一约束；平台下载按钮只在下载开关打开时可用，问题模型由人工禁止下载，不提供模型回滚。
- 修改文件清单：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/models.tsx`、`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/models.test.tsx`、本交付台账。
- 测试命令及通过/失败/跳过数量：`pnpm exec vitest run src/pages/platform/pages/models.test.tsx --run`，`10 passed`、零失败、零跳过；`pnpm run type-check`通过。测试输出包含既有React/Ant Design/jsdom警告，不影响断言。
- 本地、预生产和生产证据：仅本地Vitest和TypeScript检查；未启动浏览器做视觉验收，未联调真实模型列表、发布目录、AICM或下载制品，未连接生产和未执行生产写入。
- 真实性限制：前端已按后端现有接口收口，但后端模型发布权限、AICM发布记录、平台下载开关和真实三精度制品仍需跨角色联调；当前不能据此标记真实发布完成。
- 风险和遗留项：已发布列表下载地址依赖后端返回稳定`model_id`；真实AICM模型ID与本平台目录ID对账、下载禁用并发控制、发布重复请求和长列表执行计划仍待R04/R05/R09验证。
- 下一依赖与负责人：R04验证模型发布和下载权限；R06/R07提供真实FP32/FP16/INT8 BModel及BM1688真机证据；R05完成AICM发布记录/第三方回流对齐；R09执行浏览器、权限、重复点击和下载禁用测试。
- LabelU应用版本恢复办法：本轮未发布，无生产恢复动作；问题模型按要求人工禁止后续下载，不使用模型回滚或数据库降级。
- 文档同步位置：本台账RUN-0021；迁移与模型血缘继续同步`7gai_u0b_migration_rebuild_plan.md`第3、4章。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：模型中心前端去项目化、三精度门禁展示、发布目录入口和本地专项测试达到L1；真实后端/外部平台/边端/生产验收未完成，WP3和平台整体不得超过L1。

### RUN-0030 U0B迁移链现状与21张项目外键表机器校验

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：迁移重建方案第2、4、7章；迁移治理`MIG-U0B-002`。
- 工作包/实施单元：WP0/U0B八段迁移后缀准备。
- 主责角色/协作角色/验收角色：R03架构、R10数据库主责；R04/R05/R06/R07领域协作；R09测试验收。
- 本轮目标：重新核验生产基线与本地迁移图，把当前一期21张`project`外键残留表逐一分配到八段迁移，并用代码测试防止漏表、重复归属或迁移图事实漂移。
- 迁移现状：生产基线仍为`h7i8j9k0l1m2`；本地迁移图为130个唯一修订、一个base`54fee6a7ecd8`、唯一head`z3a4b5c6d7e8`；生产基线之后有37个未跟踪迁移文件。当前图无分叉，但37个迁移包含项目、设备发布、Agent、OTA等已取消方向，不能直接上线。
- 21张外键归属：第01段0张；第02段`yolo_train_job`；第03段`auto_training_plan`、`workflow_instance`；第04段`ingestion_job`、`media_asset`；第05段`inference_event`、`edge_hard_example`、`feedback_cluster`、`active_learning_batch`；第06段`provider_job`、`prediction_set`、`prediction_set_review_audit`、`prediction_set_projection`；第07段`rule_template`、`rule_binding`、`rule_binding_audit`、`rule_version_audit`；第08段`task`、`data_source`、`strategy_task_integration`、`third_party_call_audit`。
- 首段结论：数据集、数据版本和标签契约ORM已经无项目外键，首段新链应直接创建无项目结构。当前不能直接新增以生产基线为父版本的迁移，否则与现有37个迁移形成第二head；也不能挂在当前head之后，否则生产会先执行37个不可上线迁移。因此本轮不制造第38个清理迁移或伪首段。
- 实现内容：新增`phase_one_migration_plan.py`作为八段表级唯一归属；新增专项测试核对生产基线存在、当前唯一head和修订总数、21张闭包表完全覆盖、无重复分配、首段数据模型已经项目独立。
- 修改文件清单：`labelu/internal/common/phase_one_migration_plan.py`、`labelu/tests/alembic/test_phase_one_migration_plan.py`、`labelu/docs/7gai_u0b_migration_rebuild_plan.md`、本交付台账。
- 测试命令及通过/失败/跳过数量：`python -m pytest labelu/tests/alembic/test_phase_one_migration_plan.py labelu/tests/alembic/test_phase_one_schema_scope.py -q --disable-warnings`，`7 passed`、零失败、零跳过、115条既有弃用警告。
- 并发和性能：本轮只做迁移图与元数据集合校验，不执行DDL和业务查询；正式迁移仍必须使用短元数据锁等待、低峰在线索引、分批对账，禁止迁移内全表同步回填。
- 本地、预生产和生产证据：只读取本地迁移图和ORM元数据，运行隔离测试；生产版本事实沿用已确认的只读基线，未连接生产数据库、未执行升级/降级或写入。
- 真实性限制：机器校验证明“每张当前项目外键表都有唯一迁移归属”，不等于外键已移除；新八段文件、空MySQL和生产快照副本升级尚未完成。
- 风险和遗留项：37个旧文件必须在R03/R10独占窗口完成证据对照后整体替换；21张ORM字段和服务查询要按段同步修改，否则新库与运行时代码不一致；生产大表DDL锁与索引耗时未知。
- 下一依赖与负责人：R03/R10完成旧37个修订到新八段的逐对象DDL映射并生成正式新链；各领域角色按表级归属移除ORM/服务项目依赖；R09执行空MySQL、生产快照副本、唯一head和锁等待验收。
- LabelU应用版本恢复办法：本轮没有数据库写入，无生产恢复动作；正式升级失败时停止新写并从升级前快照恢复到独立实例后切换，不把数据库降级或模型回滚作为常规方案。
- 文档同步位置：本台账RUN-0022、`7gai_u0b_migration_rebuild_plan.md`第2.1至2.2节。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：迁移链事实、21张表唯一归属和自动校验达到L1准备状态；项目外键移除、新八段DDL、MySQL快照和生产验收均未完成，U0B整体不得超过L1。

### RUN-0031 训练队列自动作业过期租约抢占收口

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第7至8章训练/自动训练、第15.8节并发性能；并发治理`CONC-U0B-03`。
- 工作包/实施单元：WP2训练队列恢复旁路。
- 主责角色/协作角色/验收角色：R04后端主责；R03架构、R06算法协作；R09并发测试验收。
- 本轮目标：防止API进程训练队列看门狗绕过自动训练派发器，直接重复提交自动作业。
- 实现变化：`drain_db_pending_train_jobs`把人工作业和自动作业分开；自动作业只有在明确存在且已过期的数据库租约时才进入候选；提交前通过`PENDING + 自动血缘 + 租约已过期`条件更新抢占租约，失败者跳过；提交异常只由租约持有者清理；自动训练关闭时不恢复自动作业，人工作业保留重启恢复行为。新增专项覆盖无租约自动作业不补发、过期租约只抢占一次、人工作业正常恢复。
- 修改文件清单：`labelu/internal/application/service/yolo_train_queue.py`、`labelu/tests/internal/application/service/test_yolo_train_queue.py`、本交付台账。
- 测试命令及结果：`python -m pytest labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings`，`31 passed`、零失败、零跳过。
- 并发限制：本轮只收口训练队列恢复旁路；自动轮次/训练作业物化唯一冲突回读、Watchdog续租/fencing、BM1688和边端自动恢复跨进程抢占、Worker command claim、20并发真实副作用次数仍未完成。
- 本地、预生产和生产证据：只有隔离SQLite专项测试；未连接生产数据库、未执行生产写入、未运行真实训练或BM1688；不能声明自动训练并发闭环或生产完成。
- 下一依赖与负责人：R04继续统一自动轮次、训练、编译、边端和Worker claim；R09增加20并发和双进程租约过期测试；R10核对生产迁移实际唯一索引/租约字段。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：训练队列自动作业恢复旁路达到本地L1；自动训练完整并发、迁移、预生产和生产验收仍未通过。

### RUN-0032 自动训练队列重启补发租约门禁

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第7章训练中心、第8章自动训练、第15.8节并发性能；并发治理`CONC-U0B-03`。
- 工作包/实施单元：WP2/U0B自动训练队列旁路治理。
- 主责角色/协作角色/验收角色：R04后端并发主责；R03架构、R06算法协作；R09测试验收。
- 本轮目标：修正`drain_db_pending_train_jobs`重启扫描会绕过自动派发链直接提交自动训练作业的问题；人工训练作业保持原有重启恢复行为。
- 实现规则：自动训练作业只有在自动训练开关开启、明确存在已过期`queue_lease_expires_at`且数据库条件更新成功取得新的`queue_lease_owner`后才允许补发；无租约、租约未过期或其他进程先抢占的作业均跳过。提交异常时仅按本次租约所有者条件清理租约，避免误清理其他进程的租约。自动训练识别同时兼容`queue_source=AUTO`、计划编号和轮次编号，避免历史记录只写血缘字段时漏过门禁。
- 修改文件清单：`labelu/internal/application/service/yolo_train_queue.py`、`labelu/tests/internal/application/service/test_yolo_train_queue.py`、本交付台账。
- 新增测试：自动作业无租约不提交；已过期租约可条件抢占并提交，租约未过期的第二次扫描不会重复提交；人工重启恢复和用户暂停用例继续保留。
- 测试命令及结果：`python -m pytest labelu/tests/internal/application/service/test_yolo_train_queue.py -q --disable-warnings`，`31 passed`、零失败、零跳过；用全新隔离测试库执行自动训练Outbox、派发、调度租约、Worker Runtime和本文件组合回归，`62 passed`、零失败、零跳过。此前复用旧测试库时曾出现自动训练派发测试受`task.dataset_id`历史结构缺失影响，已确认属于既有测试库/迁移夹具问题，不归因于本轮队列改动；组合验收以全新隔离库结果为准。
- 并发与性能边界：本轮只把重启补发入口改为数据库条件抢占，未统一训练主派发、BM1688导出和边端自动恢复的租约领取；未完成20并发、双进程租约接管、真实训练Worker副作用次数和MySQL执行计划压测。`queue_lease_owner`和`queue_lease_expires_at`仍需在其他派发器统一消费。
- 本地、预生产和生产证据：仅隔离SQLite服务层专项测试；未连接生产数据库、未执行生产写入、未运行真实GPU/TPU-MLIR/BM1688/板端、AICM或S3链路；不把健康检查或路由200作为本轮验收证据。
- 风险和遗留项：当前迁移链仍未为新增租约/执行签名字段补齐生产唯一约束；自动派发主链及BM1688/边端自动恢复可能继续存在跨进程重复副作用；组合回归需先修复测试库结构与U0B迁移闭包后重跑。
- LabelU应用版本恢复办法：本轮未发布；本地失败采用前向修正，不使用模型回滚或生产数据库降级。已发布模型仍按方案由人工禁止后续下载，不提供模型回滚。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：训练队列重启旁路门禁和本地专项达到L1；统一派发租约、20并发、迁移、预生产和生产验收未完成，自动训练整体不得超过L1。

### RUN-0024 一期 BM1688 模型制品门禁和保留发布链收口

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第3章模型中心、第9章BM1688编译与真机验证、第10章平台内模型下载；边端范围`EDGE-BM1688-01`、发布权限`MODEL-PUBLISH-01`。
- 本轮目标：把后端模型制品范围与一期产品边界统一为YOLO + BM1688；PT、ONNX、FP32/FP16/INT8 BModel属于同一模型版本，禁止通过旧RKNN类型绕过门禁；保留模型发布接口，发布后的问题模型只允许管理人员关闭后续下载，不提供模型回滚。
- 代码变化：`CreateModelArtifactCommand`不再接受`rknn`；模型质量门禁的转换/边端/发布检查只接受`bmodel`；BM1688发布前仍必须同时存在READY的FP32、FP16、INT8三份BModel；错误提示统一为BM1688 BModel。历史AICM包内部的`output_rknn`仅作为外部平台兼容的包目录协议，不作为平台模型制品类型或一期芯片入口。
- 权限规则：模型版本发布接口仍允许模型创建者、任务创建者和有效协作者操作；已发布模型对全员可见；平台发布目录的展示、下载由`download_enabled`控制，关闭操作仍仅管理人员可执行。
- 测试命令及结果：`python -m pytest labelu/tests/internal/adapter/routers/test_model_version.py labelu/tests/internal/application/service/test_model_version_no_project.py labelu/tests/internal/adapter/routers/test_phase_one_retired_control_plane.py -q --disable-warnings`，`12 passed`、零失败、零跳过。
- 历史契约修正：移除测试中把`/model-versions/{id}/publish`当作已下线控制面的断言；一期下线的是项目、设备部署、OTA、规则设备签名和工作流控制面，模型发布属于保留主流程。
- 真实性限制：本轮仅完成本地服务契约和隔离SQLite测试；未执行真实TPU-MLIR、三份BModel生成、BM1688真机、AICM发布、平台下载或生产写入；不能据此声明边端和生产完成。
- 下一依赖与负责人：R07提供真实编译/真机证据；R04/R05完成模型版本与发布目录/AICM记录的真实绑定、下载禁止并发控制；R09执行浏览器和重复发布/下载测试；R10完成生产迁移索引与列对账。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：一期制品类型和保留发布权限达到本地L1；真实边端、AICM/S3回流、迁移、预生产、20并发和生产验收仍未通过。

### RUN-0025 数据集、模型中心、训练中心前端联合契约验收

- 日期和时区：2026-09-18，Asia/Shanghai。
- 工作包/角色：WP5前端；R08前端主责，R01产品、R04后端、R09测试协作验收。
- 验收范围：数据集、模型中心、训练中心三组页面的无项目请求、中文页面契约、居中弹窗、模型三精度门禁展示、平台发布目录入口和训练创建流程。
- 测试命令及结果：在`frontend/labelU-Kit/apps/frontend`执行`pnpm exec vitest run src/pages/platform/pages/datasets.test.tsx src/pages/platform/pages/models.test.tsx src/pages/platform/pages/training.test.tsx --run`，3个测试文件、`50 passed`、零失败、零跳过；`pnpm run type-check`通过。
- 警告与限制：输出存在React、Ant Design和jsdom既有警告，但不影响断言；本轮没有浏览器桌面/窄屏截图、真实后端接口、AICM、S3、TPU-MLIR或BM1688证据，不能标记为L2或生产验收。
- 工作树说明：相关页面包含当前未跟踪文件，后续获得“提交代码”授权时必须显式核对并纳入正确提交范围；未清理、恢复或覆盖其他既有改动。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确授权。
- L0/L1/L2/L3/L4结论：三菜单前端契约达到本地L1；浏览器视觉、真实API和生产闭环未完成。

### RUN-0028 自动训练看门狗续租、唯一竞态回读与清单临时文件清理

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第8章自动训练、第15.8节并发性能；并发治理`CONC-U0B-04`。
- 工作包/实施单元：WP2/U0B自动训练调度与冻结数据物化。
- 主责角色/协作角色/验收角色：R03架构、R04后端并发主责；R06算法协作；R09测试验收。
- 本轮目标：缩小自动训练看门狗租约过期后旧进程继续写入的窗口；对轮次和训练作业唯一冲突执行同业务回读；清单原子替换失败时清理临时文件。
- 实现内容：看门狗在发现、回流、Outbox、到期计划以及训练/BM1688/边端/发布逐项同步前续租并检查续租结果，fencing失效立即停止本轮；轮次创建和训练作业物化先锁定对应计划/轮次行，再做执行签名/轮次作业幂等回读，避免同一业务行被并发创建；`persist_round_manifest`在写入或原子替换失败时删除`.json.tmp`。
- 修改文件清单：`labelu/internal/application/service/auto_training_watchdog.py`、`labelu/internal/application/service/auto_training_scheduler.py`、`labelu/internal/application/service/auto_training_manifest.py`、`labelu/tests/internal/application/service/test_auto_training_watchdog.py`、`labelu/tests/internal/application/service/test_auto_training_reconciliation.py`、本交付台账。
- 测试命令及结果：看门狗和调度测试`python -m pytest labelu/tests/internal/application/service/test_auto_training_watchdog.py labelu/tests/internal/application/service/test_scheduler_lease.py -q --disable-warnings`，`6 passed`；轮次/清单/训练物化专项`python -m pytest ...test_auto_training_reconciliation.py::test_same_execution_signature_cannot_create_a_second_round ...::test_frozen_round_manifest_is_immutable_and_sha_verified ...::test_round_manifest_failed_replace_cleans_temporary_file ...::test_all_training_series_materialize_lineage_without_starting_gpu -q --disable-warnings`，`5 passed`；均零失败、零跳过。
- 并发与性能边界：当前只在服务层行锁和看门狗续租点收口，未完成真实双进程唯一冲突、20并发同签名、MySQL行锁执行计划和长周期租约压力测试；主训练派发、BM1688导出、边端恢复仍需统一业务状态条件抢占，不能据此宣称全链路幂等。
- 本地、预生产和生产证据：仅隔离SQLite测试；未连接生产数据库、未修改生产数据、未运行真实GPU/TPU-MLIR/BM1688/板端、AICM或S3链路。
- 风险和遗留项：轮次`round_no`在不同执行签名并发时仍依赖数据库唯一冲突后重试策略；训练作业异常冲突之外的孤儿文件/外部副作用需要Worker级command claim；迁移链新增唯一约束和MySQL快照升级尚未验收。
- LabelU应用版本恢复办法：本轮未发布；本地失败采用前向修正，不使用模型回滚或生产数据库降级。已发布模型仍按方案人工禁止后续下载，不提供回滚。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：看门狗续租、冻结轮次/训练物化唯一冲突回读和清单临时文件清理达到本地L1；全链路条件抢占、20并发、迁移、预生产和生产验收未完成，自动训练整体不得超过L1。

### RUN-0029 自动训练主派发条件租约领取

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第8章自动训练、第15.8节并发性能；并发治理`CONC-U0B-05`。
- 工作包/实施单元：WP2/U0B训练事件到执行器的派发入口。
- 主责角色/协作角色/验收角色：R04后端并发主责；R03架构、R06算法协作；R09测试验收。
- 本轮目标：消除自动训练派发在读取`PENDING`后直接调用训练副作用的跨进程竞态，同时保留非YOLO Worker命令的Outbox幂等重试语义。
- 实现内容：新增训练派发短租约条件更新，条件包含作业编号、`PENDING`状态、自动训练来源和计划编号；领取成功才调用训练提交或Worker命令生产，失败释放本次所有者租约；重复的非YOLO Worker命令先按确定性幂等键回读已存在Outbox事件，不重复产生事件。租约默认5分钟，派发器只负责短暂交接，训练Worker继续负责执行期状态和结果 fencing。
- 修改文件清单：`labelu/internal/application/service/auto_training_dispatch.py`、`labelu/tests/internal/application/service/test_auto_training_dispatch.py`、本交付台账。
- 测试命令及结果：`$env:LABELU_TEST_DB_NAME='dispatch-claim-20260918'; python -m pytest labelu/tests/internal/application/service/test_auto_training_dispatch.py -q --disable-warnings`，`8 passed`；随后补充提交失败释放租约用例，在全新隔离库`dispatch-fail-20260918b`执行为`9 passed`；与自动训练轮次、看门狗、调度租约和训练队列组合隔离回归`64 passed`；零失败、零跳过。
- 并发与性能边界：训练主派发已具备单作业条件领取，但尚未做真实双进程20并发副作用次数压测；BM1688导出模型目前没有`lease_owner/lease_expires_at`字段，边端验证仅有到期时间没有所有者，不能在本轮声称它们已完成同级条件抢占；需要先纳入U0B迁移链再实现。
- 本地、预生产和生产证据：仅隔离SQLite服务层测试；未连接生产数据库、未执行生产写入、未运行真实GPU/TPU-MLIR/BM1688/板端、AICM或S3链路。
- 风险和遗留项：训练作业状态在提交后仍由旧Worker路径更新，跨服务租约续期需要与Worker command claim统一；BM1688/边端自动恢复、迁移唯一约束和真实设备并发仍待后续工作。
- LabelU应用版本恢复办法：本轮未发布；本地失败采用前向修正，不使用模型回滚或生产数据库降级。已发布模型仍按方案人工禁止后续下载，不提供回滚。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：训练主派发条件租约和本地专项达到L1；BM1688/边端条件抢占、20并发、迁移、预生产和生产验收未完成，平台整体不得超过L1。

### RUN-0026 AICM 发布结果回写模型版本与平台目录

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第3章模型中心、第9章BM1688发布、第10章平台内模型下载；模型真源`MODEL-PUBLISH-02`。
- 工作包/实施单元：WP3模型中心发布闭环。
- 主责角色/协作角色/验收角色：R04后端主责；R05外部平台、R06模型算法、R07边端、R09测试协作验收。
- 本轮目标：AICM发布成功后，平台已发布目录与同一训练作业产生的`ModelVersion`必须指向同一发布事实；标注人员按任务权限发布模型，管理人员可关闭后续下载，不能通过两个页面产生两套模型真源。
- 实现内容：`register_published_model`在创建/更新平台发布目录后，按`yolo_train_job_id`查找唯一`ModelVersion`，写入`published_catalog_id`、`published_at`、`published_by`并将状态置为`PUBLISHED`；AICM异步发布任务传入当前用户编号；历史没有规范模型版本的发布记录仍保留目录，可由后续对账补链。训练Worker和模型制品登记同时移除旧`rknn`类型，保持一期BM1688制品白名单一致。
- 测试命令及结果：隔离测试库`LABELU_TEST_DB_NAME=model-catalog-link-20260918`执行`python -m pytest labelu/tests/internal/application/service/test_published_model_catalog.py labelu/tests/internal/application/service/test_model_version_no_project.py labelu/tests/internal/adapter/routers/test_phase_one_retired_control_plane.py -q --disable-warnings`，`23 passed`、零失败、零跳过；迁移计划与一期建表范围组合`7 passed`。
- 并发与性能边界：本轮验证的是发布结果回写和白名单契约；发布任务重复提交、目录唯一约束冲突回读、AICM网络重试和下载并发仍需在预生产真实外部平台验证；当前没有执行生产写入。
- 真实性限制：未连接真实AICM、未生成真实TPU-MLIR三精度BModel、未在BM1688设备运行、未执行平台实际下载或S3专家回流；不能标记为跨系统L2/L3。
- 下一依赖与负责人：R05完成真实AICM发布/重复请求/模型ID对账；R07提供三精度真机证据；R09补发布重复点击、下载禁用和权限矩阵；R10把`published_catalog_id`及唯一索引纳入八段迁移和生产快照对账。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：发布回写和一期制品范围达到本地L1；真实AICM、边端、并发、迁移、预生产和生产验收未完成。

### RUN-0027 角色开发交付基线与逐轮记录模板

- 日期和时区：2026-09-18，Asia/Shanghai。
- 产出文档：`labelu/docs/7gai_role_delivery_plan.md`。
- 目的：把主方案中的研发交付职责固定为R01至R10十个角色，明确每个角色负责的菜单/API/数据/执行器边界、L0-L4验收证据和当前未完成项；再次强调系统用户仍只有标注人员和管理人员。
- 记录要求：后续每轮必须同步日期、工作包、代码/页面/API/数据库影响、权限并发、测试命令与计数、本地/预生产/生产证据、风险依赖、提交部署状态和L0-L4结论；缺少真实环境证据只能写本地L1或“建设中”。
- 本轮状态：文档已创建，未修改生产、未提交、未推送、未部署；后续开发继续沿用本台账，不维护第二套角色口径。

### RUN-0033 全仓库回归与旧契约分层

- 日期和时区：2026-09-18，Asia/Shanghai。
- 验收角色：R09测试主责；R03架构、R01产品和各领域主责协作。
- 测试命令及结果：隔离库`LABELU_TEST_DB_NAME=full-suite-20260918`执行`python -m pytest labelu/tests -q --disable-warnings`，`2280 passed`、`63 failed`、`47 skipped`。
- 失败分层：多数失败来自仍要求创建项目、设备部署/Agent、设备规则签名、边端任务和旧多业务空间的历史测试；这些能力已经由一期方案明确下线，不能为了让旧测试通过而恢复项目/设备控制面。另有事件目录文件名在当前 Windows 代码页下解析失败、基准脚本读取`git status`时使用系统GBK导致解码失败、旧前端主题断言仍要求已移除的导航入口等独立问题，需要分别修复或迁移测试契约。
- 已确认的非回归专项：模型中心/训练/数据集前端`50 passed`且类型检查通过；自动训练并发组合`56 passed`；训练派发专项已更新为`9 passed`；模型发布、Worker和退休控制面组合`23 passed`；AICM辅助链`101 passed`；迁移计划与一期建表范围`7 passed`。
- 处理原则：保留失败原始输出，不把旧测试批量改绿；R09/R03先建立“保留主流程、已退休入口、独立工具问题”三类清单，再逐条迁移测试。只有新一期测试和真实环境证据通过，才能提升L等级。
- 真实性限制：本轮只执行本地隔离测试，没有生产写入、迁移、AICM、S3、TPU-MLIR或BM1688真机操作；全仓库结果不能替代生产验收。
- 下一依赖与负责人：R03/R09迁移旧测试契约并修复编码/事件目录；R07完成真实三精度和设备；R05完成S3/AICM；R10完成迁移副本、压测和恢复演练。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：专项本地L1通过；全仓库仍有旧契约和工具问题，平台不能提升到L2，更不能生产放行。

### RUN-0034 全量回归中的独立工具问题修复

- 日期和时区：2026-09-18，Asia/Shanghai。
- 工作包/角色：WP5生产放行；R09测试主责，R03架构协作。
- 修复内容：补齐`docs/系统方案文档/event_catalog.yaml`，与代码中的15个规范事件名逐项一致；基准脚本`_runtime_evidence`读取Git命令输出时显式使用UTF-8并替换非法字节，避免Windows系统代码页受脏工作树中文文件名影响。
- 测试命令及结果：`python -m pytest labelu/tests/internal/common/test_architecture_contracts.py::test_event_catalog_yaml_matches_code_catalog labelu/tests/scripts/test_benchmark_workspace_api.py::test_runtime_evidence_contains_no_token_and_has_script_hash -q --disable-warnings`，`2 passed`；自动训练看门狗与派发组合`15 passed`。
- 边界：未修改旧项目、设备部署、规则签名和OTA测试契约；这些失败仍表示已退休控制面测试未迁移，不作为恢复功能的理由。
- 真实性限制：只完成本地工具和契约测试，没有生产写入或外部平台/设备验证。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：独立工具问题达到L1；旧契约迁移、真实链路和生产放行仍未完成。

### RUN-0035 已发布模型下载开关的并发冲突保护

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第10章平台内模型下载、第15.8节并发性能；下载控制`MODEL-DOWNLOAD-CAS-01`。
- 工作包/角色：WP3模型中心；R04后端主责，R08前端、R09测试协作。
- 本轮目标：两个管理人员同时处理问题模型时，后提交者不能静默覆盖先提交者的下载开关；继续遵守“禁止后续下载 + 发布替代版本”，不增加模型回滚。
- 实现内容：`UpdatePublishedModelCommand`增加可选`expected_download_enabled`；提供数据库条件更新函数，只有数据库当前值仍等于客户端期望值才修改，冲突返回409并提示刷新；成功变更写结构化业务日志`published_model.download_switch.changed`。不传期望值时保持兼容旧调用，但新前端必须带当前值。
- 测试命令及结果：隔离库`LABELU_TEST_DB_NAME=download-cas-20260918`执行`python -m pytest labelu/tests/internal/adapter/routers/test_published_model_catalog.py -q --disable-warnings`，`4 passed`、零失败、零跳过。
- 性能和安全边界：单条条件更新避免先读后写覆盖；尚未完成MySQL执行计划、真实双进程下载开关竞态、审计持久化表和浏览器联调；日志不替代后续正式审计表。
- 真实性限制：没有生产写入、没有真实AICM/S3/BM1688操作；只能记为本地L1。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：下载开关本地条件并发保护达到L1；生产审计、并发压测、真实外部链路仍未完成。

### RUN-0037 八段迁移重建离线闸门与候选工作区校验

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：迁移重建方案第2.3节、验收条件1至4；迁移治理`MIG-U0B-003`。
- 工作包/角色：WP0/U0B迁移后缀重建；R03架构、R10数据库主责，R09测试验收。
- 本轮目标：在不修改正式迁移目录、不产生第二head的前提下，建立旧37修订逐对象映射完整性检查和八段候选迁移目录生成前闸门。
- 旧后缀映射：生产基线后37个修订全部显式登记处理决定和对象；25个由八段新链替代，12个项目、部署、Agent/OTA、组织场景、设备签名、模型优化等已取消方向整体排除。新增旧修订、漏登记、空对象映射、非法段号或排除项绑定替代段都会审计失败。
- 候选工作区约束：候选迁移只能在正式`versions`之外生成；必须精确包含`gai_p1_01`至`gai_p1_08`八个迁移，第一段父版本固定为`h7i8j9k0l1m2`，其余逐段单父串联，拒绝第二根节点、合并元组父版本和动态父版本，唯一候选head为`gai_p1_08`。
- 安全实现：审计器用Alembic只读迁移图核对现有后缀，用Python AST读取候选文件的`revision/down_revision`，不导入候选迁移、不调用`upgrade/downgrade`、不创建数据库连接。命令入口为`python -m labelu.scripts.audit_phase_one_migration_rebuild [--replacement-versions <隔离目录>]`。
- 修改文件清单：`labelu/internal/common/phase_one_migration_rebuild_audit.py`、`labelu/scripts/audit_phase_one_migration_rebuild.py`、`labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py`、`labelu/docs/7gai_u0b_migration_rebuild_plan.md`、本交付台账。
- 测试命令及结果：迁移审计、八段归属和一期建表范围组合`python -m pytest labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py labelu/tests/alembic/test_phase_one_migration_plan.py labelu/tests/alembic/test_phase_one_schema_scope.py -q --disable-warnings`，`11 passed`、零失败、零跳过；命令行只读审计成功输出生产基线、当前head、37个修订、25个替代和12个排除。
- 本地、预生产和生产证据：只有本地文件图、AST和隔离临时目录测试；未连接数据库，未执行DDL，未改正式37个迁移，未生成正式候选迁移，未连接生产。
- 性能与并发：离线审计只扫描37个迁移元数据和8个候选文件，不进入应用请求链；正式DDL锁等待、在线索引耗时、双实例迁移锁和生产快照恢复仍需后续环境验收。
- 真实性限制：闸门证明映射和候选链结构完整，不证明八段DDL内容正确，也不证明SQLite/MySQL和生产快照能升级；候选迁移必须继续经过对象DDL对账和三类数据库演练。
- 下一依赖与负责人：R03/R10在`.tmp/phase-one-rebuild/versions`生成候选八段并先过本闸门；R09随后执行空SQLite、空MySQL、生产快照副本、唯一head和结构/行数对账；全部通过前正式`versions`目录保持不变。
- LabelU应用版本恢复办法：本轮未修改数据库和正式迁移链，无恢复动作；候选失败直接丢弃隔离工作区并前向修正，不使用生产降级或模型回滚。
- commit/push/deploy状态：未提交、未推送、未部署，无生产写入；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：旧修订对象级映射和候选链离线结构闸门达到本地L1；八段DDL、空MySQL、快照副本和生产验收未完成，U0B整体不得超过L1。

### RUN-0036 前端主题契约迁移与任务卡去项目入口收口

- 日期和时区：2026-09-18，Asia/Shanghai。
- 工作包/角色：WP1任务与标注、WP5前端验收；R08前端主责，R01产品、R03架构、R09测试协作验收。
- 本轮目标：处理全量回归中`test_frontend_theme_tokens.py`的两项失败，按六个一级菜单、无项目/业务空间、无设备发布/回滚入口的一期方案迁移陈旧契约，同时核对任务卡徽标可读性和训练入口。
- 分析结论：导航失败由旧测试仍要求`deployments`和项目上下文导致，不能恢复已取消入口；任务卡草稿徽标原硬编码前景/背景已有可读性，但未复用统一主题令牌，同时任务卡真实残留“必须绑定项目才能训练”、`project_id`跳转参数和“未归属项目”提示，属于无项目主流程缺陷。
- 实现内容：草稿徽标统一使用高对比`--color-warning-active`和`--color-warning-bg`；图像任务在标注完成且训练就绪时即可进入训练，不再检查项目编号；训练跳转仅携带任务编号；移除任务卡“未归属项目”提示。静态契约改为精确验证“工作台、任务与标注、训练中心、模型中心、数据回流、系统管理”六个一级菜单，并反向断言没有部署/工作流路由和任务卡项目参数。
- 修改文件清单：`frontend/labelU-Kit/apps/frontend/src/pages/tasks/components/taskCard/index.tsx`、`labelu/tests/test_frontend_theme_tokens.py`、本交付台账。工作树中这些文件原有其他修改均保留，本轮未清理、恢复或覆盖无关变化。
- 测试命令及结果：`python -m pytest labelu/tests/test_frontend_theme_tokens.py -q --disable-warnings`为`9 passed`；`pnpm exec vitest run src/pages/platform/modules.test.ts --run`为`6 passed`；`pnpm exec vitest run src/pages/tasks/components/taskCard/taskNavigation.test.ts --run`为`3 passed`；`pnpm run type-check`通过；`git diff --check`无空白错误。均零失败、零跳过，Python仍报告112条既有弃用警告。
- 性能和并发边界：本轮只修正纯前端条件和路由参数，不新增请求、轮询、列表加载或共享状态，不改变训练作业服务端并发控制；未执行并发压测。
- 真实性限制：当前会话未提供Browser技能，且本轮没有启动完整页面做桌面/窄屏截图或真实交互；没有预生产/生产写入，没有真实训练、AICM、S3、TPU-MLIR或BM1688证据，不能据此标记L2或生产通过。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：主题与六菜单/任务卡无项目契约达到本地L1；浏览器视觉、真实接口和生产闭环仍未完成。

### RUN-0038 全量失败旧契约机器可读迁移清单

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第1.12节、第15.8节；角色交付基线第4节；迁移治理`MIG-U0B-003`。
- 工作包/角色：WP5全量回归与旧契约迁移；R09测试主责，R01产品、R02数据契约、R03架构、R04后端、R05外部回流、R07边端、R08前端协作。
- 本轮目标：基于隔离全量回归的61个失败节点，生成机器可读迁移清单，区分已退休项目/设备控制面、一期主流程旧契约和独立实现缺陷；不得通过恢复已取消能力使旧测试变绿。
- 全量基线：`LABELU_TEST_DB_NAME=full-suite-classification-20260918 python -m pytest labelu/tests -q --disable-warnings --tb=short`，`2286 passed`、`61 failed`、`47 skipped`；原始日志为`.tmp/full-suite-classification-20260918.log`。
- 交付物：新增`labelu/docs/7gai_legacy_test_contract_migration.json`，逐测试文件记录测试名、分类、责任角色、失败原因和迁移动作；新增`labelu/tests/internal/common/test_legacy_test_contract_migration.py`，校验JSON结构、失败节点总数、类别白名单、责任角色、测试文件存在性、节点不重复和分类计数。
- 分类结果：`retired_project_control_plane` 26项（项目创建、设备任务/规则签名、设备直连回流等一期明确下线能力）；`phase_one_contract_to_migrate` 34项（主动学习、S3/Provider、自动训练、迁移、Worker血缘及旧前端项目入口）；`independent_defect` 1项（无项目测试中已完成样本仍返回`training_ready=false`）。
- 关键处理原则：项目/业务空间、设备发布、Agent/OTA、设备规则签名和设备直连回流不得恢复；S3问题图片仍以第三方策略系统对象包为唯一一期来源；Provider和训练测试迁移到任务、标签契约、冻结数据版本、AICM模型编号和执行器租约边界。
- 测试命令及结果：`python -m pytest labelu/tests/internal/common/test_legacy_test_contract_migration.py -q --disable-warnings`，`2 passed`、零失败、零跳过。
- 并发与性能边界：本轮只做失败证据归档和静态清单校验，不改请求路径、不改数据库、不新增锁；不能替代后续旧契约迁移后的并发、外部S3/AICM、TPU-MLIR/BM1688和生产验证。
- 本地、预生产和生产证据：仅隔离SQLite全量回归和本地JSON校验；未连接生产、未执行迁移DDL、未产生生产写入，未恢复任何旧控制面能力。
- 下一依赖与负责人：R09/R03迁移测试断言；R02/R04修复训练候选`training_ready`独立缺陷；R05迁移S3/Provider夹具；R07隔离设备旧测试并补真机验证契约；迁移完成后重新执行全量回归并保留新日志。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：机器可读分类和校验达到本地L1；旧契约迁移、独立缺陷修复、真实外部链路、设备验证、并发压测和生产放行仍未完成。

### RUN-0039 训练候选标注完成与可训练媒体状态拆分

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第2章任务与标注、第5章训练中心；角色交付基线R02/R04/R09。
- 工作包/角色：WP1训练候选契约；R04后端主责，R02数据契约、R09测试协作。
- 根因：`list_training_candidates_by_labels`先过滤没有附件的DONE样本，再把剩余数量用于`training_ready`。历史或测试样本可能已经完成标注但媒体附件缺失，造成任务级标注完成状态被错误降为`false`；同时训练快照确实不能使用缺失媒体，因此不能简单取消媒体门禁。
- 实现内容：候选统计新增`annotation_ready`输入；`training_ready`改由任务全量样本的标注/几何审计决定，`can_train`、可训练样本数和缺失媒体阻断原因仍由带附件且通过复核的样本决定。任务维度和标签维度两个候选入口统一使用该语义。专项夹具补充图像宽高，明确几何审计所需的最低标注证据。
- 修改文件清单：`labelu/internal/application/service/task.py`、`labelu/tests/internal/adapter/routers/test_yolo_train_eligible_tasks.py`、本交付台账。
- 测试命令及结果：隔离库`training-ready-regression-20260918c`执行单项回归`1 passed`；隔离库`training-ready-regression-suite-20260918`执行`python -m pytest labelu/tests/internal/adapter/routers/test_yolo_train_eligible_tasks.py -q --disable-warnings`，`5 passed`、零失败、零跳过。
- 权限与并发：本轮只调整候选响应计算，不改变任务可见性、创建训练权限、冻结快照或租约；没有新增请求、锁或共享状态。
- 本地、预生产和生产证据：仅隔离SQLite专项测试；未连接生产、未写生产数据，未运行真实训练、S3、AICM、TPU-MLIR或BM1688。
- 风险和下一依赖：全量回归需重新执行以确认该1项独立失败消失；真实训练创建仍必须再次校验附件、文件摘要和冻结数据版本，不能仅凭`training_ready=true`放行。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：训练候选状态拆分达到本地L1；全链路训练、执行器并发、外部链路和生产验收仍未完成。

### RUN-0040 模型目录详情改为居中弹窗并接入下载开关并发校验

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第3章模型中心、第10章平台内模型下载、第12章前端交互；交互契约`UI-MODAL-01`，下载并发契约`MODEL-DOWNLOAD-CAS-01`。
- 工作包/角色：WP3模型中心与WP5前端验收；R08前端主责，R04后端协作，R09测试验收。
- 本轮目标：消除模型目录右侧抽屉，统一使用居中弹窗；让页面实际携带后端要求的`expected_download_enabled`，防止两个管理人员同时修改下载开关时静默覆盖。
- 实现内容：`frontend/extensions/pages/yolo_model_catalog.html`将详情、标签、模型包和目录编辑改为带遮罩的居中弹窗，保留单层弹窗、窄屏内部滚动和关闭行为；下载开关和模型保存请求均携带当前下载状态，收到409时保留后端冲突提示并要求刷新；页面品牌和算法展示补充中文“7GAI 进化平台”“目标检测”。
- 测试命令及结果：`python -m pytest labelu/tests/e2e/test_yolo_model_catalog_static.py -q --disable-warnings`，`2 passed`；`node --test frontend/extensions/tests/third_party_calls_console.test.mjs`，`1 passed`。模型目录 Playwright 测试已同步选择器，但当前未设置`LABELU_E2E_BASE`，未执行浏览器联调。
- 并发与性能边界：本轮只接入既有单条条件更新，不改变数据库索引、下载文件流或接口分页；尚未完成真实双进程409竞态、MySQL执行计划和5/10/20/50并发下载压测。
- 本地、预生产和生产证据：仅静态页面和Node结构测试；未连接生产、未执行AICM/S3、未执行真实模型下载，未产生生产写入。
- 风险与下一依赖：后端模型目录编辑路由仍按管理人员权限保护；标注人员发布模型的权限应在AICM发布路由按任务权限验收，不能通过模型目录管理接口放宽；剩余旧页面抽屉需要按同一`UI-MODAL-01`逐页迁移。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：模型目录弹窗和前端CAS契约达到本地L1；浏览器真机、并发、生产链路和全站抽屉清理仍未完成。

### RUN-0041 BM1688编译与边端验证自动派发条件租约

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第15.8节并发性能；并发治理`CONC-U0B-05`。
- 工作包/角色：WP2/U0B自动执行器交接；R04后端并发主责，R07 BM1688边端、R03架构、R09测试协作。
- 本轮目标：训练派发已有短租约后，补齐BM1688编译和边端验证自动派发的条件领取，避免两个调度器同时启动同一导出/验证副作用；失败时释放交接租约。
- 实现内容：`YoloBm1688Export`新增`queue_lease_owner/queue_lease_expires_at`；`YoloEdgeValidationJob`新增同名派发租约，和已有板端执行`lease_expires_at`分离。`auto_training_dispatch`新增BM1688/边端条件UPDATE领取、5分钟短租约、重复领取拒绝和提交异常释放；边端保留原有打包/调度逻辑，实际启动前才完成原子领取。
- 兼容与迁移边界：本轮更新ORM、测试夹具和本地SQLite建表/旧测试库补列；正式八段迁移目录尚未接入这两组字段，不能部署到生产。R03/R10必须将字段、索引和历史NULL兼容纳入候选迁移并完成空MySQL与生产快照演练后才能上线。
- 修改文件清单：`labelu/internal/domain/models/yolo_bm1688_export.py`、`labelu/internal/domain/models/yolo_edge_validation_job.py`、`labelu/internal/application/service/auto_training_dispatch.py`、`labelu/tests/conftest.py`、`labelu/tests/internal/application/service/test_auto_training_dispatch.py`、`labelu/docs/7gai_u0b_migration_rebuild_plan.md`、本交付台账。
- 测试命令及结果：隔离库`bm1688-edge-dispatch-lease-20260918b`执行派发专项`11 passed`；隔离库`bm1688-edge-dispatch-combo-20260918`执行BM1688自动链、板端保留路由和编译流程组合`24 passed`；三个修改模块`py_compile`通过。
- 并发与性能边界：条件UPDATE在单库层保证同一活动租约只有一个领取者；本轮尚未执行真实双进程竞争、MySQL执行计划、20并发副作用次数和长任务租约续期。派发租约只覆盖调度交接，Worker/板端执行期仍需各自的状态fencing和续租证据。
- 本地、预生产和生产证据：仅隔离SQLite和模拟提交函数；未连接真实TPU-MLIR、BM1688、AICM、S3或生产数据库，未产生生产写入。
- 下一依赖与负责人：R03/R10纳入八段迁移；R07补真实设备执行期续租/失联接管；R09执行双进程20并发和失败重试；生产放行前不得把本地11/24通过写成真实编译或真机通过。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：BM1688/边端自动派发条件领取达到本地L1；字段迁移、执行期fencing、真实设备、并发压测、预生产和生产验收未完成，平台整体仍不得超过L1。

### RUN-0042 第三方调用中心详情抽屉改为居中弹窗

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第6章数据回流、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP4数据回流与第三方调用；R05外部回流主责，R08前端实现，R09测试验收。
- 本轮目标：将第三方调用中心的调用详情从右侧抽屉统一改为居中弹窗，保持原有调用记录查询、详情展示和关闭行为不变，并统一页面中文品牌文案。
- 实现内容：`frontend/extensions/pages/third_party_calls.html`将详情容器、遮罩、关闭按钮和脚本调用统一为`detailModal`/`modal-*`命名；支持关闭按钮、点击遮罩、`Esc`关闭、窄屏内部滚动；顶部品牌改为“7GAI 进化平台 / 第三方调用中心”。
- 测试命令及结果：`node --test frontend/extensions/tests/third_party_calls_console.test.mjs`，`1 passed`、零失败、零跳过；`rg -n -i "drawer|detailDrawer|drawerBackdrop" frontend/extensions/pages/third_party_calls.html`无匹配。静态HTML语法检查未执行，不能作为通过证据。
- 并发与性能边界：本轮只调整展示层容器和事件绑定，不改变第三方调用查询接口、分页、S3导入、任务分流或执行器租约；未执行浏览器真机、并发压测和生产链路验证。
- 本地、预生产和生产证据：仅本地Node结构测试和文本残留检查；未连接生产、未写生产数据、未执行真实S3/AICM链路。
- 风险与下一依赖：`annotation_settings.html`仍有多组真实抽屉，需要按同一弹窗契约逐项迁移；完成后还需做浏览器桌面/窄屏交互验收。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：第三方调用中心弹窗结构达到本地L1；全站抽屉清理、浏览器视觉、外部回流和生产验收仍未完成。

### RUN-0043 标注设置六类编辑表单统一改为居中弹窗

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第2章任务与标注、第7章系统管理、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP1任务与标注、WP5前端验收；R08前端主责，R01产品、R02数据契约、R09测试协作。
- 本轮目标：将标签、数据集分类、第三方标签别名、运行模板、预设、全局维度六类编辑表单从右侧抽屉统一迁移为居中弹窗；保留原有字段、校验、保存接口和权限边界。
- 实现内容：`frontend/extensions/pages/annotation_settings.html`统一使用`modalBackdrop`、`modal-panel`、`modal-head`、`modal-body`、`modal-foot`；弹窗居中、带遮罩、支持关闭按钮、点击遮罩、`Esc`关闭、窄屏内部滚动；所有打开/关闭函数和表单标题引用同步切换为弹窗 ID；未改变标签、分类、别名、运行模板、预设和维度的后端接口。
- 测试命令及结果：Node `vm.Script` 对内嵌脚本解析通过，输出`js-parse-ok`；Python `HTMLParser` 解析通过，输出`html-parser-ok`；`rg` 检查页面无`drawer`、`data-close-drawer`、`openDrawer`、`closeDrawer`残留；`git diff --check`无空白错误。
- 并发与性能边界：本轮只调整前端容器和事件绑定，不新增 API 请求、轮询、数据库锁或共享状态；未执行浏览器桌面/窄屏真机、表单接口联调和并发压测。
- 本地、预生产和生产证据：仅本地静态解析和残留扫描；未连接生产、未写生产数据、未执行AICM/S3/BM1688链路。
- 风险与下一依赖：需要补充浏览器真实点击六类“新建/编辑/保存/取消”路径，确认弹窗打开后字段仍可编辑；全站其他页面仍需继续清理旧抽屉。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：标注设置弹窗结构达到本地L1；浏览器视觉、接口联调、外部链路和生产验收仍未完成。

### RUN-0044 自动恢复路径接入数据库条件租约

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第15.8节并发性能；并发治理`CONC-U0B-05`。
- 工作包/角色：WP2/U0B自动执行器恢复；R04后端并发主责，R03架构、R07 BM1688边端、R09测试协作。
- 本轮目标：修复部署重启后自动恢复旁路直接起线程的问题，保证多进程/多实例只有一个自动恢复调度器获得恢复权；手工作业继续保留兼容恢复路径。
- 分析结论：正常自动派发已经有`queue_lease_owner/queue_lease_expires_at`，但`yolo_bm1688_export_auto_resume.py`和`yolo_edge_validation_auto_resume.py`在检查到`PROCESSING`或策略待处理后直接启动线程，跨进程无法互斥。恢复租约必须和边端执行租约`lease_expires_at`分离，且只针对`auto_training_round_id`非空、`pipeline_origin`为`AUTO_QUALITY`或`MANUAL_DIAGNOSTIC`的自动流水线。
- 实现内容：`auto_training_dispatch.py`新增BM1688导出和边端验证恢复的条件UPDATE领取/释放函数，使用5分钟短租约；BM1688自动恢复在解析ONNX后领取租约再启动编译线程，线程结束或启动失败时按所有者释放；边端自动恢复在确认`PROCESSING`已过期或策略待处理后领取租约再启动执行，结束或启动失败时释放；手工记录和一期关闭自动计划的记录不经过新租约。设备实际执行租约保持独立，未把调度交接租约当作板端租约。
- 修改文件清单：`labelu/internal/application/service/auto_training_dispatch.py`、`labelu/internal/application/service/yolo_bm1688_export_auto_resume.py`、`labelu/internal/application/service/yolo_edge_validation_auto_resume.py`、`labelu/tests/internal/application/service/test_auto_training_dispatch.py`、本交付台账。
- 测试命令及结果：恢复与派发专项`python -m pytest labelu/tests/internal/application/service/test_auto_training_dispatch.py labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py -q --disable-warnings --tb=short`，`50 passed`、零失败、零跳过；自动训练组合专项再加入`test_auto_training_bm1688_edge_flow.py`和`test_auto_training_watchdog.py`后为`72 passed`、零失败、零跳过；三个修改服务模块`python -m py_compile`通过。
- 并发与性能边界：SQLite真实条件UPDATE测试覆盖同一记录单次领取、重复领取拒绝、释放后再次领取；尚未完成双进程/双Session真实竞争、MySQL执行计划、恢复线程执行期续租和fencing、20并发恢复压测。5分钟是调度交接租约，不代表编译或板端任务最长执行时间。
- 本地、预生产和生产证据：仅隔离SQLite与模拟线程测试；未连接真实TPU-MLIR、BM1688设备、AICM、第三方S3或生产数据库，未执行DDL，未产生生产写入。
- 风险与下一依赖：正式八段迁移仍未加入四个租约字段及索引，不能部署生产；需要R03/R10先生成候选迁移并完成空SQLite、空MySQL、生产快照副本演练，再由R07补执行期租约续期/失联接管，R09补双进程并发证据。生产重启恢复在这些证据完成前保持关闭或观察模式。
- LabelU应用版本恢复办法：本轮只改应用代码和隔离测试，未改正式数据库；若本地验证失败，回退本轮代码改动即可，不涉及模型下载、已发布模型或生产数据回滚。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：自动恢复条件领取和本地单库竞争达到L1；正式迁移、双进程/MySQL、执行期fencing、真实设备、预生产和生产验收未完成，平台整体仍不得超过L1。

### RUN-0045 BM1688三制品质量门禁统一为版本化规则

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第10章模型发布、第15.8节并发和质量门禁；规则契约`BM1688-QUALITY-RULE-01`。
- 工作包/角色：WP2模型编译与评估；R06模型算法、R07边端、R03架构、R09测试协作。
- 生产问题证据：只读生产页面当前约有65条导出记录、22条阻断、36条可进入发布评估；已见INT8 mAP50绝对掉点约0.0309超过服务端0.020，另有边端脚本退出失败和PT/BModel同口径证据缺失。原边端脚本默认0.05且只检查INT8，与服务端规则不一致。
- 实现内容：新增`labelu/internal/common/bm1688_quality_rules.py`，定义`bm1688-yolo11-detection-v1`唯一默认规则；服务端转换门禁携带同版本质量规则快照；边端验证任务将规则快照写入`run_config.json`；边端通用三制品门禁固定FP32为基线，分别检查FP16和INT8掉点，默认INT8上限从0.05收紧为0.020，并输出规则版本、基线制品和各精度掉点；车牌专用FP16路径保持独立，不套用通用三制品门禁。
- 规则含义：PT→ONNX检查导出/后处理回归，PT→FP32检查编译保真，FP32→FP16检查半精度变换，FP32→INT8检查量化误差；三种BModel同批留存用于验收和问题定位，发布时只选择一个已通过门禁的目标精度，不要求设备同时部署三份。
- 测试命令及结果：`python -m pytest labelu/tests/internal/common/test_bm1688_quality_rules.py labelu/tests/scripts/test_edge_board_validate_runner_script.py labelu/tests/internal/application/test_yolo_sop_model_evaluation.py -q --disable-warnings`，`31 passed`、零失败、零跳过；前序编译/边端组合专项`42 passed`；相关模块语法检查通过，`git diff --check`无空白错误。
- 并发与性能边界：本轮只统一规则快照和评估判定，不改编译线程模型、设备并发、运行时性能指标或生产阈值数据；尚未完成关键类别/场景分层、数值一致性、P50/P95/P99、温度/功耗、长稳和真实设备证据。
- 本地、预生产和生产证据：仅本地规则单测、边端脚本单测和生产只读页面快照；未连接真实TPU-MLIR/BM1688/AICM/S3，未写生产数据。
- 风险与下一依赖：历史运行包若只有旧`max_int8_map50_drop`字段会被标记为规则配置异常，不再参与发布判定，必须重新打包版本化规则快照；后续仍需由编译任务冻结模型族/输入尺寸/校准集/前后处理/编译器版本。正式八段迁移、Compiler Worker、Edge Worker和环境快照仍未完成。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：版本化门禁和本地一致性达到L1；生产已有掉点问题未修复，真实三制品/真机/性能证据和生产放行仍未完成，平台整体不得超过L1。

### RUN-0046 AICM发布环境详情改为居中弹窗并同步标注设置E2E契约

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第3章模型中心、第7章系统管理、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP3模型发布环境、WP1标注设置、WP5前端验收；R08前端主责，R04后端协作，R09测试验收。
- 实现内容：`frontend/extensions/pages/aicm_publish_environments.html`将环境详情从右侧抽屉改为带遮罩的居中详情弹窗，保留查看、设为当前、编辑、遮罩关闭和关闭按钮；同步更新`labelu/tests/e2e/test_annotation_settings_frontend.py`，将标签和运行模板旧`drawer-*`选择器改为`modal-*`，并增加打开状态和Esc关闭断言。
- 测试命令及结果：AICM页面内嵌脚本`vm.Script`解析通过，输出`aicm-js-parse-ok`；页面旧抽屉标识扫描无匹配；标注设置E2E在未设置`LABELU_E2E_BASE`时为`3 skipped`，未冒充浏览器通过；此前Node/HTML静态解析仍通过。
- 并发与性能边界：本轮只改展示容器和E2E选择器，不改AICM环境接口、密码加密、发布请求或数据库并发；未执行浏览器桌面/窄屏视觉联调。
- 本地、预生产和生产证据：只完成本地脚本解析、静态扫描和未配置环境下的跳过结果；未连接生产、未写AICM/S3或数据库。
- 风险与下一依赖：`yolo_training.html`仍存在以`drawer-panel`命名的历史模态样式；需要确认其实际均为居中模态后再做命名清理，不能误改训练向导。设备注册页面属于一期退休设备控制面，不恢复其业务入口。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：AICM详情弹窗和标注设置E2E契约达到本地L1；浏览器真机、真实发布环境和生产验收仍未完成。

### RUN-0047 BM1688文档唯一真源入口和门禁版本指引

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9.13节、第15章；文档治理`DOC-BM1688-SOURCE-01`。
- 工作包/角色：WP0方案与基线；R01产品、R03架构、R06模型算法、R07边端协作。
- 本轮目标：解决研发技能扫描所发现的`docs/04-边端验证与BM1688/`入口缺失问题，同时避免复制出第二套BM1688规则。
- 实现内容：新增`docs/04-边端验证与BM1688/README.md`，只指向主方案第9章/9.13节、角色交付基线和交付台账；明确唯一生效规则版本`bm1688-yolo11-detection-v1`、编译节点与BM1688职责边界、三制品留存与单精度发布选择。
- 测试命令及结果：文档链接按仓库相对路径检查，目标文件均存在；未执行生产或数据库操作。
- 并发与性能边界：纯文档入口变更，不影响请求、执行器、数据库或模型文件。
- 本地、预生产和生产证据：仅本地文件存在性检查；未连接生产。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：文档路由达到本地L1；方案内容、真实设备和生产证据仍按RUN-0045边界执行。

### RUN-0052 BM1688/边端调度租约候选迁移与隔离演练

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第15.8节并发性能；迁移治理`MIG-U0B-LEASE-01`、并发治理`CONC-U0B-05`。
- 工作包/角色：WP2/U0B数据库迁移与执行器；R03架构、R04后端、R07 BM1688边端、R09测试协作。
- 本轮目标：在不修改正式`labelu/alembic_labelu/versions`目录、不制造第二个head的前提下，产出四个自动调度租约字段和索引的候选迁移，并完成空SQLite演练和MySQL演练安全闸门验证。
- 迁移结论：候选文件位于`labelu/docs/7gai_phase_one_migration_candidates/gai_p1_08_add_dispatch_leases.py`，声明`revision=gai_p1_08`、`down_revision=gai_p1_07`，只在正式八段重建时合入第八段；不允许直接复制到正式目录。迁移要求前置段已创建`yolo_bm1688_export`与`yolo_edge_validation_job`，否则明确失败，不静默跳过。
- 字段和索引：两张表各增加可空`queue_lease_owner VARCHAR(128)`、`queue_lease_expires_at DATETIME(timezone=True)`；分别创建`ix_<表>_queue_lease_owner`和`ix_<表>_queue_lease_expires_at`，索引可重复执行，旧行保持NULL兼容。降级只删除这四列和四个索引，不触碰业务状态、制品和边端执行租约`lease_expires_at`。
- 演练脚本：新增`labelu/scripts/rehearse_dispatch_lease_migration.py`。无参数时创建临时SQLite、创建最小目标表、执行升级两次、校验列/索引、执行降级并清理；MySQL只有本机且数据库名以`labelu_migration_sandbox`开头、显式传入`--允许修改`才允许DDL，拒绝生产主机和生产库名。当前环境未提供可确认的MySQL沙箱，因此没有冒充MySQL执行通过。
- 修改文件清单：`labelu/docs/7gai_phase_one_migration_candidates/gai_p1_08_add_dispatch_leases.py`、`labelu/scripts/rehearse_dispatch_lease_migration.py`、`labelu/tests/alembic/test_dispatch_lease_migration_candidate.py`、本交付台账。
- 测试命令及结果：`python -m pytest labelu/tests/alembic/test_dispatch_lease_migration_candidate.py -q --disable-warnings --tb=short`，`5 passed`、零失败、零跳过；`python labelu/scripts/rehearse_dispatch_lease_migration.py`默认SQLite演练通过；候选迁移和脚本`py_compile`通过；隔离MySQL安全闸门测试覆盖非本机主机、生产库名拒绝。
- 全量回归证据：隔离库`full-suite-continuation-20260918`全量为`2296 passed、60 failed、47 skipped`。失败主要仍是项目/设备签名/设备Agent等已退休控制面、旧项目契约、旧抽屉选择器和未迁移的主动学习/Provider/规则旧测试；本轮未通过恢复已取消能力来消除失败。该全量结果不能作为一期生产通过证据。
- 并发与性能边界：本轮只验证迁移级幂等、索引存在和前置表缺失快速失败；没有执行真实MySQL DDL、元数据锁等待、生产快照副本、双进程租约竞争、执行期续租/fencing或20并发压测。
- 本地、预生产和生产证据：仅候选目录、隔离SQLite和本地安全闸门；未修改正式迁移链、未连接生产数据库、未执行生产DDL、未写生产数据。
- 下一依赖与负责人：R03/R10需将候选内容合入完整八段`gai_p1_08`并通过唯一head审计；R09在可控本机MySQL沙箱执行空库与快照副本升级；R07补执行期租约续期/fencing；完成前四个字段不能部署生产，应用保持本地L1。
- LabelU应用版本恢复办法：候选文件和脚本均不在正式迁移扫描目录；验证失败直接删除候选目录或前向修正，不需要回滚正式数据库和已发布模型。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：租约候选DDL、SQLite演练和MySQL安全拒绝达到本地L1；完整八段合并、MySQL沙箱/生产快照、真实并发和生产迁移验收未完成，平台整体仍不得超过L1。

### RUN-0048 训练中心历史右侧面板统一为居中弹窗

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第5章训练中心、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP2训练与评估、WP5前端验收；R08前端主责，R04后端协作，R09测试验收。
- 本轮目标：清理训练中心导入外部模型和双模型对比窗口遗留的右侧面板样式；训练作业详情和新建训练原本已是居中弹窗，保持其业务逻辑不变。
- 实现内容：`frontend/extensions/pages/yolo_training.html`将导入模型、报告对比窗口的`drawer-panel/drawer-top/drawer-scroll/drawer-close-btn`统一改为`modal-form-panel/modal-form-head/modal-form-scroll/modal-close-btn`；基础遮罩改为居中布局、限高和内部滚动；页面滚动锁定统一为`modal-open`；未改变训练创建、模型导入、报告查看和对比接口。
- 测试命令及结果：Node `vm.Script` 内嵌脚本解析通过，输出`training-js-parse-ok`；`rg -i "drawer|抽屉"`页面无匹配；`git diff --check`无空白错误。未启动完整浏览器页面。
- 并发与性能边界：纯展示层 CSS、类名和滚动锁变更，不新增训练请求、轮询、数据库锁或执行器任务；未执行训练接口联调、浏览器视觉和并发压测。
- 本地、预生产和生产证据：仅本地静态解析和残留扫描；未连接生产、未启动训练或导入模型。
- 风险与下一依赖：训练中心仍需要在有`LABELU_E2E_BASE`的本地浏览器环境验证导入、关闭、窄屏滚动和报告对比；生产训练链路和权限不因本轮 UI 改动改变。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：训练中心弹窗结构达到本地L1；浏览器真机、训练联调和生产验收仍未完成。

### RUN-0049 AI标注设置模型档案右侧面板改为居中弹窗并复核标注设置E2E

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第7章系统管理、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP5系统管理与前端验收；R08前端主责，R01产品、R02体验、R09测试协作。
- 本轮目标：检查AI标注设置实际交互，确认模型档案编辑确实使用居中弹窗；同步确认标注设置页面不再使用旧抽屉选择器。
- 现状结论：`frontend/extensions/pages/ai_labeling_settings.html`原先的`modal-bg`是右侧全高面板（`justify-content:flex-end`、`width:520px`、`height:100vh`），与主方案统一居中弹窗要求不一致；标注设置页面已使用`modalLabel`、`modalRuntime`等居中弹窗ID，E2E旧`drawer-*`选择器已改为`modal-*`并增加打开状态和Esc关闭断言。
- 实现内容：AI标注设置遮罩改为居中布局，模型档案弹窗改为`max-width:720px`、`max-height:calc(100vh - 32px)`和内部滚动；页面滚动锁定由`drawer-open`统一为`modal-open`；打开后聚焦取消按钮；遮罩和Esc关闭时统一清理弹窗状态；保存成功后同样清理状态。未改变AI档案、调用链、站点提示词和YOLO评估提示词API。
- 修改文件清单：`frontend/extensions/pages/ai_labeling_settings.html`、`frontend/extensions/tests/ai_labeling_settings_modal.test.mjs`；标注设置E2E同步状态位于`labelu/tests/e2e/test_annotation_settings_frontend.py`（本轮复核，旧选择器已不存在）。
- 测试命令及结果：`node --test frontend/extensions/tests/ai_labeling_settings_modal.test.mjs frontend/extensions/tests/third_party_calls_console.test.mjs`，`2 passed`、零失败、零跳过；`python -m pytest labelu/tests/e2e/test_annotation_settings_frontend.py -q --disable-warnings --tb=short`，`3 skipped`（未设置`LABELU_E2E_BASE`，未冒充浏览器通过）；`git diff --check`通过。
- 并发与性能边界：本轮只修改弹窗展示、滚动锁定、焦点和静态契约测试；不新增接口、轮询、数据库锁、任务、模型或外部调用，不改变AI配置保存并发语义。
- 本地、预生产和生产证据：仅本地静态契约测试和未配置环境下的E2E跳过结果；未启动浏览器服务，未连接生产，未写AI配置、S3、AICM或数据库。
- 风险和下一依赖：具备本地页面服务后，R09需执行AI档案新建/编辑/删除确认/取消/遮罩/Esc/窄屏滚动浏览器测试；AI页面删除档案已统一使用居中确认弹窗，标注设置页面的分类、运行模板、预设和维度仍有原生`confirm()`，应另立小单元处理；全站其他旧抽屉需继续按`UI-MODAL-01`清理。
- LabelU应用版本恢复办法：本轮只改前端页面和本地测试，未改数据库或生产配置；验证失败时回退本轮前端文件即可，不涉及模型发布或下载副本。
- 文档同步位置：主方案第12章、第15.10节；本台账RUN-0042、RUN-0043、RUN-0046、RUN-0049。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：AI标注设置居中弹窗契约达到本地L1；标注设置E2E选择器已同步但浏览器服务未启用；浏览器视觉、接口联调、预生产和生产验收仍未完成。

### RUN-0050 BM1688质量规则收口为单一按精度字段

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第10章模型发布；规则契约`BM1688-QUALITY-RULE-01`。
- 工作包/角色：WP2模型算法与质量门禁；R06主责，R07边端、R03架构、R09测试协作。
- 本轮目标：消除质量快照中`edge.variant_max_drop.int8`与`edge.max_int8_map50_drop`的重复语义，避免新任务和历史运行包出现两套阈值解释。
- 实现内容：`BM1688_QUALITY_RULE`只保留`edge.variant_max_drop`，FP16和INT8均按精度读取；`edge_variant_drop_limits`仅在读取没有新版字段的历史快照时兼容`max_int8_map50_drop`，兼容值不会写回新快照，也不会改变默认规则版本。
- 测试命令及结果：`python -m pytest labelu/tests/internal/common/test_bm1688_quality_rules.py labelu/tests/scripts/test_edge_board_validate_runner_script.py -q --disable-warnings --tb=short`，`16 passed`、零失败；覆盖新版快照不含旧字段、历史字段只读兼容和边端脚本既有门禁。
- 并发与性能边界：纯规则结构和解析逻辑变更，不增加数据库请求、锁或设备任务；未执行真实TPU-MLIR、BM1688、MySQL并发或生产数据验证。
- 本地、预生产和生产证据：仅本地单测证据；未连接生产、未写生产数据，历史运行包仍需重新打包版本化快照后才可进入发布评估。
- 风险与下一依赖：正式迁移、执行期fencing、真实设备三精度验证和生产阈值标定仍未完成；边端报告中的`max_int8_map50_drop`输出字段作为历史报告兼容展示，后续应标注为兼容字段并逐步停止写入。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：规则结构收口达到本地L1；跨服务联调、预生产和生产验收未完成，平台整体仍不得超过L1。

### RUN-0051 AI标注设置删除确认统一为居中弹窗

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第2章任务与标注、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP1标注设置；R08前端主责，R04后端协作，R09测试验收。
- 本轮目标：清除AI标注设置中浏览器原生`confirm()`确认框，统一为可关闭、可取消、支持Esc和遮罩关闭的中文居中确认弹窗。
- 实现内容：新增删除确认弹窗及待删除档案状态；删除按钮只负责打开弹窗，确认按钮才调用原有删除接口；取消、遮罩、Esc均关闭弹窗并恢复页面滚动；未改接口路径、权限和删除语义。
- 测试命令及结果：`node --test frontend/extensions/tests/ai_labeling_settings_modal.test.mjs frontend/extensions/tests/third_party_calls_console.test.mjs`，`2 passed`、零失败；页面内嵌脚本`node/vm.Script`解析通过，`rg`检查无`drawer`、`抽屉`或原生`confirm(`残留；`git diff --check`通过。
- 并发与性能边界：纯前端交互变更，不增加请求；删除确认仍由单次点击触发，重复点击在请求期间不改变后端幂等语义，真实接口并发与浏览器焦点验收尚未执行。
- 本地、预生产和生产证据：仅本地静态脚本和残留扫描；未连接生产，未删除模型档案或写入生产数据。
- 风险与下一依赖：需要配置`LABELU_E2E_BASE`后执行真实浏览器桌面/窄屏点击、取消、遮罩、Esc及删除失败提示；全站其他页面仍需按`UI-MODAL-01`逐页验收。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：AI标注设置弹窗结构达到本地L1；浏览器真机、接口联调和生产验收未完成，平台整体仍不得超过L1。

### RUN-0054 标注设置危险操作统一使用复用确认弹窗

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第3章标签与数据契约、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP1标注设置；R08前端主责，R02数据契约、R04后端、R09测试协作。
- 本轮目标：把分类删除、运行模板停用、预设删除、全局维度删除和JSON导入覆盖写入从浏览器原生确认框统一到现有居中弹窗框架。
- 实现内容：复用`modalBackdrop`和`modal-panel`新增`modalConfirm`；`askConfirm(message, action, title)`统一管理确认文案、待执行回调和焦点；取消、关闭按钮和遮罩不执行回调，确认后关闭弹窗再调用原有API；未改变接口、版本校验和权限。
- 测试命令及结果：页面内嵌脚本`vm.Script`解析通过；`node --test frontend/extensions/tests/annotation_settings_confirm_modal.test.mjs frontend/extensions/tests/ai_labeling_settings_modal.test.mjs frontend/extensions/tests/third_party_calls_console.test.mjs`，`3 passed`、零失败；页面无原生`confirm(`、`drawer`或`抽屉`残留；`git diff --check`通过。
- 并发与性能边界：确认弹窗只影响客户端操作顺序，不增加数据库锁和请求；确认按钮每次只消费一个待执行回调，真实接口重复提交、权限和失败提示仍需浏览器联调。
- 本地、预生产和生产证据：仅本地脚本解析和结构测试；未连接生产、未写标签/预设/维度数据，未执行真实JSON导入。
- 风险与下一依赖：需要设置`LABELU_E2E_BASE`执行桌面/窄屏打开、取消、Esc、遮罩和确认失败提示；确认回调与后端幂等仍需R09联调；设备注册页属于一期退休控制面，继续保留历史抽屉但不重新开放。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：危险操作复用确认弹窗达到本地L1；浏览器真机、后端联调、预生产和生产验收未完成，平台整体仍不得超过L1。

### RUN-0055 第三方策略S3配置删除确认改为居中弹窗

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第8章数据回流、第12章前端交互；生产事实`S3-SOURCE-01`、交互契约`UI-MODAL-01`。
- 工作包/角色：WP4第三方回流与系统管理；R05外部平台主责，R08前端、R04后端、R09测试协作。
- 本轮目标：第三方策略 S3 配置页删除操作统一为中文居中危险确认，避免误删生产问题图片回流来源配置。
- 实现内容：`strategy_integration_s3_config.html`新增居中遮罩确认框、待删除配置状态、取消/遮罩/Esc关闭和删除确认；确认成功后才调用原有`DELETE /strategy-integration-s3-config/connections/{id}`，保留当前启用地址、鉴权和刷新逻辑。
- 测试命令及结果：页面内嵌脚本`vm.Script`解析通过；`node --test frontend/extensions/tests/strategy_integration_s3_confirm_modal.test.mjs frontend/extensions/tests/annotation_settings_confirm_modal.test.mjs frontend/extensions/tests/ai_labeling_settings_modal.test.mjs frontend/extensions/tests/third_party_calls_console.test.mjs`，`4 passed`、零失败；S3配置页无原生`confirm(`残留；`git diff --check`通过。
- 并发与性能边界：只改客户端确认顺序，不增加S3读取、数据库锁或回流任务；删除请求仍由后端权限和配置状态校验，真实重复点击/失败提示尚未做浏览器联调。
- 本地、预生产和生产证据：仅本地结构测试；未连接`http://113.141.72.175:10000/strategy-integration-s3-config`，未删除或修改任何生产S3配置。
- 风险与下一依赖：需要真实浏览器验证窄屏弹窗、焦点、取消和删除失败状态；继续清理 BM1688 导出、训练和模型目录页面的原生确认框时，必须复用同一交互契约，不得新增第二套确认组件。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：S3配置危险确认达到本地L1；真实S3、浏览器真机、预生产和生产验收未完成，平台整体仍不得超过L1。

### RUN-0056 模型中心同步和兼容标识删除复用确认弹窗

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第10章模型中心、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP3模型发布与目录；R08前端主责，R06模型算法、R04后端、R09测试协作。
- 本轮目标：模型中心“同步模型目录”和“删除第三方兼容标识”不再使用浏览器原生确认框，避免误触发 AICM 同步或破坏模型回流对齐标识。
- 实现内容：复用模型中心现有居中弹窗样式新增确认弹窗和单次待执行回调；同步按钮在确认后才调用`POST /published-models/sync`，兼容标识删除在确认后才调用原有删除接口；支持取消、遮罩关闭和Esc关闭，不改变模型 ID、下载开关或发布权限。
- 测试命令及结果：页面内嵌脚本`vm.Script`解析通过；`node --test frontend/extensions/tests/yolo_model_catalog_confirm_modal.test.mjs frontend/extensions/tests/strategy_integration_s3_confirm_modal.test.mjs`，`2 passed`、零失败；模型中心页无原生`confirm(`残留；`git diff --check`通过。
- 并发与性能边界：确认弹窗不增加后台请求；同步仍由按钮禁用和后端幂等负责，删除仍由后端权限与目录状态校验；真实 AICM 同步、重复点击和并发更新尚未联调。
- 本地、预生产和生产证据：仅本地结构测试；未连接 AICM、未同步生产目录、未删除任何模型标识。
- 风险与下一依赖：需在浏览器真机验证模型详情弹窗与确认弹窗互斥、窄屏滚动和同步失败提示；BM1688 导出/训练页仍有原生确认框，后续继续复用同一组件契约。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：模型中心两个危险/影响操作达到本地L1；AICM联调、浏览器真机、预生产和生产验收未完成，平台整体仍不得超过L1。

### RUN-0057 正式八段迁移图审计与租约候选隔离预演

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第15.8节并发性能；迁移治理`MIG-U0B-LEASE-02`。
- 工作包/角色：WP2/U0B迁移预演；R03架构主责，R04后端、R07边端、R09测试协作。
- 本轮目标：在不改正式`labelu/alembic_labelu/versions`、不连接生产数据库的前提下，审计正式迁移图唯一head，验证八段候选链入口和第八段租约字段/索引的隔离结构闭包。
- 实现内容：新增`labelu/scripts/rehearse_phase_one_migration.py`，只读审计生产基线`h7i8j9k0l1m2`后的37个旧修订，并可接收隔离八段目录做单父链校验；默认使用临时SQLite演练`gai_p1_08`候选迁移，升级重复执行保持幂等，确认`yolo_bm1688_export`和`yolo_edge_validation_job`各有`queue_lease_owner`、`queue_lease_expires_at`及对应四个索引，降级只移除这些租约对象。新增`labelu/tests/alembic/test_phase_one_migration_rehearsal.py`覆盖正式图报告、SQLite四字段/索引、退休表不创建和MySQL库名安全闸门。
- 测试命令及结果：`python -m pytest labelu/tests/alembic/test_phase_one_migration_rehearsal.py labelu/tests/alembic/test_dispatch_lease_migration_candidate.py labelu/tests/alembic/test_phase_one_migration_rebuild_audit.py -q --disable-warnings --tb=short`，`13 passed`、零失败；补充`python -m pytest labelu/tests/alembic/test_empty_database_upgrade.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py -q --disable-warnings --tb=short`，`5 passed`，空SQLite正式引导也校验两张表的四个租约字段和四个索引；`python labelu/scripts/rehearse_phase_one_migration.py`默认SQLite演练通过；脚本和测试均可导入执行。
- 并发与性能边界：本轮只验证迁移图静态唯一head、候选DDL幂等和索引存在，不代表执行期续租、fencing token、双进程竞争或20并发压测已完成；没有测量MySQL元数据锁等待和执行计划。
- 本地、预生产和生产证据：正式迁移目录未修改，使用临时SQLite；当前环境没有可确认的本机`labelu_migration_sandbox*` MySQL实例，未执行MySQL DDL，未连接生产快照、S3、AICM、TPU-MLIR或BM1688。
- 下一依赖与负责人：R03/R10仍需在独占窗口生成真实八个`gai_p1_01`至`gai_p1_08`迁移并通过空SQLite、空MySQL和生产快照副本升级；R09提供本机MySQL沙箱和表/列/索引/外键/字符集/行数对账；R07补执行期租约续期与fencing；在这些证据完成前，四个字段不能视为已进入生产。
- LabelU应用版本恢复办法：本轮只新增隔离脚本和测试，候选迁移仍在`docs/7gai_phase_one_migration_candidates`，验证失败删除或修正隔离文件即可，不涉及正式数据库、已发布模型和下载目录。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：正式图审计和租约候选SQLite隔离预演达到本地L1；真实八段迁移文件、MySQL沙箱、生产快照、并发和生产升级验收未完成，平台整体仍不得超过L1。

### RUN-0111 BM1688手工导出与重新编译统一进入独立Worker

- 日期和时区：2026-09-18，Asia/Shanghai。
- 工作包/角色：WP3模型编译与边端执行；R03架构、R04后端主责，R06模型算法、R07边端、R09测试、R10运维协作。
- 本轮交付：新增`YoloBm1688Export.worker_action`，统一`export`和`recompile`动作；独立Worker消费自动和手工BM1688任务；Web在独立Worker开启时只入队，不再启动编译线程；增加动作索引和Worker扫描复合索引；保留未开启Worker时的兼容线程路径。
- 验证：BM1688 Worker、导出入口、重新编译 fencing 和候选迁移专项合计`46 passed`；Python编译和`git diff --check`通过。
- 并发/性能边界：仅本地SQLite和代码级验证；MySQL执行计划、锁等待、20并发、systemd、真实TPU-MLIR/BM1688、S3、AICM和生产容量未验收。
- 当前状态：本地L1，未提交、未推送、未部署；独立记录为`labelu/docs/7gai_run_0111_bm1688_manual_queue_actions.md`。
- 下一依赖：R03/R09/R10将`worker_action`纳入正式唯一迁移链并完成同版本MySQL演练；R07完成真实BM1688 Worker重启和板卡资源释放验证；完成前保持`fail-closed`。

### RUN-0053 全量回归结果与旧契约清单同步

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第1.12节、第15.8节；测试迁移治理`MIG-U0B-003`。
- 工作包/角色：WP5全量回归与旧契约清单；R09测试主责，R01产品、R03架构、R04后端、R07边端、R08前端协作。
- 本轮目标：在RUN-0039修复训练候选状态后重新执行隔离全量回归，更新机器可读失败清单，避免把已修复缺陷和已完成的导航/迁移测试继续算作未完成。
- 全量证据：全新隔离库执行`LABELU_TEST_DB_NAME=full-suite-continuation-20260918-fresh python -m pytest labelu/tests -q --disable-warnings --tb=short`，结果为`2303 passed、60 failed、47 skipped`，完整日志为`.tmp/full-suite-continuation-20260918-fresh.log`。失败节点通过`--lf --collect-only`核对，较旧61项清单移除迁移审计、`training_ready`和旧主题导航三项，新增AICM环境详情页面两个旧抽屉选择器断言。
- 清单变更：`labelu/docs/7gai_legacy_test_contract_migration.json`和`labelu/tests/internal/common/test_legacy_test_contract_migration.py`同步到60节点；类别为`retired_project_control_plane` 26项、`phase_one_contract_to_migrate` 34项，不再保留已修复的`independent_defect`类别。AICM两个旧选择器测试归入前端契约迁移，不能通过恢复抽屉实现。
- 修改文件清单：`labelu/docs/7gai_legacy_test_contract_migration.json`、`labelu/tests/internal/common/test_legacy_test_contract_migration.py`、本交付台账。
- 测试命令及结果：清单校验`python -m pytest labelu/tests/internal/common/test_legacy_test_contract_migration.py -q --disable-warnings --tb=short`，`2 passed`；节点清单独立收集为`60/149`，与JSON逐项差异仅为已登记的AICM两个测试。
- 并发与性能边界：本轮只运行隔离回归和静态清单核对，不改变请求路径、数据库、租约、模型制品或外部回流；全量失败不能替代双进程、MySQL、S3、AICM、TPU-MLIR和BM1688证据。
- 本地、预生产和生产证据：仅隔离SQLite测试数据库和本地测试清单；未连接生产、未写生产数据、未部署。
- 下一依赖与负责人：R08迁移AICM环境E2E选择器并补浏览器真实弹窗行为；R02/R04/R05继续迁移主动学习、Provider、导入和自动训练旧项目夹具；R07隔离设备控制面测试；迁移完成后重新执行全量回归并生成完整日志文件。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：失败清单与现状同步达到本地L1；60个失败节点仍未完成迁移或已退休隔离，平台不能据此宣称一期完成。

### RUN-0058 自动训练、BM1688编译与边端验证执行并发审计

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第11章并发与任务执行、第15.7节验收分层；并发契约`CONCURRENCY-EXEC-01`。
- 工作包/角色：WP2训练与边端执行、WP3模型编译；R03架构主责，R04后端、R06模型算法、R07边端、R09测试协作。
- 本轮目标：核对自动训练、BM1688编译和边端验证是否仍由Web进程后台线程执行，区分调度交接租约与执行期租约，确认续租、fencing和跨进程竞争的真实完成范围。
- 代码路径结论：自动训练看门狗由`auto_training_watchdog.py`在应用进程启动线程，并通过调度器领导租约、续租和fencing检查保护看门狗周期；训练作业仍由`yolo_train.py`的本地队列在应用进程派发，默认兼容路径可在普通线程中启动训练。BM1688正常自动派发和自动恢复分别在`auto_training_dispatch.py`、`yolo_bm1688_export_auto_resume.py`中条件领取`queue_lease_owner/queue_lease_expires_at`后创建`threading.Thread`执行`_run_bm1688_export_sync`；`yolo_bm1688_export.py`另有进程内集合及MySQL命名锁，不能替代数据库执行期fencing。边端调度器在`yolo_edge_validation_scheduler.py`中将任务分配设备并写入`lease_expires_at`，随后创建线程执行`_run_manual_job_sync`；边端长阶段只通过`updated_at`心跳判断陈旧，当前没有按执行租约所有者条件续期和过期worker拒绝。
- 已有保护：BM1688/边端自动派发与自动恢复均先做数据库条件领取，失败不启动线程；边端同一进程有作业运行锁；边端执行有心跳；训练Worker命令路径已有fencing检查和晚到结果保护；看门狗周期有领导租约和fencing。`queue_lease_*`只承担调度器短时交接，不能视为编译或板端执行租约。
- 已确认缺口：一是BM1688编译执行期间没有独立的执行所有者、fencing token和续租，默认5分钟交接租约可能在长编译期间过期并被其他进程接管；二是边端`lease_expires_at`没有绑定执行worker所有者，心跳只更新时间不校验租约，旧worker可能在接管后继续写状态或导入结果；三是正常训练、BM1688和边端副作用仍存在Web进程线程路径，不是独立Worker完整链路；四是没有真实双进程/双Session接管、20并发副作用次数和长任务续租压测证据。
- 测试命令及结果：`python -m pytest labelu/tests/internal/application/service/test_auto_training_dispatch.py labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py labelu/tests/internal/application/service/test_auto_training_watchdog.py -q --disable-warnings --tb=short`，`56 passed`、零失败；`git diff --check`通过。现有测试覆盖单库条件领取、重复领取拒绝、恢复租约和看门狗fencing，但不覆盖真实跨进程执行期租约。
- 并发与性能边界：本轮只做代码路径和专项单元审计，没有启动TPU-MLIR、BM1688真机、SSH边端设备、生产数据库或外部平台；不能据此宣称生产并发安全、设备独占或副作用只执行一次。5分钟交接租约不能作为编译时长上限，边端设备执行租约必须大于脚本超时并支持续期，但当前尚未形成条件更新和fencing闭环。
- 下一步实现要求：先在八段迁移中为BM1688导出和边端验证补执行期所有者、到期时间及递增fencing token（与`queue_lease_*`分离）；Worker启动、每个长阶段、状态写入、制品落盘和结果导入前后均按所有者/token条件校验，续租失败立即停止副作用；边端心跳改为条件续期而非仅更新`updated_at`；再补双进程抢占、过期worker拒绝、长任务续租、20并发和生产快照副本验证。独立Worker迁移应保持现有Web接口兼容，先灰度自动链路，手工作业保留兼容路径但同样必须受执行fencing保护。
- 本地、预生产和生产证据：仅本地代码审计和隔离SQLite单元测试；未修改正式迁移链，未连接生产、未写生产任务、未触发编译、边端SSH或模型发布下载。
- LabelU应用版本恢复办法：本轮只新增台账记录，不改变代码、数据库、设备配置、模型制品或发布状态；无需回滚已发布模型，后续实现失败也不得使用模型回滚，按既定人工禁止下载处理。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：现有调度交接条件租约和看门狗fencing达到本地L1；BM1688/边端执行期续租与fencing、独立Worker、真实设备、双进程并发、预生产和生产验收未完成，平台整体不得超过L1。

### RUN-0059 训练、BM1688编译和评估提示词页确认弹窗统一

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第11章训练中心、第12章前端交互；交互契约`UI-MODAL-01`。
- 工作包/角色：WP2训练与边端验证、WP3模型编译；R08前端主责，R06模型算法、R07边端、R09测试协作。
- 本轮目标：将生产链路三个页面中的浏览器原生`confirm()`统一为中文居中确认弹窗，所有确认动作支持取消、遮罩关闭和Esc关闭，确认后才继续原有异步请求。
- 实现内容：`yolo_eval_prompts.html`新增提示词重新加载确认弹窗；`yolo_training.html`新增复用`requestConfirmation`确认器，覆盖BM1688导出、导入模型删除、训练暂停/继续、训练作业或模型删除、合并任务训练；`yolo_bm1688_exports.html`新增同样的复用确认器，覆盖无标注边端验证、旧板端验证重跑、重新编译、导出记录删除和coco128 ONNX评估。未改接口路径、权限、按钮状态、训练/编译/边端/S3/AICM业务逻辑。
- 修改文件清单：`frontend/extensions/pages/yolo_eval_prompts.html`、`frontend/extensions/pages/yolo_training.html`、`frontend/extensions/pages/yolo_bm1688_exports.html`；新增`frontend/extensions/tests/yolo_eval_prompts_confirm_modal.test.mjs`、`frontend/extensions/tests/yolo_training_confirm_modal.test.mjs`、`frontend/extensions/tests/yolo_bm1688_exports_confirm_modal.test.mjs`。
- 测试命令及结果：三个Node静态契约测试均通过；三个页面内嵌脚本使用`vm.Script`解析通过；目标文件`git diff --check`通过；`rg`扫描三个页面无原生`confirm(`残留。
- 并发与性能边界：纯前端确认交互，不新增请求、数据库锁、任务、轮询、设备连接或外部平台调用；Promise确认器只消费一次待处理结果，实际接口幂等、重复点击和长任务并发仍由既有后端/调度约束负责。
- 本地、预生产和生产证据：仅本地静态测试和脚本解析；未设置浏览器E2E环境，未连接生产、S3、AICM、TPU-MLIR或BM1688，未写入生产数据。
- 下一依赖与负责人：R09在配置`LABELU_E2E_BASE`后执行桌面/窄屏真机验证，覆盖确认、取消、遮罩、Esc、焦点和请求只触发一次；再由R01/R06/R07复核危险文案与生产作业语义。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：三个页面确认弹窗契约达到本地L1；浏览器真机、后端联调、预生产和生产验收未完成，平台整体不得超过L1。

### RUN-0060 前端可见品牌和入口文案中文化

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第1.5章、第12章前端交互；交互契约`UI-CN-01`。
- 工作包/角色：WP5前端统一体验；R08前端主责，R01产品、R02体验、R09测试协作。
- 本轮目标：落实“页面不要涉及英文，能用中文尽量用中文”，清除统一顶栏、页面标题和训练默认提示中的英文品牌名，保持平台路由和接口不变。
- 实现内容：统一工具页和 labelU-Kit 入口显示为“7GAI进化平台”；训练页面超参来源提示、页面标题和无障碍标签同步中文；更新平台入口静态契约测试。备份页和临时检查页不属于生产入口，未改动。
- 修改文件清单：`frontend/extensions/assets/labelu_portal.js`、生产工具页入口文案、`frontend/labelU-Kit/apps/frontend/index.html`、`frontend/labelU-Kit/apps/frontend/public/assets/labelu_portal.js`、`frontend/extensions/tests/7gai_platform_entrypoints.test.mjs`、`frontend/extensions/tests/labelu_portal_navigation.test.mjs`。
- 测试命令及结果：统一前端静态契约集合`9 passed`；目标页面原生`confirm(`扫描仍为零；`git diff --check`通过。
- 并发与性能边界：仅修改静态文案和测试，不增加请求、数据库锁、队列、设备任务或轮询；不改变权限、模型发布、下载开关和回流语义。
- 本地、预生产和生产证据：仅本地静态测试；未启动浏览器、未连接生产、S3、AICM、TPU-MLIR或BM1688，未写生产数据。
- 下一依赖与负责人：R09在浏览器真机验收中复核桌面/窄屏顶栏不换行、无障碍标签和中文长文本；后续新页面必须复用中文品牌常量，不得重新引入英文可见文案。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：生产入口文案和静态契约达到本地L1；浏览器真机、预生产和生产验收未完成，平台整体不得超过L1。

### RUN-0063 八段迁移候选链骨架与表归属审计

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第15.8节并发性能；迁移治理`MIG-U0B-CHAIN-01`。
- 工作包/角色：WP2/U0B正式迁移重建准备；R03架构主责，R04后端、R09测试协作。
- 本轮目标：基于生产基线`h7i8j9k0l1m2`，在不修改正式`labelu/alembic_labelu/versions`和不连接生产的前提下，生成八段候选链骨架，执行唯一单父链和表归属审计，明确无法自动重建的阻塞。
- 实现内容：新增隔离目录`.tmp/phase-one-rebuild/versions`中的`gai_p1_01`至`gai_p1_08`骨架和README。第01段父版本固定为`h7i8j9k0l1m2`，后续每段只指向前段，候选head为`gai_p1_08`；每段声明一期表归属和未完成阻塞。骨架`upgrade()`/`downgrade()`故意失败关闭，不伪装成可执行迁移；第08段明确区分表归属与兼容字段增补，保留现有四个租约字段/索引候选为待合入内容。新增`labelu/tests/alembic/test_phase_one_candidate_skeleton.py`。
- 审计结论：正式目录仍存在生产基线后的37个旧修订（25个替代、12个排除），含双父合并节点；一期ORM外键闭包仍有21张表指向退休`project`，必须先完成去项目化、唯一键和索引重建。当前候选第08段仅有租约DDL，Task/附件/数据源/专家回流兼容字段、`model_version_id`及生产大表在线索引仍未完成。
- 测试命令及结果：`python -m pytest labelu/tests/alembic/test_phase_one_candidate_skeleton.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py -q --disable-warnings --tb=short`，`7 passed`、零失败；`audit_phase_one_migration_rebuild.py --replacement-versions .tmp/phase-one-rebuild/versions`通过八段单父链审计；骨架未执行任何DDL。
- 并发与性能边界：本轮仅静态链路、表归属和失败关闭契约；未执行真实迁移、MySQL锁等待、生产快照对账、双进程租约/fencing或20并发。
- 本地、预生产和生产证据：正式迁移目录和数据库均未修改；没有可确认的空MySQL沙箱和生产快照，未连接生产、S3、AICM、TPU-MLIR或BM1688。
- 下一依赖与负责人：R03/R04按八段归属逐段把37个旧`upgrade()`拆成真实DDL，先解决21项项目外键闭包；R09补空SQLite/空MySQL/生产快照结构对账；R07落实执行期租约续期和fencing。所有阻塞关闭前不得将骨架放入正式versions或部署生产。
- LabelU应用版本恢复办法：`.tmp/phase-one-rebuild`为隔离候选，验证失败可直接删除该临时目录，不涉及正式迁移、业务数据或已发布模型。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：八段单父链和表归属静态审计达到本地L1；真实DDL、MySQL、生产快照、并发和生产迁移仍未完成，平台整体不得超过L1。

### RUN-0067 BM1688独立Worker第08段字段、索引与MySQL验收审计

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节与需求编号：主方案第9章BM1688编译与真机验证、第15.8节并发性能；迁移治理`MIG-U0B-WORKER-01`。
- 工作包/角色：WP2/U0B数据库迁移；R03架构主责，R04后端、R07边端、R09测试协作。
- 本轮目标：按BM1688独立Worker和边端真实调度查询核对第08段必需租约字段、fencing字段与索引，补充MySQL执行计划和锁等待验收清单；正式versions不动、生产不连接。
- 审计结论：`yolo_bm1688_export`和`yolo_edge_validation_job`各需5列：`queue_lease_owner`、`queue_lease_expires_at`、`execution_lease_owner`、`execution_lease_expires_at`、`execution_fencing_token`，共10列。前两列负责短时派发领取，后三列负责实际执行续租、接管和旧Worker写入隔离，不能复用边端板卡分配的`lease_expires_at`。按主键编号执行的fencing条件更新无需token单列索引。
- 索引决定：每表保留queue/execution owner和expires四个单列索引；BM1688表新增`(status,pipeline_origin,auto_training_round_id,queue_lease_expires_at,id)`的`worker_scan`复合索引，覆盖Worker和自动派发实际的round非空条件；边端表按当前真实调度器新增`(status,queued_at,id)`的`dispatch_scan`复合索引，不虚构尚不存在的独立Edge Worker。两表合计10个本轮租约/调度索引，列序仍需MySQL快照EXPLAIN确认。
- 实现内容：补充隔离候选`labelu/docs/7gai_phase_one_migration_candidates/gai_p1_08_add_dispatch_leases.py`和两个演练脚本；扩展`labelu/tests/alembic/test_dispatch_lease_migration_candidate.py`，精确断言10列、token非空、10索引和两个复合索引列顺序；新增`labelu/docs/7gai_bm1688_worker_migration_acceptance.md`，记录MySQL结构、执行计划、元数据锁、在线DDL和并发fencing验收步骤；同步隔离八段骨架阻塞说明。
- 测试命令及结果：`python -m pytest labelu/tests/alembic/test_dispatch_lease_migration_candidate.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py -q --disable-warnings --tb=short`，`9 passed`、零失败；`python labelu/scripts/rehearse_dispatch_lease_migration.py`和`python labelu/scripts/rehearse_phase_one_migration.py`均在临时SQLite通过，后者报告两表共10租约字段、10索引；候选重复升级幂等，降级只移除租约字段和索引。
- MySQL验收边界：必须在生产同版本的本机`labelu_migration_sandbox*`和脱敏生产快照副本执行`SHOW CREATE TABLE`、`SHOW INDEX`、`EXPLAIN FORMAT=JSON`、元数据锁观察、短`lock_wait_timeout`和双进程领取/接管测试；实际MySQL版本未确认前不承诺`INSTANT`或`LOCK=NONE`。当前没有可确认MySQL沙箱，以上项目均未执行、不得标记通过。
- 当前候选差异：租约候选已闭合独立Worker需要的10列/10索引，但仍不是完整`gai_p1_08`；Task、附件、数据源、第三方回流审计、模型版本关联和生产大表在线索引仍待逐项合并。空库ORM目前只会由`index=True`生成8个单列索引，正式落地还需同步两个复合索引到ORM，避免空库与生产升级结构漂移。
- 并发与性能边界：SQLite只能证明DDL结构和幂等，不能证明MySQL选择复合索引、锁等待可控或两个真实进程只执行一次副作用；20并发、生产规模执行计划和fencing失联接管仍待R09/R07验收。
- 本地、预生产和生产证据：仅本地隔离候选、临时SQLite和静态代码查询；未修改正式迁移目录，未连接生产、S3、AICM、TPU-MLIR或BM1688。
- commit/push/deploy状态：未提交、未推送、未部署；等待用户明确说“提交代码”。
- L0/L1/L2/L3/L4结论：第08段Worker字段/索引候选和SQLite结构验收达到本地L1；完整第08段、MySQL/快照、真实并发和生产迁移未完成，平台整体不得超过L1。

## 27. RUN-0117 独立边端验证 Worker 部署化接入

| 角色 | 工作包 | 本轮验收边界 | 结果 |
|---|---|---|---|
| R07 边端 | WP3 | 复用独立边端 Worker 入口、设备分配、execution lease 和 fencing | 本地结构接入完成；真实 BM1688 未验收 |
| R10 运维 | WP3/WP5 | systemd 单元、默认关闭、启停/重启/停止/清理、资源隔离和部署检查清单 | `deploy.sh` 已接入；真实 Ubuntu/systemd 未验收 |
| R04 后端 | WP3 | 独立 Worker 开启时关闭 Web 进程内执行，避免双消费 | 本地静态确认 |
| R09 测试 | WP5 | 部署入口、配置默认值、生命周期、隔离项和参数失败关闭 | 新增 shell 契约；Windows 缺可用 Bash，未执行 |

本轮独立记录：`labelu/docs/7gai_run_0117_edge_validation_worker_deployment.md`。修改文件为 `deploy.sh`、`.env.example` 和 `scripts/tests/test_deploy_edge_validation_worker_units.sh`。`git diff --check` 通过；未提交、未推送、未部署。

当前仍保持 `LABELU_ENABLE_EDGE_VALIDATION_WORKER=0`。在 Ubuntu/systemd、同版本 MySQL 双进程接管、真实 BM1688 三精度加载/推理/P95/短稳和生产数据证据完成前，不得打开该开关或宣称边端生产可用。

## 28. RUN-0118 去项目化生产验收基线

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP0/P5 | 验收范围不再要求项目 ID、跨项目隔离或项目接口 | 已同步一期无项目边界 |
| R03 架构 | WP0 | 以任务权限和数据集权限作为验收作用域 | 本地代码路径核验完成 |
| R04 后端 | WP1/WP2 | 核对任务、数据集、训练、模型、回流和退休控制面路由 | 与一期路由门禁一致 |
| R09 测试 | WP5 | 更新验收脚本夹具，增加项目参数拒绝和退休路径契约 | `15 passed` |
| R10 运维 | P5 | 生产脚本保持只读探针和证据输出 | 真实生产尚未执行 |

独立记录：`labelu/docs/7gai_run_0118_staging_acceptance_no_project_scope.md`。修改 `scripts/staging_acceptance.py` 和 `labelu/tests/scripts/test_staging_acceptance.py`；未改变生产 API、数据库或菜单。未提交、未推送、未部署。

下一步：在预生产/生产只读环境运行新的无项目验收脚本；在真实证据完成前，不得把脚本通过、HTTP 200 或 OpenAPI 路由存在当成全链路生产通过。

## 29. RUN-0119 生产只读验收差距证据

生产地址 `http://113.141.72.175:10000` 已使用无项目只读验收脚本完成实测，证据文件为 `.tmp/evidence/run0119-production-admin-readonly.json`，SHA256 为 `578830BA91720F1013B06B3E89B15D529131BF812A4CB3AFC853062C350EB6E1`。

结果：健康、登录、任务、训练作业、已发布模型目录、BM1688 编译、边端验证和第三方 S3 配置通过；数据集、算法目录、模型版本、工作台汇总均为 HTTP 404；`/openapi.json` 为真实 JSON，但 `/api-docs/openapi.json` 为前端 HTML；生产 OpenAPI 仍暴露 `edge-device-jobs` 三条已退休路径。结论为生产验收失败，不能放行。

本轮只读，无生产写入。P0 依赖是部署当前无项目路由、隐藏旧设备任务路由并修正 Nginx/API 分流；完成前不能宣称方案已在生产落地。

## 30. RUN-0135 规范路由与 Nginx 分流契约审计

| 角色 | 本轮职责 | 结果 |
|---|---|---|
| R03 架构 | 核对一期四条规范 OpenAPI 路由与退休控制面边界 | 本地四条路由均已注册；`edge-device-jobs` 兼容地址仅返回 30004/403 且已从 OpenAPI 隐藏 |
| R04 后端 | 核对认证依赖、响应模型和中文 OpenAPI 真源 | 四条规范路由统一 Bearer 认证和 `OkResp`/分页响应；本地 OpenAPI 路径契约通过 |
| R09 测试 | 执行路由、退休控制面、部署脚本专项回归 | `22 passed`；新增 `/api-docs/openapi.json` JSON 探针断言 |
| R10 运维 | 审计 Nginx 精确匹配、SPA 回退和部署探活 | 精确路径位于 `location /` 之前；补充本地化 OpenAPI JSON 探针，Linux 真机待执行 |

独立记录：`labelu/docs/7gai_run_0135_route_contract_audit.md`。本轮未连接生产数据库、未写入生产、未提交、未推送、未部署。生产 `RUN-0134` 的缺失路由、旧路由暴露和 `/api-docs/openapi.json` HTML 问题仍是生产放行阻断。

### RUN-0121 Provider 注册表项目契约收口增量

| 角色 | 本轮交付 | 验收结果 |
|---|---|---|
| R04 后端 | Provider 注册表列表/详情移除 `project_id` 查询参数和项目权限分支；保留状态、能力、版本筛选及管理人员证据更新 | 本地路由回归 `11 passed` |
| R08 前端 | Provider 目录请求不再发送 `project_id`，复用全局目录分页查询 | Provider 页面 Vitest `2 passed` |
| R09 测试 | 更新旧项目成员测试为历史参数兼容读取契约；核对真实 OpenAPI 参数 | 后端无 `project_id` 参数；前端无失败 |
| R01/R03 产品架构 | 明确 Provider 注册表是平台级能力目录，不属于任务/数据项目隔离 | 方案和 RUN-0121 已记录边界 |

本轮独立记录：`labelu/docs/7gai_run_0121_provider_no_project_scope.md`（第8节）。修改文件为 `labelu/internal/adapter/routers/provider_registry.py`、`labelu/internal/application/service/provider_registry.py`、`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/providers.tsx` 和 Provider 注册表测试。当前仅本地验证，未提交、未推送、未部署；生产 OpenAPI、前端构建产物和真实 Provider 服务仍待部署后复核，平台整体保持本地 L1。

## 31. RUN-0137 BM1688/边端执行期租约续租与 fencing 收口

| 角色 | 本轮交付 | 验收结果 |
|---|---|---|
| R03 架构 | 区分 `queue_lease_*` 派发交接与 `execution_lease_*` 实际执行；确认失租由恢复扫描回队 | 本地契约完成，正式迁移未执行 |
| R04 后端 | 边端长阶段心跳改为 owner/token/未过期条件写入；旧 Worker 失租后停止刷新 `updated_at` | 本地完成 |
| R07 BM1688/边端 | BM1688 独立 Worker 新增实际数据库字段就绪探针，迁移不完整时不领取、不编译 | 本地完成，真实设备未验收 |
| R09 测试 | 复核两进程领取、租约接管、字段缺失失败关闭和边端心跳 fencing | `26 passed` |
| R10 运维 | `--once` 未就绪返回失败码，循环模式等待；保留默认关闭和生产阻断 | 本地静态完成，Ubuntu/systemd 待验收 |

独立记录：`labelu/docs/7gai_run_0137_execution_fencing.md`。本轮修复边端未加 fencing 的心跳写入，并为 BM1688 独立 Worker 增加迁移字段就绪门禁；未修改正式 Alembic 迁移，未连接生产、MySQL、TPU-MLIR、BM1688、S3 或 AICM。

本地专项：`python -m pytest labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/application/service/test_yolo_edge_validation_auto_resume.py labelu/tests/internal/application/service/test_worker_execution_fencing_contract.py -q --disable-warnings --tb=short`，`26 passed`。当前仍为本地 L1；同版本 MySQL 双进程/锁等待/重启接管、真实 BM1688 和生产探针完成前，独立 Worker 必须保持关闭。

本轮未提交、未推送、未部署，等待用户明确“提交代码”。

### RUN-0140 第三方策略 S3 问题图片自动回流审计

- 日期和时区：2026-09-19，Asia/Shanghai。
- 工作包/角色：WP4 回流闭环；R05 外部平台、R04 后端、R09 测试，R03/R10 协作。
- 审计范围：`metadata.json`、AICM 模型 ID、`package_id` 幂等、聚合任务创建/复用、预标注、少量人工异常、S3 Worker 租约和失败关闭。
- 本地结论：新 `/internal/v1/experiment-datasets` 已有包级幂等、任务聚合复用、发布模型解析、预标注和独立 Worker owner/token 租约；Worker 专项 `6 passed`。同名文件在任务内可复用，但没有跨包内容 SHA256/感知哈希近重复去重。
- 关键缺口：旧 `/integrations/strategy-tasks` 仍是同步独立链路，按 `external_task_id` 幂等但不校验发布目录 AICM ID，也不走新 Worker/聚合等待状态；在入口统一前不能把旧链路计入自动回流通过。`metadata.json` 内 AICM ID 字段名和与请求 ID 一致性尚无真实生产样例，当前只保存请求 `target.model_id`。
- 生产事实：`/strategy-integration-s3-config` 只维护 S3 连接；生产只读曾确认 1 个启用连接、`strategy_task_integration` 有 23 条历史记录，但 S3 契约字段为空，尚无有效包、metadata、图片、Worker 日志、AICM 对账或 MySQL 并发证据。
- 人工边界：系统自动接收、下载、校验、去重候选、建/复用任务和预标注；管理人员只处理字段冲突、模型未发布、标签异常、低置信/抽检失败和少量近重复确认，不逐张建任务。
- 并发边界：本地只证明租约领取/过期接管/单包失败隔离；同版本 MySQL 双 Worker、20 并发、锁等待、重启接管、S3 大包和真实 AICM 尚未验收。
- 旧项目测试说明：`test_strategy_workflow_bridge.py` 的 3 个旧 `project_id` 断言失败属于已退休项目维度测试，不恢复 `project_id`，不计入本轮 S3 新增回归。
- 独立记录：`labelu/docs/7gai_run_0140_s3_reflow_audit.md`。
- commit/push/deploy：未提交、未推送、未部署，等待用户明确“提交代码”。
- L0/L1/L2/L3/L4：达到本地 L1 审计；旧入口统一、真实 metadata/AICM、S3、MySQL、Worker 和生产验收未完成，平台整体不得超过 L1。

## RUN-0144 第三方回流导入表与正式迁移链只读审计

- 日期和时区：2026-09-19，Asia/Shanghai。
- 工作包/角色：WP2 迁移与数据治理；R03 架构主责，R04 后端、R09 测试协作。
- 审计目标：核对正式 Alembic head、`ExperimentDatasetImport` ORM 与迁移后的表结构/索引，以及空库升级测试是否真实可运行。
- 代码和数据库变化：未修改正式迁移、ORM、生产数据库；仅使用 `.tmp/run0144-empty.sqlite` 做隔离升级和 Inspector 对账。
- 证据：Alembic 实际解析为唯一 head `z7a8b9c0d1e2`，共 134 个 revision；全新 SQLite `upgrade(head)` 成功；ORM 与升级后表均为 37 列，缺失/多余列均为 0；Worker 扫描复合索引、唯一 `package_id` 约束、来源/聚合/租约索引均存在。
- 测试：`test_experiment_import_worker_migration.py` 与 `test_phase_one_migration_rehearsal.py` 共 `5 passed`、113 warnings；未执行真实 MySQL。
- 关键边界：空库路径由 `env.py` 创建当前 ORM 基线并 stamp head，不等价于生产历史数据逐迁移；生产仍需同版本 MySQL、脱敏快照、锁等待、执行计划、备份恢复和双 Worker fencing 证据。
- 并发与失败关闭：生产确认 `alembic_version`、字段或索引漂移时，回流 Worker 必须保持关闭；双 Worker 竞争、过期接管和旧 token 拒绝未完成前不放量。
- 独立记录：`labelu/docs/7gai_run_0144_experiment_import_migration_audit.md`。
- commit/push/deploy：未提交、未推送、未部署，等待用户明确“提交代码”。
- L0-L4：本地 L1；预生产/生产迁移和并发证据未完成，平台整体不得超过 L1。

## RUN-0148 训练提交链不可变数据版本与清单完整性

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP2 | 核对训练作业、DatasetVersion、manifest、标签快照和 train/val/test 划分的单一血缘 | 统一训练路由已自动生成/复用冻结版本；生产未验收 |
| R04 后端 | WP2 | 训练作业绑定数据版本、路径和 SHA256；Worker 清单摘要校验与失败关闭 | 已修改 Worker 清单读取逻辑；摘要不一致/不可读不再回退可变任务数据 |
| R06 模型算法 | WP2 | 审计单标签训练类别快照与 YOLO 导出语义 | 单标签只保留对应矩形框和类别顺序，全量训练保留全量类别 |
| R09 测试 | WP5 | 冻结清单、摘要完整性、单标签版本专项回归 | `5 passed`；另有 2 项既有项目字段夹具失败，未计入通过 |

本轮独立记录：`labelu/docs/7gai_run_0148_training_dataset_freeze.md`。修改文件为 `labelu/internal/application/service/yolo_train.py` 和 `labelu/tests/internal/application/service/test_yolo_train_frozen_manifest.py`，新增上述独立记录。尚未提交、推送或部署，等待用户明确“提交代码”。

生产边界：尚未执行同版本 MySQL 并发/锁等待、真实 GPU、TPU-MLIR、BM1688、对象存储、S3、AICM 或生产 Worker；训练 Worker 继续保持关闭，当前只能标记本地 L1。

## RUN-0152 主动学习聚类与批次去项目化及前端编排契约

| 角色 | 本轮交付 | 验收结果 |
|---|---|---|
| R03 架构/R04 后端 | 聚类、批次使用 `scope_key`；历史 `project_id` 只兼容追溯；全局批次按模型、来源包和难例集合生成稳定幂等键 | 后端主动学习专项 `3 passed`；空库迁移和 MySQL 在线迁移、真实并发仍未验收 |
| R08 前端 | 进化中心去掉项目选择依赖；聚类、批次、数据集版本、工作流统一调用全局接口；中文按钮、居中弹窗 | 页面已无 `/v1/projects` 和请求 `project_id`；新增无项目请求契约测试，前端依赖缺失，待完整前端环境执行 |
| R09 测试 | 锁定全局聚类/批次权限、幂等和终态只读规则 | 后端/迁移组合专项 `5 passed`；前端测试代码已补，未能在当前缺少 Vitest 依赖的环境执行 |
| R01 产品 | 明确菜单区域、按钮权限和自动化边界：标注人员处理难例审核，管理人员编排聚类/批次/冻结 | 已写入 `labelu/docs/7gai_run_0152_active_learning_global_scope.md` |

独立记录：`labelu/docs/7gai_run_0152_active_learning_global_scope.md`。当前 Alembic 唯一 head 为 `z9f3a4b5c6d7`。本轮仍未提交、未推送、未部署；平台整体保持本地 L1。生产放行前仍需同版本 MySQL 迁移、历史数据重复检查、2/10/20/50 并发幂等、S3/AICM 对账和线上菜单/API 验证。
# RUN-0156 训练僵尸回收原子 fencing（本地 L1）

- 责任角色/工作包：R03 架构、R04 后端、R06 模型算法、R09 测试、R10 运维；WP2。
- 交付：旧版 `reconcile_stale_processing_jobs` 改用 `update_status_if_execution_lease_free`，在数据库条件更新中校验执行租约；新增 Worker 持租约时旧回收不得覆盖状态、租约过期后可回收的专项测试。
- 验证：新增专项 `1 passed`；训练 Worker 目标模块 `py_compile` 通过。SQLite 多进程证据不替代同版本 MySQL/InnoDB 证据。
- 生产边界：训练 Worker、GPU 真实训练、MySQL 20 并发锁等待、systemd 重启接管均未验收，生产开关保持关闭。
- 状态：未提交、未推送、未部署；独立记录见 `labelu/docs/7gai_run_0156_training_reconcile_fencing.md`。

## RUN-0157 服务探针与 SPA 回退边界

- 责任角色/工作包：R03 架构、R04 后端、R09 测试、R10 运维；WP5。
- 交付：健康、就绪、版本和 OpenAPI 路由在 SPA 静态回退前注册，避免线上把 HTML 当健康接口响应。
- 验证：探针专项 `2 passed`，中文 OpenAPI 专项 `8 passed`；生产部署后仍需重新探针。
- 状态：本地 L1，未提交、未推送、未部署；独立记录见 `labelu/docs/7gai_run_0157_service_probe_spa_boundary.md`。

## RUN-0158 主动学习旧项目测试迁移

- 责任角色/工作包：R01 产品、R03 架构、R04 后端、R09 测试；WP4/WP5。
- 交付：旧测试改用 `scope_key=global` 和当前两类登录角色，保留批次幂等、评分、审核快照、样本消费和聚类过滤覆盖；一期自动训练计划写入仍保持硬封锁。
- 验证：主动学习旧专项 `9 passed`。
- 状态：本地 L1，未提交、未推送、未部署；独立记录见 `labelu/docs/7gai_run_0158_active_learning_legacy_test_migration.md`。

## RUN-0159 主动学习测试库结构对齐

- 责任角色/工作包：R03 架构、R04 后端、R09 测试；WP0/WP4。
- 交付：测试启动时仅在旧 `test.db` 存在项目外键非空约束时重建主动学习三张测试表，按当前 ORM 保持全局作用域，不修改生产数据库。
- 验证：主动学习旧专项、全局专项和健康探针联合 `14 passed`。
- 状态：本地 L1，未提交、未推送、未部署；独立记录见 `labelu/docs/7gai_run_0159_active_learning_test_schema_alignment.md`。

## RUN-0160 角色化交付联合回归与放行边界

- 责任角色/工作包：R03/R04/R06/R07/R09/R10；WP2/WP3/WP5。
- 交付：联合回归主动学习/健康/迁移 `16 passed`、训练 Worker `11 passed`、BM1688 编译/Worker/fencing `51 passed`；模型中心/训练中心前端 `41 passed`，类型检查通过；`compileall` 和 `git diff --check` 通过。并行启动时出现的 4 个 BM1688 ready 队列超时，串行单测及完整联合套件均通过，列为测试夹具启动窗口审计项，不计为生产通过。
- 生产边界：未连接生产 MySQL、S3、AICM、TPU-MLIR、GPU 或 BM1688；未执行 systemd；所有独立 Worker 和自动训练生产开关保持关闭。
- 独立记录：`labelu/docs/7gai_run_0160_role_based_regression.md`。
- 状态：本地 L1，未提交、未推送、未部署；继续等待正式迁移、同版本 MySQL 并发、真实设备和外部回流证据。

## RUN-0169 预发布 JSON 探针与一期禁用写入口门禁

- 责任角色/工作包：R03 架构、R04 后端、R09 测试、R10 运维；WP0/WP5/OPS。
- 交付：共享 OpenAPI 契约新增退休写探针；预发布脚本拒绝健康/版本/OpenAPI 的非 JSON 响应，
  并验证 `POST /api/v1/edge-device-jobs` 只能返回退休门禁 403 或入口不存在 404；发布证据
  校验要求记录 `response_kind=json` 和退休写探针。
- 验证：`labelu/tests/scripts/test_staging_acceptance.py` 与
  `scripts/tests/test_verify_staging_and_hil.py` 共 `23 passed`；覆盖 SPA HTML 200、退休写入、
  缺少 JSON 类型和缺少退休探针的失败关闭。
- 影响文件：`scripts/ops/7gai_openapi_contract.json`、`scripts/staging_acceptance.py`、
  `scripts/verify_staging_and_hil.py`、对应测试及独立记录。
- 生产边界：未连接生产/预生产，未执行写请求、设备操作、S3/AICM 或部署；本地 L1，未提交、
  未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0169_staging_probe_retired_write_gate.md`。
- 上游本轮生产只读阻断：`healthz/readyz/version` 均为 HTTP 200 `text/html`，`/openapi.json`
  为 HTTP 200 JSON，`/api/v1/edge-device-jobs` 仍暴露；这些结果不计入本地通过，待提交部署后
  重新执行 JSON 探针和退休写入口拒绝验证。

## RUN-0178 模型目录角色与下载禁用门禁

| 角色 | 本轮交付 | 验收结果 |
|---|---|---|
| R01 产品 | 明确模型发布、目录保留、AICM `model_set_id` 稳定标识和“问题模型只禁下载、不回滚”边界 | 本地链路核对完成 |
| R04 后端 | 核对发布成功写入本地模型目录、长期模型包、下载开关条件更新与管理员写权限 | 既有后端专项保持通过，未改生产 |
| R08 前端 | 模型目录按 `/users/me` 区分管理人员与标注人员；标注人员只查看/下载，管理区隐藏；所有操作改为居中弹窗 | 页面代码完成 |
| R09 测试 | 模型目录路由、发布目录、下载禁用、并发开关和本地包下载回归 | 后端 `22 passed`，前端静态契约 `2 passed` |

独立记录：`labelu/docs/7gai_run_0178_model_catalog_role_download_gate.md`。

生产边界：未连接生产、未写 AICM、未下载生产模型、未迁移数据库、未提交、未推送、未部署。生产放行前仍需两类账号、AICM `model_set_id`、目录下载禁用和同版本 MySQL 并发证据。

## RUN-0176 服务器 Ed25519 签名与边端清单验签门禁

- 责任角色/工作包：R03 架构、R04 后端、R07 BM1688 边端、R09 测试、R10 运维；WP3/WP5。
- 交付：服务器使用 Ed25519 私钥签名边端 `run_config`，Board Agent 与 SSH runner 共用公钥验签和制品 SHA256/FP32/FP16/INT8 完整性门禁；绑定导出编号、Profile 摘要、执行世代，验签失败不得加载模型。
- 验证：清单签名、Board Agent 打包、SSH runner 回归共 `15 passed`。
- 生产边界：未配置生产私钥/设备公钥，未连接真实 BM1688、AICM、S3，未提交、未推送、未部署；需真机篡改包和旧世代重放证据后才可放行。
- 独立记录：`labelu/docs/7gai_run_0176_ed25519_edge_manifest_gate.md`。

## 角色开发执行手册（持续生效）

- 新增文档：`labelu/docs/7gai_role_execution_playbook.md`。
- 本轮交付：固定两类用户、任务与标注/训练中心/模型中心/进化中心/平台设置的按钮权限和验收条件，明确 R01-R10 的主责边界，以及每个 RUN 必须记录的代码、测试、并发、真实环境证据和 L0-L4 等级。
- 状态：文档契约测试 `1 passed`；不修改业务接口、数据库或生产数据；未提交、未推送、未部署。

## RUN-0177 固定评估集与三精度掉点契约收口

| 角色 | 本轮交付 | 验收结果 |
|---|---|---|
| R06 模型算法 | 固定评估集身份、FP32 基线、FP16/INT8 绝对 mAP50 掉点规则；默认值标记为工程起点待生产标定 | 本地规则校验通过，真实生产标定未完成 |
| R07 BM1688 边端 | run_config 固定评估签名、清单摘要、三精度同集约束；旧 SSH 路径补齐图片/标注清单摘要 | 本地边端契约通过，真实 BM1688 未验收 |
| R09 测试 | 规则非法值、清单篡改、三精度评估签名不一致和掉点门禁回归 | `26 passed`；`py_compile`、`git diff --check` 通过 |
| R03/R04 | 导出清单保存质量规则快照；Board Agent/SSH 复用同一规则 | 本地代码链路完成 |

本轮独立记录：`labelu/docs/7gai_run_0177_fixed_eval_quality_gate.md`。

生产边界：未连接真实 TPU-MLIR、BM1688、生产 MySQL、AICM 或 S3；未执行生产写入、真机推理、阈值标定或部署。固定评估集同口径和阈值必须在真实代表模型上复验后，才能从本地 L1 进入预生产验收。

## RUN-0180 迁移发布闸门与 MySQL 沙箱证据收口

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP2 | 只读检查唯一 Alembic head、ORM 字段/索引来源、候选迁移正式合入边界 | 本地逻辑完成；当前候选仍在 docs，闸门按设计失败关闭 |
| R04 后端 | WP2 | 新增 `migration_release_gate` 模块和命令行入口，校验租约/`worker_action` 正式迁移来源 | 本地专项覆盖字段索引闭包 |
| R09 测试 | WP5 | 危险 URL、证据缺失、候选停留 docs、完整证据和部署顺序回归 | 新增专项测试；真实 MySQL 未执行 |
| R10 运维 | WP2/WP5 | 共享 `migrate_deploy.sh` 在 Alembic 前调用只读闸门；无沙箱/备份/锁证据不得连接 `DATABASE_URL` | 静态契约完成；Ubuntu/systemd 未执行 |

独立记录：`labelu/docs/7gai_run_0180_migration_release_gate.md`。本轮不连接生产、不执行数据库 DDL、不写业务数据。

当前门禁结果：正式 Alembic 只有一个 head，ORM 字段/索引来源闭合；但 `gai_p1_08` 仍位于 `labelu/docs/7gai_phase_one_migration_candidates`，且没有真实备份恢复和 MySQL 锁等待证据，因此整体 `fail-closed`，不能放行迁移。

提交/推送/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。

## RUN-0185 执行租约候选与正式 Alembic 链只读对账

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP2 | 对账生产基线、正式唯一 head、`z4...` 父链与候选 `gai_p1_08` 父链 | 当前 head `z9f...` 可从生产基线 `h7...` 到达；候选父版本 `gai_p1_07` 不存在，直接收口阻断 |
| R04 后端 | WP2 | 只读比较三表租约字段、扫描索引、`worker_action` 和列顺序 | 字段集合和索引语义与正式 `z4...` 完全一致，但 revision/父链不一致 |
| R09 测试 | WP5 | 固化候选/正式迁移差异和未知 parent 的回归 | `2 passed`；审计命令按预期退出 1 |

独立记录：`labelu/docs/7gai_run_0185_execution_lease_candidate_alignment.md`；审计脚本：`labelu/scripts/audit_execution_lease_candidate_alignment.py`。

本轮不移动或删除候选，不修改正式 Alembic，不连接 MySQL/生产。最小阻断证据是候选 `down_revision=gai_p1_07` 不在正式图；候选已标记由正式 `z4...` 覆盖，不再新增重复迁移。后续只需完成脱敏快照和同版本 MySQL 演练证据。

## RUN-0195 正式执行租约字段闭包审计与新增迁移阻断

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP2 | 核对正式唯一 head、生产基线可达性和三张执行表 ORM/迁移字段闭包 | head 唯一；租约字段、扫描索引和辅助字段均已由正式 `z4...` 覆盖 |
| R04 后端 | WP2 | 核对 `worker_action`、GPU/运行时字段和正式来源，判断是否需要新增迁移 | 无缺失；新增空/重复迁移会增加漂移风险，不形成草案 |
| R09 测试 | WP5 | 新增正式闭包和“无安全新增迁移”阻断证据测试 | 专项通过；只读，无数据库连接 |
| R10 运维 | WP2/WP5 | 维护生产 DDL 前置条件和证据边界 | MySQL `SHOW CREATE/SHOW INDEX`、备份恢复、锁等待和 20 并发仍待真实沙箱 |

独立记录：`labelu/docs/7gai_run_0195_formal_execution_lease_closure_blocker.md`。

结论：当前不新增正式 Alembic 草案；候选 `gai_p1_08` 已由 `z4a5b6c7d8e9` 覆盖并标记 `SUPERSEDED_BY`，其未知父版本仅用于阻断其再次执行。静态闭包通过不等于生产已迁移，缺少真实 MySQL 结构/备份/锁证据时继续 fail-closed。

## RUN-0179 边端执行世代持久化与防重放

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP3 | 明确执行世代不能按摘要字典序比较；按 AICM 模型或导出编号确定范围，以签名序号或清单签发时间排序，缺少顺序时失败关闭 | 本地契约完成 |
| R04 后端 | WP3 | Board Agent/SSH 包补充完整制品清单、Ed25519 签名、世代范围和签发时间；现代清单的服务端证据门禁要求状态存在且与清单摘要一致 | 本地代码完成 |
| R07 BM1688 边端 | WP3 | 设备持久状态文件、并发锁、损坏拒绝、原子写、同代幂等和旧代拒绝；SSH 与 Board Agent 共用完整清单门禁 | 本地专项完成，真机待验收 |
| R09 测试 | WP5 | 覆盖首代、新代、旧代、同代、不可比较、损坏状态、签发时间排序、双并发幂等、SSH 清单签名路径及服务端证据门禁 | 执行世代专项 `7 passed`；证据专项 `8 passed`；联合脚本回归 `43 passed`；SSH 联合专项 `12 passed` |
| R10 运维 | WP3/WP5 | 约定设备状态目录、权限、重启和并发验收证据 | 未执行 Ubuntu/BM1688 真机 |

独立记录：`labelu/docs/7gai_run_0179_edge_execution_generation_persistence.md`。

生产边界：未连接生产、未写生产状态、未操作 AICM/S3/TPU-MLIR/BM1688，未提交、未推送、未部署。生产放行前必须在真实设备完成状态目录权限、双进程并发、重启接管和损坏文件失败关闭证据。

## RUN-0196 前端菜单、弹窗与两类角色契约收口

| 角色 | 本轮交付 | 验收结果 |
|---|---|---|
| R01 产品 | 无项目一期六菜单、两类角色菜单差异、标注人员可发布模型 | 本地规则完成 |
| R08 前端/UI | 平台壳测试去项目化；回流/模型详情统一居中弹窗；统一管理人员和系统标识文案 | 本地代码完成 |
| R09 测试 | 新增静态菜单/中文/无抽屉门禁；重写条件浏览器 E2E，覆盖菜单、弹窗、发布和兼容跳转 | 静态 `6 passed`；浏览器 `9 skipped`（无本地服务） |
| R10 运维 | 预留隔离本地服务、三视口和两类账号执行入口 | 未执行 |

独立记录：`labelu/docs/7gai_run_0196_frontend_menu_modal_role_contract.md`。

生产边界：本轮未连接生产数据库、S3、AICM 或 BM1688，未提交、未推送、未部署。真实浏览器、两类账号、模型下载禁用和生产探针仍是后续放行条件。

## RUN-0198 测试库结构兼容与自动派发联合回归

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP2 | 对齐旧 SQLite 测试库 `task_annotation_contract.label_scope_mode` 与 ORM/迁移语义 | 本地结构兼容完成；不改变正式 Alembic |
| R04 后端 | WP2/WP3 | 验证租约失败关闭、fencing token、边端任务原子创建和 Outbox 幂等 | 联合回归覆盖通过 |
| R09 测试 | WP5 | 修复持久化测试库漂移；执行 reconciliation 与自动训练/边端联合回归 | `17 passed`；联合 `93 passed` |
| R06/R07 | WP3 | 复核训练事件与 BM1688 边端打包/恢复契约 | 本地通过；真实训练节点和真机未执行 |

独立记录：`labelu/docs/7gai_run_0198_test_schema_compat_and_dispatch_regression.md`。

生产边界：本轮未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688，未提交、未推送、未部署。真实 MySQL 迁移、备份恢复、20 并发领取、锁等待、Worker 重启接管和真机证据仍是后续放行条件。

## RUN-0200 一期车牌矩形框训练入口收口

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R06 模型算法 | WP0/WP3 | 车牌新训练只允许普通目标检测矩形框；不使用号码 OCR、四角点、Pose 或多边形 | 本地契约完成 |
| R04 后端 | WP1/WP3 | 新任务应用复合车牌预设、普通任务首次引入复合配置均失败关闭；历史复合任务保留兼容 | 后端专项通过 |
| R08 前端/UI | WP1/WP3 | 复合预设标注历史兼容并禁用新建；提示使用“车牌/数字位置”矩形框标签 | 前端静态契约通过 |
| R09 测试 | WP5 | 预设/任务更新、车牌训练兼容和页面入口回归 | `36 + 54 + 8 passed` |
| R07/R10 | WP3/WP5 | 真实矩形框车牌任务的三精度编译、BM1688 真机和两类账号验证 | 尚未执行 |

独立记录：`labelu/docs/7gai_run_0200_plate_rect_only_phase_one.md`。

生产边界：本轮未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688，未提交、未推送、未部署；历史复合模型和数据未删除。

## RUN-0199 共享平台组件去项目化与通知按需加载（WP0/WP2/WP5）

| 责任角色 | 开发/验收边界 | 当前状态 |
|---|---|---|
| R01 产品 | 明确一期页面不以项目为用户维度，保留规则验签历史兼容边界 | 本地 L1 完成 |
| R03/R04 架构/后端 | 核对 ResourcePage 双模式、通知无项目接口和验签内部 `project_id` 兼容 | 本地 L1 完成；未连接生产接口 |
| R08 前端/UI | 共享资源页、通知中心、主布局和验签证据去可见项目化；通知按打开时加载 | 本地类型检查通过 |
| R09 测试 | 修复通知请求契约；静态平台契约和通知单测 | `5 passed` + `2 passed` |
| R10 运维 | 真实浏览器、三视口、API、并发和生产探针 | 未执行 |

独立记录：`labelu/docs/7gai_run_0199_shared_platform_components_no_project.md`。

本轮未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688，未提交、未推送、未部署。历史数据库字段和规则接口兼容参数未删除；生产放行前仍需完成真实 API/账号回放和容量证据。

## RUN-0212 边端旧入口与全局回流测试契约迁移（WP5）

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R09 测试 | WP5 | 迁移边端旧作业、第三方审计、回调派发和难例回流测试；移除项目创建与 `StrategyTaskIntegration.project_id` 夹具；补充退休入口无副作用断言 | 专项 `13 passed`；`git diff --check` 通过 |
| R04 后端 | WP1/WP3 | 提供现行一期退休门禁、全局回流和历史兼容字段契约 | 未改生产代码；按现有实现验收 |
| R10 运维 | WP5 | 生产旧入口探针、真实 S3 回流包和并发回放 | 尚未执行 |

独立记录：`labelu/docs/7gai_run_0212_retired_edge_and_global_feedback_test_contracts.md`。

生产边界：本轮未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688，未提交、未推送、未部署。自动训练计划写入开关保持关闭。

## RUN-0215 一期去项目化测试库结构隔离

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP0 | 确认一期全局数据链不恢复项目默认值，历史项目字段只兼容 | 规则确认 |
| R03 架构 | WP2 | 核对正式去项目化迁移已覆盖媒体、供应商任务和工作流 | 不新增重复迁移 |
| R04 后端 | WP1/WP2 | 保持 ORM 三表 `project_id nullable=True` 和全局写入服务不变 | 现有实现符合一期 |
| R09 测试 | WP5 | 默认测试库按进程和启动时间隔离；历史项目筛选测试保留二期跳过契约 | 去项目化专项 `43 passed`；内部全量 `2283 passed, 25 skipped` |
| R10 运维 | WP5 | 记录本地测试库与生产数据库边界 | 未连接生产 |

独立记录：`labelu/docs/7gai_run_0215_test_schema_fresh_database_isolation.md`。

本轮仅调整测试夹具和历史测试契约，未修改生产代码、数据库迁移或运行时配置；本地全量 `2283 passed, 25 skipped`，前端类型检查和构建均通过。生产只读探针显示健康路径回退 HTML；实际业务路由未登录返回 401，尚未完成两类账号验收；未提交、未推送、未部署。

## RUN-0217 角色团队本地交付、Outbox 路由契约与 HTTP 验收

| 责任角色 | 本轮交付 | 验收结果 |
|---|---|---|
| R01/R03 | 明确自动训练 Outbox 是一期保留的内部操作，普通工作流控制面仍退休 | 契约规则完成 |
| R04 | 统一 `staging_acceptance.py` 与 `deploy.sh` 的允许路径例外 | 本地脚本通过 |
| R08 | 复核角色菜单、中文按钮、居中弹窗和构建基线 | 类型检查与构建通过 |
| R09 | 增加 Outbox 允许路径及普通工作流拒绝边界测试 | `11 + 5 passed` |
| R10 | 使用隔离本地服务执行真实 HTTP 只读验收 | 全部必需探针通过，退出码 `0` |

独立记录：`labelu/docs/7gai_run_0217_role_team_frontend_build_baseline.md`。

本轮仍未提交、未推送、未部署。生产 MySQL、S3、AICM、TPU-MLIR、BM1688 真机、两类生产账号和部署后探针仍是后续放行条件。

## RUN-0218 补充：匿名探针与部署阻断对账

- 责任角色：R03 架构、R09 测试、R10 运维；工作包 WP2/WP5/OPS。
- 只读结果：线上 `/openapi.json` 为 295 条旧部署路径；一期 `datasets`、`algorithms`、`model-versions`、`workspace/summary` 缺失；旧 `/api/v1/edge-device-jobs` 仍暴露；健康、就绪、版本接口返回网页文本而非 JSON。
- 结论：生产仍为旧版本，不能把本地 L1 结果写成生产完成；必须在提交/部署后重新执行主链、退休路径、两类账号和探针验收。
- 独立记录：`labelu/docs/7gai_run_0218_production_readonly_probe.md`；管理人员登录后的完整路由矩阵见 `labelu/docs/7gai_run_0218_production_readonly_route_probe.md`。
- 本轮无生产写入、无迁移、无 S3/AICM/BM1688 调用，未提交、未推送、未部署。

## RUN-0219 任务页历史项目操作下线

- 责任角色：R01/R03/R04/R08/R09/R10；工作包 WP0/WP1/WP2/WP4/WP5/OPS。
- 需求：一期无项目/业务空间，任务列表不得查询项目、显示项目归属或触发项目批量赋值。
- 代码：移除主任务页 `/v1/projects` 查询、`/v1/tasks/project-assignment` 调用和“归属项目”控件；将 `/rules`、`/annotation-review` 历史页面改为兼容跳转；旧训练页不再转发 `project_id`；后端历史字段和接口保留兼容。
- 验证：平台 UI 静态契约 `7 passed`；前端类型检查通过；生产构建退出码 `0`，4094 个模块转换，`29.81s`；生产浏览器待补。
- 独立记录：`labelu/docs/7gai_run_0219_legacy_task_project_control_retired.md`。
- 状态：本地 L1，未提交、未推送、未部署。

## RUN-0250 模型中心编号防串与禁止回滚语义收口

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03 产品与架构 | WP3/WP4 | 固定“发布后不回滚，只停止本平台后续下载”；历史隐藏/撤销状态只读兼容 | 业务和接口契约完成 |
| R04/R05 后端与外部平台 | WP4 | 相同 AICM 数字编号跨环境改绑失败关闭；撤回式目录更新被拒绝；下载开关条件更新；最终包原子只安装一次 | 本地代码完成 |
| R06 模型算法 | WP3 | 复核 PT -> ONNX -> FP32/FP16/INT8 BModel -> BM1688 -> AICM 主链未被目录治理改动 | 模型中心联合回归通过 |
| R08 前端/UI | WP4 | 移除回滚含义的编辑控件；停止/恢复下载使用居中确认弹窗和中文后果说明 | 页面静态契约通过 |
| R09 测试 | WP5 | 覆盖跨环境同号、禁止撤回、下载并发旧值、并发模型包、第三方别名编号对齐 | 目录专项 `29 passed`；联合 `290 passed, 1 skipped`；条件浏览器 `2 skipped`（未设置地址） |
| R10 运维 | OPS | 生产 AICM/MySQL/三精度编译/BM1688/两类账号验收 | 尚未执行 |

独立记录：`labelu/docs/7gai_run_0250_model_catalog_identity_and_no_rollback.md`。

生产边界：本轮未连接生产数据库、AICM、S3、TPU-MLIR 或 BM1688，未提交、未推送、未部署；真实外部平台已下载文件不具备回收能力。

## RUN-0262 生产验收探针写入安全修复与误写事件记录

- 责任角色：R01 产品、R03 架构、R04 后端工具、R09 测试、R10 运维。
- 本地交付：`scripts/staging_acceptance.py` 的退休写接口探针默认跳过；新增显式危险参数 `--allow-retired-write-probes`；`--mutating` 继续失败关闭；测试 `11 passed`。
- 生产事实：旧版本生产 `/api/v1/edge-device-jobs` 的 `POST` 探针返回 `201`，说明旧部署接受了写入。证据见 `.tmp/production-readonly-acceptance-20260920-admin.json`。
- 处置边界：已停止生产写请求；未继续查询、删除或补偿。运维必须依据时间、路径、`export_job_id=1` 和日志/数据库确认是否产生记录并按流程处理。
- 放行结论：本地 L1；生产验收失败，旧版本不得继续写探针或宣称无副作用。未提交、未推送、未部署，等待用户明确“提交代码”。
- 独立记录：`labelu/docs/7gai_run_0262_staging_probe_write_safety.md`。

## RUN-0263 评估执行代次迁移闸门补漏

- 责任角色：R03 架构、R04 后端工具、R09 测试、R10 运维。
- 发现：迁移闸门只检查 `gai_p1_08`，漏检 `gai_p1_09` 评估执行代次候选，存在误报正式收口的风险。
- 修复：拆分调度租约和评估代次两个候选迁移检查；评估候选当前无正式替代修订号，闸门明确失败关闭。
- 验证：迁移闸门专项 `10 passed`；只读发布闸门退出码 `1`，阻断原因符合预期。
- 结论：正式评估迁移、MySQL 沙箱、备份恢复、锁等待及 `2/10/20/50` 并发仍未完成；未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0263_migration_gate_eval_candidate_closure.md`。

## RUN-0266 BM1688 制品下载路径与清单校验修复

- 责任角色：R04 后端、R06 模型算法、R09 测试、R10 运维。
- 发现：指定 `filename` 的 BModel 下载缺少导出目录归属和制品清单校验，存在路径穿越及下载未登记文件风险。
- 修复：绝对路径/`..` 返回 `403`；解析路径必须位于当前导出 `models` 目录；文件必须属于该导出清单；默认数据库路径也执行同样校验。
- 验证：导出恢复专项 `43 passed`；BM1688 编译、边端业务和路由联合专项通过，含 1 个既有跳过项。
- 生产边界：未连接生产、未访问生产文件、未部署；真实 BM1688、Board Agent、签名和固件证据仍未完成。
- 独立记录：`labelu/docs/7gai_run_0266_bm1688_download_path_guard.md`。

## RUN-0267 发布模型包摘要一致性门禁

- 责任角色：R04 后端、R05 外部平台、R09 测试、R10 运维。
- 发现：发布模型下载只校验路径和 ZIP 格式，没有在登记摘要存在时核对磁盘包 SHA256/大小。
- 修复：下载前校验 `package_size_bytes` 和 `package_sha256`；不一致返回 `409`；历史无摘要记录保持兼容。
- 验证：发布模型目录专项 `23 passed`。
- 生产边界：未连接生产、未对账生产包、未提交、未推送、未部署；部署后仍需执行目录/AICM/摘要对账。
- 独立记录：`labelu/docs/7gai_run_0267_published_package_digest_guard.md`。

## RUN-0268 后端全量回归与本轮交付汇总

- 责任角色：R04 后端、R06 模型算法、R09 测试、R10 运维。
- 全量命令：`python -m pytest labelu/tests -q --disable-warnings --tb=short`。
- 结果：`2654 passed, 56 skipped, 410 warnings`，退出码 `0`，耗时约 9 分钟。
- 结论：本轮本地安全修复和闸门补漏未破坏既有训练、评估、模型发布和边端业务测试；生产、正式迁移、MySQL/BM1688/AICM/S3 证据仍未完成。
- 独立记录：`labelu/docs/7gai_run_0268_backend_full_regression.md`。

## RUN-0269 只读验收探针与证据校验器契约同步

- 责任角色：R03 架构、R04 工具后端、R09 测试、R10 运维。
- 发现：RUN-0262 的默认跳过语义未同步到 `verify_staging_and_hil.py`，新证据会被旧规则误判。
- 修复：接受默认 `退休写接口探针` 的安全跳过记录，同时保留一次性预生产危险夹具的旧名称兼容；缺少两者之一仍失败。
- 验证：验收脚本和证据校验器专项 `27 passed`。
- 生产边界：未请求生产、未写数据库、未部署；旧生产误写事件仍需运维核查。
- 独立记录：`labelu/docs/7gai_run_0269_readonly_probe_evidence_contract.md`。

## RUN-0270 MySQL 2/10/20/50 并发矩阵编排入口

- 责任角色：R03 架构、R04 后端工具、R09 测试、R10 运维；工作包 WP2/WP5/OPS。
- 交付：新增 `scripts/rehearse_mysql_concurrency_matrix.py`，固定编排 `2/10/20/50` 四档，输出迁移闸门兼容矩阵，任一档失败整体失败。
- 安全：默认只读失败关闭；仅允许本机 `labelu_migration_sandbox*`，并要求显式演练、修改确认和环境确认；不连接业务库/生产。
- 验证：矩阵入口、单档执行器和迁移闸门专项 `20 passed`；默认命令退出码 `2`、状态 `BLOCKED`，未触发数据库连接。
- 遗留：本机没有已授权沙箱、脱敏快照、备份恢复目标，真实四档证据尚未生成；正式迁移继续阻断。
- 独立记录：`labelu/docs/7gai_run_0270_mysql_concurrency_matrix_orchestrator.md`。

## RUN-0271 BModel 下载制品清单与摘要校验

- 责任角色：R04 后端、R06 模型算法、R07 BM1688、R09 测试；工作包 WP3/WP4/WP5。
- 发现：BModel 下载只校验目录和文件名，未校验 `artifact_manifest_json` 中登记的文件摘要。
- 修复：清单存在时强制校验 BModel 相对路径、`bytes` 和 SHA256；清单异常或文件被替换时拒绝下载；历史无清单记录兼容读取。
- 数据/API影响：不新增表和接口；普通下载、边端验证包、AICM 发布继续复用同一导出制品目录。
- 验证：BM1688 导出/边端联合专项 `52 passed, 114 warnings`。
- 生产边界：未连接生产、未读取生产包、未部署；真实 BM1688、签名、公钥和 AICM 对账仍是下一依赖。

## RUN-0278 创建任务与训练幂等契约收口

- 责任角色：R01 产品、R03 架构、R04 后端、R08 前端/UI、R09 测试、R10 运维；工作包 WP0/WP1/WP2/WP4/WP5/OPS。
- 交付：任务创建、旧 YOLO 创建、`/v1/yolo-training-tasks`、`/v1/training/jobs` 支持可选 `Idempotency-Key`；同键同请求复用，冲突返回 `409`，数据库唯一约束和保存点处理并发竞态。
- 前端：任务创建和训练弹窗生成稳定键，网络重试复用，重新打开或切换训练维度重建；新增请求服务和契约测试。
- 验证：后端专项 `32 passed, 160 warnings`；任务/自动训练只读回归 `39 passed`；角色团队统一训练专项 `36 passed`；前端幂等 `5 passed`；训练页面 `30 passed`；后端全量 `2666 passed, 56 skipped, 419 warnings`；前端生产构建成功；`compileall` 和 `git diff --check` 通过。
- 类型检查：仅剩仓库既有 `src/pages/404.tsx:29 TS2589`，本轮未新增错误。
- 并发/失败关闭：请求摘要冲突拒绝；唯一键赢家单写；输家不重复冻结、不重复创建、不重复派发；不带键仍保留主动新建；自动训练计划关闭时不写任务创建 Outbox。
- 真实环境证据：尚未连接生产、MySQL、S3、AICM、TPU-MLIR 或 BM1688；生产仍为旧部署。
- 状态：本地 L1；未提交、未推送、未部署。下一依赖为同版本 MySQL 并发演练、真实训练节点回放、两类账号和生产只读验收。
- 独立记录：`labelu/docs/7gai_run_0278_create_idempotency_contract.md`。
- 独立记录：`labelu/docs/7gai_run_0271_bmodel_manifest_download_gate.md`。

## RUN-0279 生产只读路由复核与旧部署差异

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP0/WP5 | 复核一期 OpenAPI 与生产路径差异 | 发现四个一期路由缺失，旧边端路径仍暴露 |
| R04 后端 | WP1/WP3/WP4 | 复核健康检查、版本、任务、训练、模型、S3 和边端只读入口 | 生产仍为旧部署；健康检查返回 SPA HTML |
| R09 测试 | WP5 | GET-only、未认证、无请求体、跳过退休写探针 | 证据文件生成；未产生生产写入 |
| R10 运维 | OPS | 给出部署放行判断 | 生产失败关闭，不能宣称一期上线 |

- 生产地址：`http://113.141.72.175:10000`。
- 证据：`.tmp/7gai-production-readonly-route-audit-0279.json`。
- 本地状态：L1，未提交、未推送、未部署。
- 独立记录：`labelu/docs/7gai_run_0279_production_readonly_route_recheck.md`。

## RUN-0280 第三方 S3 回流副作用租约守卫收口

- 责任角色：R03 架构、R04 后端、R05 外部平台、R09 测试、R10 运维；工作包 WP2/WP4/WP5/OPS。
- 需求编号：`S3-LEASE-07`、`S3-LEASE-08`、`S3-CONC-03`。
- 交付：`update_by_worker_lease` 增加未过期条件；新增 `worker_lease_is_valid`；group/row 导入处理接入 `lease_guard`，在每包、每样本、任务、附件、样本、预标注、自动预标注和旧投影副作用前检查；失租异常不写失败或完成终态。
- 改动文件：`labelu/internal/adapter/persistence/crud_experiment_dataset_import.py`、`labelu/internal/application/service/experiment_dataset_import.py`、`labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py`、`labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py`。
- 验证：Worker 专项 `11 passed, 170.96s`；本轮新增过期写拒绝和中途失租专项 `2 passed, 113 warnings, 3.32s`；`compileall` 与 `git diff --check` 通过。
- 真实环境边界：未连接生产 MySQL、第三方 S3、AICM、systemd、GPU、TPU-MLIR 或 BM1688；生产只读路由证据显示未登录 S3 配置接口返回 `401`，不能据此宣称生产回流已验证。
- 状态：本地 L1；未提交、未推送、未部署。下一依赖为同版本 MySQL 过期写/双 Worker 接管/`2/10/20/50` 并发，授权生产只读 S3 对账和真实问题包 AICM `model_set_id` 三方核验。
- 独立记录：`labelu/docs/7gai_run_0280_s3_import_lease_guard.md`。

## RUN-0281 BM1688、Board Agent/SSH、三精度与 AICM 生产差距审计

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP3/WP5 | 对照本地代码、专项测试和生产 GET-only 证据，核对 PT -> ONNX -> 三精度 BModel -> BM1688 -> AICM 主链 | 本地 L1 契约较完整；生产 L3 证据缺失，失败关闭 |
| R06 模型算法 | WP3 | 明确 FP32/FP16/INT8 同一模型版本规则、固定评估集和工具链/qtable/校准集身份证据要求 | 规则合理但阈值仍需真实代表模型标定；不得将模拟结果作为生产证据 |
| R07 BM1688 边端 | WP3 | 核对 BM1688 Profile、设备身份、Board Agent/SSH 通道、租约和 fencing 现状 | Board Agent 尚未成为无 SSH 依赖的生产主路径；无真实三精度加载、断网接管证据 |
| R09 测试 | WP5 | 复核固定评估集、精度/P95/短稳、制品摘要、设备回报和 AICM 对账验收条件 | 生产验收未完成；不得开启生产 Worker 或自动回流 |
| R10 运维 | OPS | 核对生产版本、Worker、MySQL、AICM/S3 三方对账和开关 | 当前生产只读路由证据 `passed=false`；未提交、未推送、未部署 |

- 关键发现：调度器当前对所有设备要求 SSH host/user/password，`BOARD_AGENT_PRIMARY_ENABLED` 默认关闭；仅设置 `validation_channel=board_agent` 不能证明板端 Agent 已切主。
- 关键发现：Profile 结构、制品清单和摘要门禁已存在，但真实 TPU-MLIR 版本、镜像摘要、运行时 ABI、qtable/校准集摘要和真实权重摘要尚未形成生产硬证据。
- 关键发现：AICM `model_set_id` 作为本平台公开模型 ID 的逻辑正确，但尚未完成 AICM 回执、本地模型目录、保留包摘要和第三方 S3 metadata 的真实三方对账。
- 下一工作包：`WP3-HIL-01`，使用一个真实 YOLO11 代表模型和一台真实 BM1688，先在预生产受控 SSH 兼容通道完成三精度编译、固定评估集、真机性能/稳定性、设备租约/fencing、AICM/本地目录/S3 对账；Board Agent 完成身份、验签、断网重领和重复回报证据后再切主。
- 生产阻断：`BOARD_AGENT_ENABLED`、`BOARD_AGENT_PRIMARY_ENABLED`、独立边端 Worker 和自动回流继续失败关闭；不得以 HTTP 200、模拟器、SQLite 或本地假 BModel 宣称生产验收。
- 独立记录：`labelu/docs/7gai_run_0281_bm1688_board_agent_production_gap_audit.md`。

## RUN-0282 S3 租约修复后端全量回归

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R04 后端 | WP2/WP4 | 对 S3 租约守卫及关联后端链路执行全量回归 | 通过 |
| R09 测试 | WP5 | 保存全量测试结果和退出码 | `2668 passed, 56 skipped, 419 warnings`，退出码 `0` |
| R10 运维 | OPS | 复核环境边界 | 未连接生产、未迁移、未提交、未推送、未部署 |

- 命令：`python -m pytest labelu/tests -q --disable-warnings --tb=short`。
- 耗时：`633.52s`。
- 独立记录：`labelu/docs/7gai_run_0282_s3_full_regression.md`。

## RUN-0283 本地 HTTP 路由验收与 BM1688/HIL 环境盘点

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP3/WP5 | 在真实本地 HTTP 进程核对健康、OpenAPI 和退休入口 | 只读路由审计通过，OpenAPI 347 条路径 |
| R07 BM1688 | WP3 | 盘点 TPU-MLIR、BM1688 工具和设备证据 | 本机无可直接调用工具/板卡证据，真实 HIL 阻断 |
| R08 前端/UI | WP5 | 核对 SPA 不覆盖健康探针 | `/healthz`、`/readyz`、`/version` 均为 JSON |
| R09 测试 | WP5 | 执行 GET-only 本地验收脚本 | `passed=true`，旧 `edge-device-jobs` 返回 404 |
| R10 运维 | OPS | 核对本地与生产边界 | 使用隔离 SQLite；未连接生产、未迁移、未提交、未部署 |

- 证据：`.tmp/7gai-local-readonly-route-audit-current.json`。
- 独立记录：`labelu/docs/7gai_run_0283_local_http_and_hil_inventory.md`。

## RUN-0284 显式环境文件优先级与本地数据库隔离

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP0/WP2 | 分析 `LABELU_ENV_FILE` 与根目录 `.env` 覆盖关系 | 明确显式环境文件必须后加载 |
| R04 后端 | WP2 | 修复 Settings dotenv 文件顺序 | 显式环境文件覆盖兼容 `.env` |
| R09 测试 | WP5 | 增加冲突配置回归测试并执行本地 HTTP 复核 | 配置专项 `9 passed`；只读路由审计 `passed=true` |
| R10 运维 | OPS | 复核本地/生产数据库边界 | 不改生产配置，开发 MySQL 仍失败关闭 |

- 改动文件：`labelu/internal/common/config.py`、`labelu/tests/internal/common/test_config_env_bootstrap.py`。
- 本地证据：`.tmp/7gai-local-readonly-route-audit-env-precedence.json`；未连接生产、未迁移、未提交、未部署。
- 独立记录：`labelu/docs/7gai_run_0284_env_file_precedence.md`。

## RUN-0285 统一本地验收门禁清单

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03 | WP0/WP2 | 核对迁移单头、去项目审计和失败关闭规则 | 通过 |
| R04/R06/R07 | WP1/WP3 | 核对发布证据、Provider、Agent 和边端契约 | 通过 |
| R08 前端/UI | WP4/WP5 | 核对前端外部门禁语义 | 通过 |
| R09 测试 | WP5 | 执行统一本地门禁清单 | `8 PASSED`，`blocking=0`，退出码 `0` |
| R10 运维 | OPS | 保存工作树指纹和证据目录，确认外部环境未误判 | 未连接生产、未迁移、未提交、未部署 |

- 证据：`.tmp/7gai-local-gates-current/local-gates-20260920T095428Z.json`。
- 独立记录：`labelu/docs/7gai_run_0285_local_gate_inventory.md`。

## RUN-0286 前端类型检查、生产构建与平台界面契约验收

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP4/WP5 | 收口前端类型边界，避免国际化 Hook 类型递归 | `404.tsx` 显式兼容类型已增加 |
| R08 前端/UI | WP4 | 完成前端生产构建 | `pnpm run type-check` 通过；`pnpm run build` 通过 |
| R09 测试 | WP5 | 执行一期平台/UI静态契约和扩展页面契约 | Pytest `10 passed`；Node `5 passed` |
| R10 运维 | OPS | 保存本地验证边界和工具缺失信息 | Vitest 因当前依赖环境缺失未通过；未误判为成功 |

- 改动文件：`frontend/labelU-Kit/apps/frontend/src/pages/404.tsx`。
- 独立记录：`labelu/docs/7gai_run_0286_frontend_typecheck_build_and_ui_contract.md`。
- 真实环境边界：未连接生产、MySQL、S3、AICM、TPU-MLIR 或 BM1688；未提交、未推送、未部署。

## RUN-0287 前端修复后统一本地门禁复核

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03 | WP0/WP2 | 复核迁移、去项目和失败关闭门禁 | 通过 |
| R04/R06/R07 | WP1/WP3 | 复核发布证据、Provider、Agent 本地契约 | 通过 |
| R08 前端/UI | WP4 | 确认前端修复后外部门禁语义仍闭合 | 通过 |
| R09 测试 | WP5 | 执行统一本地门禁清单 | `blocking=0`，退出码 `0` |
| R10 运维 | OPS | 保存最新证据并确认外部环境未误判 | 本地 L1；未部署 |

- 证据：`.tmp/7gai-local-gates-current/local-gates-20260920T100532Z.json`。
- 独立记录：`labelu/docs/7gai_run_0287_local_gate_rerun_after_frontend_fix.md`。

## RUN-0288 生产只读路由复核

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构/R04 后端 | WP0/WP1 | 对账生产 OpenAPI 与一期必需路由、退休边端路径 | 生产仍为旧部署，四个一期路由缺失 |
| R09 测试 | WP5 | GET-only、未认证、无请求体只读探针 | 证据已生成；未执行生产写操作 |
| R10 运维 | OPS | 判定生产部署放行状态 | FAIL；健康探针返回 SPA HTML，旧边端路径仍登记 |

- 生产地址：`http://113.141.72.175:10000`。
- 证据：`.tmp/7gai-production-readonly-route-audit-0288.json`。
- 独立记录：`labelu/docs/7gai_run_0288_production_readonly_route_recheck.md`。
- 状态：未提交、未推送、未部署；训练/编译/边端/S3 Worker 生产开关继续失败关闭。

## RUN-0292 Board Agent 固定评估集与质量门禁

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R06 模型算法架构 | WP3 | Board Agent 评估包增加图片、YOLO/XML 标签、文件摘要和固定集签名 | 本地契约完成 |
| R07 BM1688 边端 | WP3 | `run_config` 写入固定集字段和质量规则；根 `manifest.json` 与 Board Agent 清单同摘要 | 本地契约完成 |
| R04 后端 | WP2/WP3 | SSH 固定集同步 `labeled_image_count`，与 Board Agent 使用同一门禁 | 本地代码完成 |
| R09 测试 | WP5 | 打包复用、篡改、缺标签门禁回归 | `18 passed`；固定评估包 `13 passed` |
| R10 运维 | OPS | 复核真实环境边界 | 未连接生产、未执行真实 BM1688 HIL、未提交、未部署 |

- 修改：`board_eval_pack.py`、`board_agent.py`、`edge_ssh_validation.py`、`edge_board_validate_runner.py` 及专项测试。
- 独立记录：`labelu/docs/7gai_run_0292_board_agent_fixed_eval_gate.md`。

## RUN-0298 自动样本预标注独立 Worker、租约 fencing 与并发限流

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构/迁移 | WP1/WP2 | `auto_label_job.lease_fencing_token`、原子递增领取、过期租约接管 | 本地 L1；同版本 MySQL/锁等待未完成 |
| R04 后端 | WP1 | API 开启独立 Worker 时只入队，不在 Web 进程启动长任务线程；续租、进度、终态校验 owner+token | 本地代码完成 |
| R09 测试 | WP5 | fencing 旧 token 拒绝、开关关闭、API 不启线程、既有自动预标注回归 | `17 passed` |
| R10 运维 | OPS | systemd、容量、真实模型、重启恢复和生产探针 | 未执行；未连接生产 |

- 代码：`auto_preannotate_worker_runtime.py`、`scripts/auto_preannotate_worker.py`、`auto_sample_preannotate.py`、`crud_auto_label_job.py`、`auto_label_job.py`、`zc1d2e3f4a5b_add_auto_preannotate_fencing.py`。
- 验证：`python -m pytest labelu/tests/internal/application/service/test_auto_preannotate_worker_runtime.py labelu/tests/internal/application/service/test_auto_sample_preannotate.py -q --disable-warnings --tb=short`，`17 passed`；`compileall` 通过。
- 边界：`AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED` 默认关闭，真实 YOLO/GPU/S3/MySQL/systemd/生产未验收；手工 YOLO/TagTool/VLX 作业仍需后续独立化。
- 状态：未提交、未推送、未部署。

独立记录：`labelu/docs/7gai_run_0298_auto_preannotate_dedicated_worker.md`。

## RUN-0299 手工 YOLO 批量预标注独立 Worker、持久化输入与 fencing

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构/迁移 | WP1/WP2 | 为 `yolo_preannotate_job` 增加执行输入、租约和 fencing 字段；迁移接唯一 head | 本地迁移代码完成；真实 MySQL 未验收 |
| R04 后端 | WP1 | API 持久化 sample_ids、权重、尺寸、阈值、覆盖策略和模型组件；开关开启只入队；sync 使用 fenced 进度/终态和样本写入前 guard | 本地代码完成 |
| R06 模型算法 | WP1/WP3 | 复用既有 YOLO 推理和车牌组件输入，不改变一期矩形框模型契约 | 本地接口回归通过 |
| R09 测试 | WP5 | 覆盖接管递增、旧 token 拒绝、payload 读取、关闭失败和路由兼容 | `7 passed`；compileall 通过 |
| R10 运维 | OPS | 明确 Worker 开关、并发上限、共享权重路径和真实环境放行边界 | 未连接生产/未部署 |

- 代码：`yolo_preannotate_worker_runtime.py`、`scripts/yolo_preannotate_worker.py`、`yolo_preannotate.py`、`crud_yolo_preannotate_job.py`、`yolo_preannotate_job.py`、`.env.example`。
- 迁移：`zd1e2f3a4b5c_add_yolo_preannotate_worker_contract.py`，`down_revision=zc1d2e3f4a5b`。
- 独立记录：`labelu/docs/7gai_run_0299_manual_yolo_preannotate_worker.md`。
- 边界：`YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED` 默认关闭；真实 MySQL、systemd 重启接管、共享模型目录、真实 YOLO 权重、生产页面和 BM1688 未验收。
- 状态：未提交、未推送、未部署。
# RUN-0305 BM1688 编译与边端验证 Worker 审计（2026-09-20，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收状态 |
|---|---|---|---|
| R03/R04/R06/R07/R09/R10 | WP3/WP5/OPS | 审计 PT→静态 ONNX→三精度 BModel、固定评估集、Board Agent/SSH 真机验证、租约/fencing、设备槽位和重启接管；明确车牌专用 FP16 制品不能冒充 YOLO 三精度包 | 编译/边端专项 `30 passed, 114 warnings`；真实 TPU-MLIR/BM1688/MySQL/systemd/AICM/生产证据未完成 |

- 独立记录：`labelu/docs/7gai_run_0305_bm1688_compile_edge_worker_audit.md`。
- 关键结论：本地 Worker 具备默认关闭、字段未就绪失败关闭、队列租约、执行租约和旧 token 隔离；固定评估集缺失会阻断发布。
- 关键缺口：真实 PT、静态 ONNX、FP32/FP16/INT8 BModel、BM1688 三精度加载/推理/P95/短稳、断网重启和设备容量均未验证；车牌专用链当前主要为 FP16，需单独精度策略。
- 生产边界：未连接生产、未运行真实编译或真机、未提交、未推送、未部署；相关 Worker 开关继续保持关闭。

## RUN-0322 模型中心列表与全量摘要异步拆分（2026-09-21，本地 L1）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP5 | 固化“先展示列表、统计异步加载”的模型中心交互，摘要失败不阻塞详情和下载 | 本地契约完成 |
| R03 架构 | WP2/WP5 | 新增 `include_summary` 兼容参数和 `/yolo-bm1688-exports/catalog-summary` 独立路由，保持权限/筛选口径 | 本地代码完成 |
| R04 后端 | WP2 | 摘要服务复用原统计逻辑；全量查询使用列投影和 500 条分块，避免加载编译日志和制品大字段 | 本地代码完成 |
| R08 前端/UI | WP4 | 列表与摘要请求拆分；序号保护旧响应；轮询摘要至少 15 秒刷新一次 | 本地页面完成 |
| R09 测试 | WP5 | 列表跳过摘要、摘要路由、single-flight 和模型中心页面回归 | 后端 `42 passed, 1 skipped`；页面 `25 passed` |
| R10 运维 | OPS | 形成同版本 MySQL/多副本并发压测与 EXPLAIN 验收要求 | 待执行 |

- 独立记录：`labelu/docs/7gai_run_0322_model_catalog_async_summary.md`。
- 本轮未连接生产、未提交、未推送、未部署；不能把本地性能改善当作生产验收。

## RUN-0323 本地浏览器菜单与 API 只读验收（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP5 | 登录后平台菜单、训练入口和模型中心入口可达 | 本地通过 |
| R03 架构/R04 后端 | WP2/WP5 | 本地 SQLite 服务、认证、中文 API 文档和 OpenAPI 响应可用 | 本地通过 |
| R08 前端/UI | WP4 | 7GAI 中文外壳、主导航、训练嵌入页可见 | 本地通过 |
| R09 测试 | WP5 | 执行本地浏览器只读验收 | `2 passed, 114 warnings` |
| R10 运维 | OPS | 进程级强制 SQLite，确认未触碰生产 MySQL | 本地通过 |

- 独立记录：`labelu/docs/7gai_run_0323_local_browser_acceptance.md`。
- 本地入口：`http://127.0.0.1:8002/`；模型中心：`http://127.0.0.1:8002/yolo-bm1688-exports`。
- 本轮只读，无业务写入；未连接生产、未提交、未推送、未部署。
- 真实 MySQL、GPU、TPU-MLIR、BM1688、S3、AICM 证据仍不能由本地浏览器验收替代。

## RUN-0324 本地真实 YOLO11 训练与 PT→ONNX 验证（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R06 模型算法 | WP1/WP3 | CUDA 上真实运行 `yolo11n.pt`，完成 1 轮检测训练并产出 `best.pt` | 训练成功 |
| R03 架构 | WP2/WP3 | PT→静态 ONNX，固定输入、opset、输出形状并记录 SHA256 | ONNX 结构通过 |
| R07 BM1688 边端 | WP3 | 确认 ONNX 可作为后续 TPU-MLIR 输入 | 本地输入契约通过；未编译 BModel |
| R09 测试 | WP5 | `onnx.checker`、ONNX Runtime CPU 加载和 `data.yaml` 绝对路径契约验证 | `25 passed, 114 warnings` |
| R10 运维 | OPS | 训练只使用本机 CUDA/本地样例数据，不访问生产 | 通过 |

- 独立记录：`labelu/docs/7gai_run_0324_local_real_yolo11_pt_onnx.md`。
- 本地制品：`runs/detect/.tmp/real-yolo11-acceptance/yolo11n-epoch1-absolute-data/weights/best.pt` 与 `best.onnx`。
- 本轮只产生本地制品和证据，未连接生产、未提交、未推送、未部署。
- 真实 TPU-MLIR、FP32/FP16/INT8 BModel、BM1688 真机、AICM/S3 和 MySQL 并发仍是下一依赖。

## RUN-0325 本地 BModel 编译与发布门禁失败关闭验证（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP3 | 对真实 PT/ONNX 执行 BM1688 编译契约检查 | 返回阻断 |
| R06/R07 模型算法/边端 | WP3 | 验证 FP32、FP16、INT8 BModel 均为发布前置条件 | 三精度缺失被识别 |
| R05 发布集成 | WP3 | 验证不完整制品不能进入 AICM 发布资格 | 失败关闭 |
| R09 测试 | WP5 | 保存阻断原因、ONNX 通过项和下一依赖 | 证据已记录 |

- 独立记录：`labelu/docs/7gai_run_0325_local_bmodel_publish_fail_closed.md`。
- 结果：`onnx_contract.passed=true`，但整体 `status=阻断`；原因包括三精度 BModel、校准集、固定评估集和 Profile 清单缺失。关联发布门禁回归 `33 passed, 114 warnings`。
- 本轮只读本地验证，未连接生产、未调用 AICM/S3、未提交、未推送、未部署。

## RUN-0326 本地发布目录、下载控制与第三方 S3 回流专项验收（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R05 发布集成 | WP3 | AICM 模型 ID、本地发布目录和下载包契约 | 通过 |
| R04 后端 | WP2/WP3 | 下载开关同时约束管理端和公开端 | 通过 |
| R06 模型算法 | WP3/WP5 | 专家回流按发布模型 ID 对齐血缘，未登记模型等待 | 通过 |
| R09 测试 | WP5 | 发布目录、下载控制、S3 导入、幂等和等待回归 | `39 passed, 117 warnings` |
| R10 运维 | OPS | 确认未连接真实 AICM/S3/生产 | 通过 |

- 独立记录：`labelu/docs/7gai_run_0326_local_publish_download_s3_reflow.md`。
- 本轮为本地测试替身验收，不能替代真实 AICM/S3、MySQL 并发和 Worker 重启证据。
- 未提交、未推送、未部署。

## RUN-0327 本地自动进化与自动训练门禁专项验收（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP5 | 固化回流→难例→训练→评估阶段与人工介入点 | 本地通过 |
| R03 架构/R04 后端 | WP1/WP2 | 状态机、事件幂等、开关和失败关闭 | 本地通过 |
| R06 模型算法 | WP1/WP3 | 数据、标签、评估集、质量策略门禁 | 本地通过 |
| R09 测试 | WP5 | 自动进化与自动训练专项回归 | `83 passed, 114 warnings` |
| R10 运维 | OPS | 确认生产自动计划未启用 | 通过 |

- 独立记录：`labelu/docs/7gai_run_0327_local_auto_evolution_gates.md`。
- 本轮未连接真实 S3/AICM/GPU/MySQL，未提交、未推送、未部署；自动训练相关生产开关继续关闭。

## RUN-0328 本地执行租约、fencing 与 Worker 并发契约验收（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构/R04 后端 | WP0/WP1/WP2 | 执行租约、fencing、条件更新和接管 | 本地通过 |
| R05 发布集成 | WP3 | AICM 发布 Worker 领取和旧 token 隔离 | 本地通过 |
| R07 BM1688 边端 | WP3 | BM1688 Worker 领取、失租和终态保护 | 本地通过 |
| R09 测试 | WP5 | 并发领取、重启接管、迟到写入回归 | `29 passed, 114 warnings` |
| R10 运维 | OPS | 明确 SQLite 本地边界和 MySQL 放行前置 | 记录完成 |

- 独立记录：`labelu/docs/7gai_run_0328_local_execution_fencing_concurrency.md`。
- 本轮未连接真实 MySQL、GPU、BM1688、AICM/S3，未提交、未推送、未部署。

## RUN-0329 本地只读放行探针与 OpenAPI 契约验收（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构 | WP2/WP5 | OpenAPI 必需路径、退休路径和权限契约 | 通过 |
| R04 后端 | WP2 | 未登录主链路返回 `403`，健康/版本接口可用 | 通过 |
| R08 前端/UI | WP4 | 页面依赖路由存在 | 通过 |
| R09 测试 | WP5 | 统一只读放行探针 | 退出码 `0` |
| R10 运维 | OPS | 仅访问本地 GET，无生产写入 | 通过 |

- 独立记录：`labelu/docs/7gai_run_0329_local_readonly_release_probe.md`。
- 证据：`.tmp/evidence/run0329-local-route.json`。
- 本轮未连接生产、未提交、未推送、未部署。

## RUN-0330 本地管理账号只读主链路验收（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品/R08 前端 | WP4/WP5 | 登录后主菜单依赖 API 和两类用户边界 | 通过 |
| R03 架构/R04 后端 | WP2/WP5 | 认证、任务、数据集、训练、模型、发布、边端、S3 和工作台 API | 全部 `200` |
| R09 测试 | WP5 | 统一管理账号只读验收 | 退出码 `0` |
| R10 运维 | OPS | 跳过危险退休写探针，确认无业务写入 | 通过 |

- 独立记录：`labelu/docs/7gai_run_0330_local_admin_readonly_acceptance.md`。
- 证据：`.tmp/evidence/run0330-local-admin-readonly.json`。
- 本轮未连接生产、未提交、未推送、未部署。

## RUN-0331 本地平台 UI、菜单和去项目化契约验收（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP4/WP5 | 六个一级模块和两类角色边界 | 通过 |
| R03 架构 | WP0/WP4 | 去项目化路由和退休路径契约 | 通过 |
| R08 前端/UI | WP4 | 中文页面、居中弹窗和训练/模型操作入口 | 通过 |
| R09 测试 | WP5 | UI、菜单、Shell 和外部门禁模板回归 | `13 passed, 9 skipped` |
| R10 运维 | OPS | 外部前置缺失不误报为通过 | 通过 |

- 独立记录：`labelu/docs/7gai_run_0331_local_ui_menu_contract.md`。
- 本轮未连接生产、未提交、未推送、未部署。

## RUN-0332 本地统一放行门禁（2026-09-21）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03 架构/R04 后端 | WP0/WP2 | 迁移单头、去项目化、Provider 和发布证据门禁 | `blocking: 0` |
| R07 BM1688 边端 | WP3 | Agent/OTA 本地契约 | 通过 |
| R09 测试 | WP5 | 统一本地放行门禁和证据完整性 | 通过 |
| R10 运维 | OPS | 工作树指纹、证据路径和本地边界 | 通过 |

- 独立记录：`labelu/docs/7gai_run_0332_local_unified_release_gates.md`。
- 证据：`.tmp/evidence/run0332/local-gates-20260921T020952Z.json`。
- 本轮未连接生产、未提交、未推送、未部署。
- 真实 MySQL、GPU、TPU-MLIR、BM1688、AICM、S3 和生产代理仍需外部环境证据。

## RUN-0333 新建标签英文系统值与自动颜色（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP4 | 明确“系统值（英文）”字段含义和创建后不可修改规则 | 本地页面通过 |
| R04 后端 | WP2 | 复用后端自动分配和数据库唯一约束；前端创建不再提交固定颜色 | 请求契约通过 |
| R08 前端/UI | WP4 | 新建标签弹窗自动展示未占用色号，颜色只读；编辑保留改色 | 浏览器通过 |
| R09 测试 | WP5 | 标签弹窗、颜色预览和创建请求回归 | `5 passed`；类型检查通过 |

- 独立记录：`labelu/docs/7gai_run_0333_label_create_auto_color_ui.md`。
- 代码：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.tsx`。
- 测试：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.test.tsx`。
- 页面/API：本地 `http://127.0.0.1:8002/annotation-settings` → 系统管理 → 标注与数据治理；创建接口仍为 `POST /v1/annotation-taxonomy/labels`。
- 浏览器验收未点击创建，未写入本地数据；未连接生产、未提交、未推送、未部署。

## RUN-0334 标签类型中文语义与一期模型边界（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP4 | 将 `bbox` 的页面语义明确为“矩形框（目标检测）”，说明一期 YOLO11/BM1688 边界 | 本地页面通过 |
| R08 前端/UI | WP4 | 标签列表和新建标签居中弹窗使用中文类型说明，内部仍保留 `bbox` | 浏览器通过 |
| R09 测试 | WP5 | 类型显示、弹窗值、颜色和创建请求回归 | `5 passed`；类型检查通过 |

- 独立记录：`labelu/docs/7gai_run_0334_label_kind_chinese_semantics.md`。
- 改动文件：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.tsx`、`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.test.tsx`。
- 数据/API：无数据库和路由变更；`POST /v1/annotation-taxonomy/labels` 仍使用 `annotation_kind=bbox`，颜色由后端唯一分配。
- 页面证据：`http://127.0.0.1:8002/admin?view=governance` 的标签列表和新建弹窗均显示“矩形框（目标检测）”。
- 统一本地门禁：`.tmp/evidence/run0334/local-gates-20260921T035827Z.json`，`blocking: 0`。
- 真实环境边界：未连接生产、GPU、TPU-MLIR、BM1688、AICM、S3 或 MySQL；未提交、未推送、未部署。
- 下一依赖：生产版本对齐和真实 HIL；其他标注类型必须按独立训练配方闭合后再开放。

## RUN-0335 生产版本只读探针与本地放行边界（2026-09-21，只读）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R09/R10 | WP0/WP2/WP5/OPS | 对生产入口执行健康、就绪、版本和 OpenAPI 只读探针，确认本地方案与线上版本边界 | 生产版本未对齐，阻断预生产/生产放行 |

- 生产 `/` 返回 `200 text/html`；`/api/v1/health`、`/api/v1/ready`、`/api/v1/version` 均为 `404`；`/openapi.json` 返回 `200 application/json`。
- 独立记录：`labelu/docs/7gai_run_0335_production_version_readonly_probe.md`。
- 影响：不能把本地页面、测试和统一本地门禁当作生产完成证据；先完成生产版本对齐和 JSON 探针，再进行真实环境验收。
- 本轮未连接生产写入口、未修改数据库、未提交、未推送、未部署。

## RUN-0336 本地独立工具页六菜单导航收口（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R08/R09 | WP0/WP4/WP5 | 将 8002 独立工具页共享导航从历史技术分组收口为六个一级菜单，隐藏工作流和设备部署旧入口 | Node 导航契约通过；无缓存浏览器页面通过 |

- 改动文件：`frontend/extensions/assets/labelu_portal.js`、`frontend/labelU-Kit/apps/frontend/public/assets/labelu_portal.js`、`frontend/extensions/tests/labelu_portal_navigation.test.mjs`。
- 六菜单：工作台、任务与标注、训练中心、模型中心、进化中心、系统管理。
- 页面/API影响：只改变导航展示；模型编译、模型列表、发布记录归入模型中心；边端验证设备归入系统管理；不新增设备发布或模型回滚。
- 独立记录：`labelu/docs/7gai_run_0336_local_six_menu_navigation.md`。
- 本地证据：`http://127.0.0.1:8002/yolo-bm1688-exports?local_nav_check=20260921`；两个静态资源 SHA256 一致。
- 统一本地门禁：`.tmp/evidence/local-nav-0336/local-gates-20260921T054228Z.json`，`blocking: 0`。
- 本轮未连接生产、未提交、未推送、未部署。

## RUN-0337 本地模型中心文案与入口对齐（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R08/R09 | WP0/WP4/WP5 | 修正模型发布页旧“辅助入口 / 配置”提示，统一指向系统管理中的外部发布环境 | 专项测试通过；本地浏览器通过 |

- 改动文件：`frontend/extensions/pages/yolo_model_publish.html`、`frontend/extensions/assets/labelu_portal.js`、`frontend/labelU-Kit/apps/frontend/public/assets/labelu_portal.js`、`frontend/extensions/tests/labelu_portal_navigation.test.mjs`。
- 页面证据：`http://127.0.0.1:8002/yolo-model-publish?local_publish_check=20260921`。
- 规则边界：发布记录仍属于模型中心；不新增设备发布和模型回滚。
- 独立记录：`labelu/docs/7gai_run_0337_local_model_center_copy_alignment.md`。
- 本轮未连接生产、未提交、未推送、未部署。

## RUN-0338 本地验收种子血缘与去项目化修正（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R03/R06/R09 | WP0/WP2/WP3/WP5 | 修正本地验收种子计划和训练作业的历史项目写入及固定任务关联，补充血缘测试 | `2 passed`；脚本编译通过 |

- 改动文件：`scripts/seed_local_acceptance_flow.py`、`scripts/tests/test_seed_local_acceptance_flow.py`。
- 业务影响：验收链使用无项目范围，训练作业绑定本轮新建任务，避免假成功数据。
- 独立记录：`labelu/docs/7gai_run_0338_local_acceptance_lineage_no_project.md`。
- 统一本地门禁：`.tmp/evidence/local-lineage-0338/local-gates-20260921T061636Z.json`，`blocking: 0`。
- 本轮未创建本地数据库、未连接生产、未提交、未推送、未部署。

## RUN-0339 本地训练摘要与自动训练计划可见性修正（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R04/R08/R09 | WP2/WP4/WP5 | 修正统一训练摘要返回自动训练计划；修复权限查询首屏竞态；保持无项目维度和只读历史计划边界 | 后端 `9 passed`；前端 `31 passed`；浏览器页面通过 |

- 根因：`GET /v1/training/summary` 原来把自动训练计划硬编码为空，且前端在当前用户权限未加载完成时清除 `view=auto-plan`。
- 改动文件：`labelu/internal/adapter/routers/training.py`、`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/training.tsx`、对应后端和前端测试。
- 本地页面：`http://127.0.0.1:8003/training?view=auto-plan&local_check=0339`，可看到 `7GAI-local-acceptance-flow` 并打开详情。
- 本地接口：摘要返回 `plan_count=1`、计划列表和状态；无 `project_id` 请求参数。
- 本地构建：`python -m labelu.main frontend-build` 成功；8003 使用 `.tmp/labelu-dev.sqlite`，8002 旧进程保留未动。
- 本轮未连接生产、未提交、未推送、未部署；线上仍仅作只读参考。

## RUN-0340 标签库允许修改英文系统值（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R04/R08/R09 | WP0/WP2/WP4/WP5 | 管理人员可编辑标签英文系统值；历史冻结契约、训练作业和已发布模型保持原快照；复用版本锁、唯一约束和审计 | 后端 `16 passed`；前端 `6 passed`；本地浏览器通过 |

- 改动文件：`labelu/internal/application/command/annotation_taxonomy.py`、`labelu/internal/application/service/system_labels.py`、`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.tsx`、对应专项测试。
- 页面/API：`/admin?view=governance` 的标签库编辑弹窗；`PATCH /v1/annotation-taxonomy/labels/{label_id}` 新增可选 `label_value`。
- 业务规则：新值必须以英文字母开头，只允许字母/数字/下划线且全局唯一；保存时校验 `version`；旧契约、数据集版本、训练作业、发布记录和模型文件不回写。
- 测试：`python -m pytest labelu/tests/internal/adapter/routers/test_system_label_contracts.py -q`（16 passed）；`pnpm exec vitest run src/pages/platform/pages/admin.test.tsx`（6 passed）。
- 本轮未连接生产、未提交、未推送、未部署；线上仅作只读参考。

## RUN-0344 去掉数据集分类日常入口（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R04/R08/R09 | WP0/WP2/WP4/WP5 | 将系统管理治理页签收口为“标签库、数据集管理”；新建数据集不再选择分类；历史分类字段和接口只读兼容保留 | 前端 `16 passed`；方案文档同步 |

- 独立记录：`labelu/docs/7gai_run_0344_remove_dataset_category_ui.md`。
- 改动文件：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.tsx`、`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/datasets.tsx`、对应前端测试和《7GAI进化平台菜单级产品与业务闭环方案》。
- 本地入口：`http://127.0.0.1:8003/admin?view=governance`；管理页面只显示“标签库”“数据集管理”。
- 兼容边界：不删除 `dataset_category_id` 数据、不执行迁移、不影响历史任务/训练/回流/模型血缘；本地新流程不再创建或维护分类。
- 本轮未连接生产、未提交、未推送、未部署；线上仅作只读参考。
## RUN-0345 自动训练轮次阶段唯一性（2026-09-21，本地）

| 角色/工作包 | 需求与结论 | 改动文件 | 验证与状态 |
|---|---|---|---|
| R03/R04/R09；WP0/WP2/WP5 | 为四个自动训练阶段实体补数据库级 `auto_training_round_id` 唯一保护；历史重复先阻断，NULL 可多条，迁移幂等，BM1688/边端物化使用行锁，禁止自动降级 | `labelu/alembic_labelu/versions/zf2a3b4c5d6e_add_auto_training_round_stage_uniqueness.py`；`labelu/internal/application/service/auto_training_pipeline_runtime.py`；迁移审计测试与服务回归 | 专项 `23 passed`；完整 `labelu/tests/alembic` 到 100%；BM1688/边端回归 `56 passed`；未连接生产、未提交、未推送、未部署；详见 `labelu/docs/7gai_run_0345_auto_training_round_unique_indexes.md` |

## RUN-0346 训练流程去掉数据集分类入口（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R04/R08/R09 | WP0/WP2/WP4/WP5 | 训练候选统一按数据集和标签筛选；移除按标签训练分类选择器、分类文案和分类确认项；历史字段只读兼容保留 | 前端专项 `31 passed`；生产构建成功；训练页面分类引用核验为 0 |

- 独立记录：`labelu/docs/7gai_run_0346_remove_training_dataset_category_ui.md`。
- 本地影响：`/training` 新建训练只显示数据集、标签契约/标签、来源任务和冻结快照信息；不再调用分类接口或发送 `dataset_category_id`。
- 兼容边界：不删除历史分类字段、不执行生产迁移、不改变旧任务和模型血缘。
- 本轮未连接生产、未提交、未推送、未部署；线上仅作只读参考。

## RUN-0347 数据集分析改为按数据集范围（2026-09-21，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R04/R08/R09 | WP0/WP2/WP4/WP5 | 将旧数据集分析从分类选择改为数据集选择；标签汇总、图片预览和分析接口统一使用 `dataset_id`；分类字段保留只读兼容并增加数据集访问校验 | 后端 `2 passed`；前端类型检查通过；前端专项 `37 passed` |

- 独立记录：`labelu/docs/7gai_run_0347_dataset_analysis_dataset_scope.md`。
- 本地影响：分析页不再展示“数据集分类”，新建和训练主流程继续只使用数据集与标签。
- 兼容边界：不删除 `dataset_category_id`，不执行数据库迁移，不影响历史任务、训练、第三方回流和模型血缘。
- 本轮未连接生产、未提交、未推送、未部署；线上仅作只读参考。

## RUN-0348 系统管理新增边端设备配置（2026-10-08，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R08/R09 | WP0/WP3/WP4/WP5 | 在系统管理新增“边端设备管理”，供 BM1688 BModel 真机检查复用；模型详情增加设备配置入口 | 类型检查通过；前端专项 `30 passed`；本地桌面和 390 像素窄屏浏览器通过 |

- 本地入口：`http://127.0.0.1:8003/admin?view=edge-devices`；BModel 详情入口跳转到同一设备管理页。
- 复用边界：直接复用现有 `/api/v1/edge-devices` 设备注册、当前设备、连接探测和环境检查接口，不新增第二套设备表或配置数据。
- 页面功能：设备列表、搜索筛选、新增、查看、编辑、设为当前、连接探测、环境检查和删除；新增、查看、编辑、删除确认均使用弹窗。
- 并发控制：列表正常状态不持续轮询；仅当设备环境处于检查、安装或清理状态时每 3 秒刷新；批量检查只提交未就绪设备。
- 本地真实页面：设备管理入口、新增设备弹窗和窄屏布局通过；本地当前为 0 台设备，未创建测试设备，未执行探测、环境准备或删除操作。
- BModel 边界：设备配置完成不等于模型边端检查通过；仍需具体 BModel 创建边端检查任务并取得真实设备报告。当前本地无可打开详情的 BModel，详情入口由组件测试覆盖。
- 生产边界：生产 `/edge-device-registry` 仅作只读功能参考；未修改生产数据、代码或配置，未提交、未推送、未部署。

## RUN-0349 模型中心直接创建 BModel 边端检查（2026-10-08，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R04/R08/R09 | WP0/WP3/WP4/WP5 | BModel 详情新增“新建边端检查”弹窗，复用现有编译记录、设备调度、任务样本和边端验证接口 | 类型检查、生产构建通过；前端专项 23 项通过；本地真实页面通过 |

- 本地入口：`/models?view=bmodel` → BModel“详情” → “新建边端检查”。
- 关联规则：从 READY 状态的 BM1688 BModel 制品 `metadata.export_id` 读取唯一编译记录；缺少关联或三份制品关联冲突时禁止创建，不猜测编译记录。
- 弹窗能力：展示模型、编译记录、来源任务和设备状态；支持“运行并评估/仅检查运行”“快速检查/完整检查”；从来源任务选择最多 50 张图片，默认优先选择前 10 张有人工拉框图片。
- 提交规则：“运行并评估”至少选择一张有人工拉框图片；无已连接且 BM1688 环境就绪的设备时禁止提交；设备繁忙允许任务进入现有调度队列。
- 执行链路：依次调用 `POST /v1/yolo-edge-validation/jobs` 和 `POST /v1/yolo-edge-validation/jobs/{id}/run`；弹窗内显示任务状态、执行设备、当前步骤、图片统计和结果摘要，并可进入原完整任务页面。
- 并发控制：前端使用同步提交锁防止连续点击重复创建；仅当前任务处于等待、排队或执行状态时每 2 秒查询一次，任务结束或弹窗关闭后停止轮询；设备和样本信息按需加载并短时复用。
- 复用边界：不新增边端设备表、不新增边端验证接口、不允许手工指定设备，继续由现有调度器按设备就绪和占用状态分配。
- 本地页面验收：`http://127.0.0.1:8003/models?view=bmodel` 存在 2 个可查看 BModel；有效模型弹窗读取到 1 台就绪设备，默认选中 10 张有人工拉框图片，“创建并启动”按钮可用；1440×960 桌面和 390×844 窄屏布局通过，浏览器控制台 0 个错误；为避免占用真实设备，本轮未点击最终启动按钮。
- 本轮未连接生产、未提交、未推送、未部署；生产只作为既有能力参考。

## RUN-0350 BModel 边端检查结果可追溯（2026-10-08，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R04/R08/R09 | WP0/WP3/WP4/WP5 | BModel 详情新增“查看检查结果”，集中展示检查对象、过程、精度、性能、资源和失败原因 | 类型检查、生产构建通过；前端专项 `24 passed`；本地真实页面通过 |

- 本地入口：`/models?view=bmodel` → BModel“详情” → “查看检查结果”；弹窗默认打开当前 BModel 最新一条记录，并可切换最近 20 条历史记录。
- 检查对象：显示检查方式、检查档位、执行设备、FP32/FP16/INT8 模型、阈值、执行时间和实际样本图片；样本存在时展示文件名、样本编号和人工拉框情况。
- 检查过程：按后端已保存的进度历史展示执行时间、中文阶段和真实消息；没有阶段证据时明确提示“没有保存分阶段执行记录”。
- 检查结果：分开显示整次任务状态、设备环境、模型运行、精度计算、标签覆盖、设备退出码、总耗时、缓存和回传结果；整次任务失败时仍保留并展示已经完成的有效精度证据。
- 精度与性能：分别展示 FP32、FP16、INT8 的 `mAP50-95`、`mAP50`、精确率、召回率、F1 和评估图片数；性能和资源展示平均/P95/P99 推理耗时、吞吐量、CPU、内存、NPU、温度和功耗。后端没有采集的值统一显示“未采集”，不以 0 代替。
- 当前真实结果：最新检查任务 `#2` 共检查 10 张有人工拉框图片，精度计算已完成，标签覆盖 3/6；整次任务因边端缺少 `matplotlib` 导致结果图生成失败。FP32、FP16、INT8 的有效精度仍可查看；本次已回传平均推理耗时和内存平均占用，P95/P99、吞吐量、CPU/NPU、温度和功耗等未采集项显示“未采集”，不能用缺失项判断边端性能。
- 并发控制：复用现有按编译记录过滤、倒序分页的检查记录接口；结果页只查询当前选中的一条详情；仅执行中的任务每 2 秒刷新，完成或失败后停止轮询，不批量请求所有详情。
- 本轮只修改本地前端和交付记录，不新增接口、不创建新检查任务、不连接生产、不提交、不推送、不部署。

## RUN-0351 边端检查辅助告警去重（2026-10-08，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R04/R08/R09 | WP3/WP4/WP5 | 合并结果告警和执行告警时按告警编码去重，避免同一条 matplotlib 缺失提示重复展示 | 前端专项 `3 passed`；前端构建通过；本地模型中心页面通过 |

- 修复文件：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/edgeValidationModal.tsx`；测试覆盖：`edgeValidationModal.test.tsx`。
- 当前任务 #2 的页面结果：BM1688 环境通过、FP32/FP16/INT8 推理和精度计算通过；固定评估集质量门禁未通过，模型仍不可发布。
- `matplotlib` 缺失只作为辅助结果提醒展示一次，不再被误认为模型推理失败；缺失曲线图不改变三种模型的有效精度结果。
- 检查记录下拉项复用当前任务详情判断质量门禁专属失败，显示为“检查完成（质量门禁未通过）”，避免历史列表摘要继续显示“失败”。
- 本地页面：`http://127.0.0.1:8003/models?view=bmodel` → 详情 → 查看检查结果；已通过浏览器页面和截图复核。
- 本轮未连接生产、未创建检查任务、未修改真实检查数据、未提交、未推送、未部署。

## RUN-0352 边端检查过程与结果直达（2026-10-08，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R04/R09 | WP4/WP5 | 在边端检查结果弹窗顶部增加“查看检查过程”和“查看检查结果”直达按钮，避免长结果页中过程和指标被首屏折叠隐藏 | 本地浏览器已分别点击验证；过程表和执行结论/三种模型精度表均能定位显示；专项测试 `3 passed` |

- 当前本地最新任务 #3 已结束，状态为“部分完成”：设备环境通过、精度计算通过；模型运行失败、设备退出码为 1，质量门禁未确认，因此不能发布。
- 任务 #3 已展示三种模型精度：FP32/FP16/INT8 的 mAP、精确率、召回率、F1、评估图片数，以及边端性能数据。
- 任务 #2 可在“检查记录”下拉框切换查看；其结果仍是三种模型推理和精度计算通过，但固定评估集质量门禁未通过。
- 本轮未创建检查任务、未修改检查数据、未连接生产、未提交、未推送、未部署。

## RUN-0353 边端质量门禁清单兼容修复（2026-10-08，本地）

| 责任角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01/R03/R04/R08/R09 | WP3/WP4/WP5 | 修复固定评估集清单字段兼容；修正质量门禁退出码不应被展示为模型推理失败；历史结果只读兼容回填 | 后端专项 `63 passed`；脚本编译通过；任务 #3 现有清单校验由失败变为通过；未重新执行边端任务 |

- 根因：任务 #3 的样本包实际包含 10 张图片和 10 个 XML 标注，但旧清单使用 `labeled_count/file_mappings`，边端校验只读取 `labeled_image_count/label_mappings`，因此质量门禁错误报告标签缺失并返回退出码 `1`。
- 修复：新样本包同时写入新旧字段；边端校验兼容旧字段并按实际已标注映射计数；三种 BModel 均有有效结果时，退出码 `1` 只表示质量门禁阻断，不再把模型运行显示为失败。
- 历史记录：打开旧任务时只读重算“模型运行”展示维度，不改写历史质量门禁结论；需要重新执行同一任务后才会生成新的正式门禁结果。
- 本轮未连接生产、未创建检查任务、未重新占用边端、未提交、未推送、未部署。
