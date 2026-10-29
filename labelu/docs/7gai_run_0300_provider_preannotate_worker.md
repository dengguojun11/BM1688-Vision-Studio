# RUN-0300 TagTool、LocateAnything、VLX-Seek、Rex-Omni 预标注 Worker

## 1. 本轮目标

把“任务与标注”中的四类模型预标注统一成可持久化、可重启接管、受租约
fencing 保护的独立 Worker。四类模型的请求/结果格式不同，因此继续保留
TagTool 专用表和 LocateAnything/VLX/Rex 共用表；统一的是队列控制，不强行
合并模型推理适配器。

## 2. 涉及菜单和按钮

| 菜单 | 按钮 | 行为 |
|---|---|---|
| 任务与标注 | 开始预标注 | 写入完整 payload 后返回 `PENDING`；独立 Worker 开关开启时不在 Web 进程执行推理 |
| 任务与标注 | 查看预标注进度 | 复用原作业查询接口，展示等待、处理中、完成和失败 |
| 任务与标注 | 人工确认 | 继续复用原预标注结果，不改变矩形框/全局标签契约 |

标注人员和管理人员均可发起预标注；管理人员额外处理失败重试、模型配置和
少量抽查。没有新增菜单、项目维度或第三类登录角色。

## 3. 实现内容

### 3.1 持久化输入

- `TagtoolPreannotateJob` 保存样本 ID、权重相对路径、标签映射、覆盖策略、模型和设备参数；
- `LocateAnythingPreannotateJob` 保存提供方、样本 ID、任务配置、目标标签和创建人；
- VLX-Seek 与 Rex-Omni 复用 LocateAnything 作业表，通过 `provider` 分流；
- 所有输入写入 `payload_json`，Worker 重启不依赖原请求体或 Web 内存。

### 3.2 租约、续租和 fencing

新增 `attempt_count`、`lease_owner`、`lease_expires_at`、`lease_fencing_token`。
Worker 采用数据库条件更新领取 `PENDING` 或过期 `PROCESSING` 作业，接管时
递增 token；每个样本开始前续租并检查 owner/token；样本结果写入、进度、完成
和失败状态均带 owner/token 条件，旧 Worker 不能覆盖新 Worker。

### 3.3 开关和兼容

配置 `PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED` 默认关闭；关闭时保留
原有 Web 线程兼容路径，开启时 API 只入队，独立脚本
`labelu/scripts/provider_preannotate_worker.py` 消费队列。生产没有真实模型、
共享权重目录、MySQL 和 systemd 证据前，不允许开启该开关。

## 4. 修改范围

- 服务：`tagtool_preannotate.py`、`locateanything_preannotate.py`、`vlx_seek_preannotate.py`；
- Worker：`provider_preannotate_worker_runtime.py`、`scripts/provider_preannotate_worker.py`；
- 持久化：TagTool/LocateAnything 模型和 CRUD；
- 配置：`PROVIDER_PREANNOTATE_*`；
- 迁移：`ze1f2a3b4c5d_add_provider_preannotate_worker_contract.py`，接
  `zd1e2f3a4b5c` 唯一 head。

## 5. 本地验证

```text
python -m pytest \
  labelu/tests/internal/application/service/test_provider_preannotate_worker.py \
  labelu/tests/internal/application/test_tagtool_preannotate.py \
  labelu/tests/internal/application/test_locateanything_preannotate.py \
  labelu/tests/internal/application/test_vlx_seek_preannotate.py -q --disable-warnings --tb=short
```

本轮专项结果：`27 passed`；`compileall` 通过。覆盖提供方隔离、持久化 payload、
过期接管、token 递增、旧 Worker 写入拒绝、Worker 开关失败关闭和 VLX/Rex 分流。

## 6. 生产边界和验收等级

当前为本地 **L1**，不代表生产通过。续租粒度为每个样本开始前；单次模型 HTTP/Torch
调用若超过租约时长，期间没有独立心跳线程，生产必须按真实 P95 调整租约并演练
长请求接管。尚缺同版本 MySQL/InnoDB 双 Worker 竞争、
长批次续租、真实模型和共享存储、systemd 重启接管、两类账号页面回放以及
生产日志/告警证据。未提交、未推送、未部署。
