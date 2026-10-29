# RUN-0317 一期剩余缺口优先级审计（2026-09-20）

## 1. 审计范围

基于 RUN-0316 之后的当前工作树和既有本地/生产只读证据，复核六个一级菜单及按钮主链、第三方 S3 自动回流、PT→ONNX→三精度 BModel、BM1688 真机前置、并发性能和生产版本漂移。本轮只读，不修改业务代码、不连接生产、不执行迁移、提交或部署。

## 2. 本地专项回归

```text
python -m pytest labelu/tests/e2e/test_7gai_visible_menu_no_project_contract.py labelu/tests/e2e/test_7gai_platform_shell_frontend.py labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py labelu/tests/internal/application/service/test_bm1688_worker_runtime.py labelu/tests/internal/application/service/test_edge_validation_worker.py labelu/tests/internal/application/service/test_catalog_performance_paths.py -q --disable-warnings --tb=short
```

结果：`32 passed, 9 skipped, 114 warnings`，退出码 `0`。跳过项属于未设置真实浏览器地址的条件验收，不代表生产通过。

## 3. 缺口优先级

### P0：阻断提交后的预生产/生产放行

| 缺口 | 证据 | 影响 | 下一工作包 |
|---|---|---|---|
| 生产版本漂移 | `RUN-0314`、`.tmp/evidence/run0314/production-readonly-route-audit.json` | 健康/就绪/版本为 HTML；四个一期主链路路由缺失；退休设备写入口仍登记 | **WP-生产版本对齐**：预生产部署当前后端和静态文件，修复反向代理 JSON 探针，清理 OpenAPI 退休写路径，再用同一脚本复验 |
| 真实训练/编译/真机证据缺失 | `RUN-0312`、`RUN-0305` | 本机没有 `tpu_mlir`、`model_deploy`、`bmrt_test`、MySQL；BM1688 SSH 超时，不能宣称 PT、静态 ONNX、FP32/FP16/INT8 BModel 或真机通过 | **WP-真实 HIL**：提供可达 GPU/TPU-MLIR 节点和一台 BM1688，完成真实制品、签名、固定评估集、加载推理和短稳证据 |

### P1：主链路上线前必须完成

| 缺口 | 证据 | 影响 | 下一工作包 |
|---|---|---|---|
| 第三方 S3/AICM 自动回流未做真实对账 | `RUN-0304` | 本地已覆盖租约、幂等、模型编号匹配和等待状态，但没有真实 S3 对象、AICM 回执、专家图片回流和 systemd 重启接管证据 | **WP-S3-AICM 联调**：用脱敏真实包验证接收、去重、按 AICM `model_set_id` 对齐、任务复用、预标注和异常分流 |
| 跨进程/真实数据库并发未验证 | `RUN-0310`、`RUN-0311` | 线上 BM1688 导出列表 `size=1` 中位约 1644ms，5 并发单请求约 8.1–8.7s；本地 single-flight 只在单进程有效 | **WP-性能与容量**：同版本 MySQL 预生产测 `size=1/20/100`、1/5/10/20 并发，采集慢 SQL、连接池、锁等待和摘要计算耗时，验证数据库聚合/失效缓存方案 |
| BM1688 设备容量和故障恢复未实测 | `RUN-0305` | 代码按设备单槽保护，但没有真实设备内存、模型并发、P95、短稳、断网、重启接管和资源释放曲线 | 纳入 **WP-真实 HIL**，增加 1/2/10 任务并发和 systemd/Worker 接管测试 |

### P2：不阻断一期主菜单，但应收口的兼容残留

`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/deployments.tsx` 仍保留历史 `project_id`、部署状态流转和“回滚”文案；当前 `/deployments` 路由由 `RetiredPlatformRoute` 重定向到 `/models`，后端写入也由退休门禁拒绝，现有测试已覆盖该行为。因此它不是当前可见主流程阻断，但应在 **WP-兼容代码清理** 中删除或进一步静态禁用，避免旧 bundle、直接导入或维护误用重新暴露项目/回滚概念。

## 4. 六菜单/按钮核对结论

- 工作台：本地路由和登录后只读 API 通过；生产版本仍未对齐。
- 任务与标注：任务、上传、预标注、人工确认、冻结和训练入口在本地菜单/页面契约中存在；真实文件和预标注服务吞吐未验收。
- 训练中心：全量/单标签训练和 PT 数据血缘本地已有；真实 GPU 训练节点未提供。
- 模型中心：模型版本、编译、真机验证、发布记录和本平台下载入口本地已有；三精度制品与真机证据缺失。
- 进化中心：自动回流、异常审核和批次推进本地已有；真实 S3/AICM 对账缺失。
- 系统管理：标签、规则、设备、外部连接和下载禁用配置本地已有；生产账号与配置落地未验收。

## 5. 结论与顺序

当前工作树保持 **L1 本地完成，生产 L0/L2/L3 未放行**。建议严格按以下顺序推进：

1. WP-生产版本对齐；
2. WP-真实 HIL（GPU、TPU-MLIR、BM1688）；
3. WP-S3-AICM 联调；
4. WP-性能与容量；
5. WP-兼容代码清理。

未取得真实环境和用户提交授权前，继续保持训练、编译、边端验证、S3 自动回流和外部发布 Worker 失败关闭。

## 6. 状态

- 代码变更：无。
- 数据库/生产：未连接、未写入、未部署。
- Git：未提交、未推送。
