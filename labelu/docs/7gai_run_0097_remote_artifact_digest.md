# RUN-0097 远程训练制品内容摘要校验（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`REMOTE-ARTIFACT-DIGEST-01`。
- 工作包和角色：WP2 训练与执行器；R04 后端主责，R03 架构、R06 模型算法、R09 测试协作。
- 本轮目标：在操作世代之外增加内容摘要，阻止 payload 解包不完整、`job_spec.json` 被替换或旧制品目录被误读。

## 实际代码变化

- 平台对本次 `job_spec.json` 计算 SHA256，并把摘要写入远程状态；Agent 启动参数携带摘要。
- Agent 在 `phase.json`、`agent_result.json` 和失败回传中写入 `spec_sha256`；Agent 读取清单时重新计算摘要，不一致立即失败。
- 平台轮询阶段文件和导入 `agent_result.json`/`best.pt` 前同时校验操作世代和 `spec_sha256`；不一致时忽略并等待当前轮次，不更新数据库。
- 未新增菜单、角色、数据库字段、项目维度或生产密钥；继续复用现有远程 Agent 和执行租约。

## 本地证据

```text
$env:LABELU_TEST_DB_NAME='run0097-digest'; python -m pytest \
  labelu/tests/scripts/test_remote_train_agent.py \
  labelu/tests/internal/application/service/test_remote_yolo_train_runtime.py \
  labelu/tests/internal/application/service/test_remote_payload_download.py \
  -q --disable-warnings --tb=short
27 passed, 112 warnings

$env:LABELU_TEST_DB_NAME='run0097-regression'; 远程 Agent/轮询/下载与训练/车牌/test-val/汇总合并回归：
98 passed, 117 warnings

python -m py_compile \
  labelu/internal/application/service/remote_yolo_train.py \
  labelu/scripts/remote_train/agent_worker.py
通过
```

新增覆盖：阶段文件包含摘要、启动参数摘要与作业清单不一致时失败、旧/错误摘要的远程回传被平台忽略。

## 并发、失败和真实性边界

- SHA256 能发现截断、错误包和非预期内容，但不等于带密钥的防篡改签名；节点侧攻击者仍可能重新计算摘要，生产环境还需要受管 HMAC/签名密钥和轮换机制。
- 尚未在真实 MySQL、远程 GPU、systemd、节点重启和跨主机网络中验证摘要对账、断点续传和旧目录清理。
- 未连接真实 S3、AICM、TPU-MLIR 或 BM1688；未提交、未推送、未部署。

## 下一轮依赖

1. 设计并接入远程节点专用 HMAC/签名密钥，密钥只由服务端和受管 Agent 使用，支持轮换和失效。
2. 在同版本 MySQL 脱敏快照上做双进程接管、摘要对账和锁等待测试。
3. 在真实远程 GPU 节点执行重启恢复、断点续传和制品导入演练。

本轮结论：远程制品内容摘要校验达到本地 L1；生产防篡改、真实节点和生产放行仍未完成。
