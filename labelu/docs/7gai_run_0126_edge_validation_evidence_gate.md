# RUN-0126 BM1688 真机验证证据门禁

## 1. 本轮范围

- 主责：R07 BM1688 边端；协作：R03 架构、R04 后端、R06 模型算法、R09 测试。
- 目标：把“板端进程退出成功”和“可以作为模型发布证据”分开。
- 不新增登录角色、菜单、项目/业务空间、设备发布、OTA 或模型回滚。
- Board Agent 只是内网 BM1688 验证执行协议：服务器下发已有 BModel，设备加载并推理，再回传证据；模型编译仍在服务器编译节点完成。

## 2. 代码交付

### 2.1 证据契约

新增 `labelu/internal/common/edge_validation_evidence.py`，版本为 `bm1688_edge_validation_evidence.v1`。回传报告至少需要能够证明：

1. 真实设备标识（设备编号、主机或序列号）；
2. 模型/导出制品身份和执行签名；
3. FP32、FP16、INT8 三种 BModel 均实际执行；
4. 固定评估集摘要和有效推理数量；
5. 精度指标、P95 推理时延、错误率；
6. 至少 60 秒短稳定运行；
7. 执行期 fencing token 存在；
8. 若发布门禁要求，必须有 AICM 模型编号。

证据完整时返回 `status=pass/publishable=true`；缺证据返回 `pending/publishable=false`；明确执行失败或签名/评估集不一致返回 `fail`。缺证据不能被当成边端通过。

### 2.2 Board Agent 回传

- 回传报告补充模型身份、制品执行签名、设备身份、服务器编译/设备推理职责和三精度要求。
- 路由从 OpenAPI 隐藏，避免把内网执行协议误显示为用户设备发布控制面；内部接口仍保留给受控 Board Agent 使用。
- 父链校验覆盖导出制品 → 训练作业 → 任务，历史 `project_id` 只做兼容一致性检查，不重新引入项目权限或菜单。
- `COMPLETED` 回传若证据不完整，数据库状态改为 `PARTIAL_COMPLETED`，并保留机器可读的 `evidence_gate` 原因；查询侧显示“待补证据”，不能进入发布。

## 3. 并发与失败处理

- 继续使用边端验证任务的领取租约和 fencing token；旧设备/旧 Worker 的迟到回传不能覆盖新执行世代。
- 证据检查是只读计算，不额外创建数据库锁；大报告先写证据门禁摘要，避免历史报告大小上限截断关键原因。
- 设备身份、三精度、固定评估集、P95、短稳或 fencing 缺失时不发布；明确执行失败时进入失败处理和可重试路径。
- 本轮没有把模拟器、SSH 可连接或进程返回码当成真机质量通过。

## 4. 本地验证

- `python -m pytest -q labelu/tests/internal/common/test_edge_validation_evidence.py labelu/tests/internal/adapter/routers/test_board_agent_retained.py labelu/tests/scripts/test_edge_board_validate_runner_script.py`
- 结果：`20 passed`（存在既有 Pydantic/SQLAlchemy 弃用警告，不影响通过）。
- `compileall` 和 `git diff --check` 需在本轮最终收口时再次执行。

## 5. 真实性边界

本轮为本地 L1。尚未取得真实 TPU-MLIR 编译节点、三精度 BModel、固定生产评估集、BM1688 真机短稳/P95、真实 MySQL 双进程、systemd 重启接管或生产部署证据。生产 OpenAPI 仍以 RUN-0125 只读探针为准，不能宣称本轮已上线。

## 6. 下一轮依赖

1. 在受控预生产环境提交一份三精度模型包和固定评估集，完成 BM1688 真机证据回传；
2. 由 R09 对设备身份、执行签名、评估集摘要、P95、短稳和 fencing 逐项留档；
3. 由 R05/R06/R07 将通过的证据接入 AICM 发布和本平台下载目录门禁；
4. 真实证据完成前，发布接口保持阻断，已发布模型问题仍按“人工关闭本平台后续下载并发布替代版本”处理，不提供回滚按钮。

状态：本地 L1，未提交、未推送、未部署。
