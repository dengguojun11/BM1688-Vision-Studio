# RUN-0179 边端执行世代持久化与防重放

**日期**：2026-09-20  
**环境级别**：本地 L1（未提交、未推送、未部署）  
**对应工作包**：WP3 边端编译与验证、WP5 测试验收  
**责任角色**：R03 算法架构、R04 后端、R07 BM1688 边端、R09 测试、R10 运维

## 1. 本轮问题

RUN-0176 已经要求边端清单携带 `execution_generation`，但原实现只在内存中比较本次
`run_config` 与制品清单的摘要。Board Agent 或 SSH 运行器重启后会忘记已经接受过的世代，
旧包可以再次执行；多个进程同时验证时也可能覆盖世代判断。仅比较摘要字符串不能判断新旧，
因为摘要是 SHA256，不具有时间顺序。

本轮只补设备侧执行闸门，不增加设备发布、OTA 或模型回滚，不连接生产和真实 BM1688。

## 2. 已落地规则

### 2.1 持久化位置

设备状态文件默认位于 `/var/lib/labelu-edge/execution_generations.json`，不放在会被清理的
临时任务目录。受管设备可以通过以下环境变量指定持久化位置：

- `LABELU_EDGE_EXECUTION_STATE_FILE`：指定完整状态文件路径；
- `LABELU_EDGE_EXECUTION_STATE_DIR` 或 `EDGE_EXECUTION_STATE_DIR`：指定状态目录。

状态目录应位于设备本地持久磁盘并由 Agent 账号独占；目录不可为符号链接。

### 2.2 接受和拒绝

设备按 `execution_generation_scope` 记录每个范围的最新已接受世代：已发布模型优先使用
`aicm:<model_set_id>`，未发布制品使用 `export:<导出编号>`，避免同一 AICM 模型换导出编号后
重新接受旧包。

1. 首次出现的世代写入状态并允许执行；
2. 同一世代再次执行返回 `idempotent`，允许重试，不重复改写世代；
3. 提供 `execution_generation_sequence` 时按整数比较，较小世代直接拒绝；
4. 当前导出包没有序号时使用清单 `created_at` 写入的 `execution_generation_issued_at` 比较；
5. 两个不同世代没有可比较顺序，或顺序类型不一致时拒绝，绝不按摘要字典序猜测；
6. 状态文件损坏、版本不支持、条目结构不完整、状态目录或文件为符号链接时拒绝执行。

服务器在 Board Agent 和 SSH 验证包中同时写入世代范围和清单签发时间；签名覆盖这些字段，
因此设备不能被修改时间或范围后绕过门禁。

### 2.3 并发和原子性

- 使用同目录 `O_CREAT|O_EXCL` 锁文件串行化读取、比较和写入；等待超时失败关闭；
- 状态写入临时文件，写完 `flush + fsync` 后使用 `os.replace` 原子替换；
- 临时文件残留会清理，原状态不会在 JSON 未完整写入时被覆盖；
- Board Agent 与 SSH 运行器调用同一个 `verify_edge_run_config` 和世代消费函数，保持主路径与兜底路径一致。
- 服务端边端证据门禁对现代清单要求 `execution_generation_state`，并核对回传世代与
  `artifact_manifest.execution_signature`；缺失进入待补证据，拒绝或不一致禁止发布。

### 2.4 SSH 兜底包补齐同一清单门禁

本轮复核发现，SSH 兜底包如果没有新制品清单和签名字段，会落入历史兼容验签路径，
从而无法可靠记录执行世代。现已补齐固定评估集清单、模型身份、制品执行摘要、
BM1688 Profile 摘要、FP32/FP16/INT8 要求、执行世代范围和签发时间，以及服务器
Ed25519 签名/生产缺钥失败关闭。SSH 与 Board Agent 现在共享同一清单验签、文件校验、
固定评估集和执行世代持久化门禁，不再有主路径与兜底路径分叉。

## 3. 代码和页面/API影响

| 范围 | 改动 |
|---|---|
| 板端 Agent | `scripts/ops/labelu_board_agent.py` 增加状态路径、锁、状态解析/原子写和世代消费；验签后再消费世代，并把结果放入验证报告 |
| SSH 运行器 | `labelu/scripts/edge_board_validate_runner.py` 继续复用板端 Agent 验证器；缺少世代持久化函数的签名脚本直接失败；最终报告记录消费状态 |
| 服务端打包 | `labelu/internal/application/service/board_agent.py`、`edge_ssh_validation.py` 增加 `execution_generation_scope` 和 `execution_generation_issued_at` |
| 页面/API | 不新增菜单和接口；现有验证回传 JSON 增加 `execution_generation_state` 审计字段 |
| 数据库 | 不新增表、不写生产数据 |

## 4. 本地验收

专项文件：`labelu/tests/scripts/test_edge_execution_generation_state.py`。

覆盖：

- 状态跨调用持久化、同代幂等；
- 新世代接受、旧世代拒绝；
- 缺少可比较顺序时失败关闭；
- JSON 状态损坏不被覆盖；
- 签发时间排序和非法时间拒绝；
- 临时文件不残留。

本地结果：

- 执行世代专项：`7 passed`；
- 服务端边端证据专项：`8 passed`；
- 边端脚本、阈值、BM1688 运行器联合回归：`43 passed`；
- SSH 清单、签名和世代联合专项：`12 passed`；
- `py_compile`：通过；
- `git diff --check`：无新增差异错误（工作区既有换行提示不计入本轮）。

## 5. 生产放行前置条件

本轮不能替代真实设备证据。提交和部署前仍需：

1. 在 Ubuntu/BM1688 设备上配置持久状态目录、权限和备份策略；
2. 以两个并发 Agent 进程验证同一世代，证明只有一个新世代写入、另一个幂等；
3. 重启 Agent 后提交旧世代和新世代，确认旧世代拒绝、新世代按序接受；
4. 人工损坏状态文件，确认 Agent 不加载 BModel 并回传明确错误；
5. 真实 Board Agent 与 SSH 包均使用同版本 `labelu_board_agent.py`，并核对回传的
   `execution_generation_state`；
6. 将上述日志和状态文件摘要纳入边端验证证据，再进入 AICM 发布门禁。

**提交/推送/部署状态**：未提交、未推送、未部署；未连接生产、AICM、S3、TPU-MLIR 或真实 BM1688。
