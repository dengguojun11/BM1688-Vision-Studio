# RUN-0096 远程训练操作世代与回传制品隔离（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`REMOTE-TRAIN-FENCE-01`、`REMOTE-ARTIFACT-GENERATION-01`。
- 工作包和角色：WP2 训练与执行器；R04 后端主责，R03 架构、R06 模型算法、R09 测试协作。
- 本轮目标：解决远程节点目录按 `job-{id}` 复用时，旧 `phase.json`、`agent_result.json` 或权重文件被新训练轮次误读的问题。

## 实际代码变化

- 每次远程派发使用当前执行租约的 `owner:fencing_token` 生成操作世代标识，并写入 `job_spec.json`、平台 `remote_status_json`、节点 `phase.json` 和 `agent_result.json`。
- `agent_worker.py` 增加 `--operation-id` 参数；训练、下载、失败和完成阶段统一把操作世代写入状态文件和回传结果。
- 平台轮询收到 `phase.json` 后先校验操作世代；缺少世代或世代不匹配时丢弃，不覆盖本地 `remote_phase.json`，也不更新数据库状态。
- 平台导入 `agent_result.json` 和 `best.pt` 前校验当前操作世代；旧轮次、未签名或缺失世代的回传只等待重试，不进入训练完成收尾。
- 未新增菜单、用户角色、数据库字段或项目维度，继续复用执行租约和远程 Agent。

## 本地证据

```text
$env:LABELU_TEST_DB_NAME='run0096-final2'; python -m pytest \
  labelu/tests/scripts/test_remote_train_agent.py \
  labelu/tests/internal/application/service/test_remote_yolo_train_runtime.py \
  labelu/tests/internal/application/service/test_remote_payload_download.py \
  -q --disable-warnings --tb=short
26 passed, 112 warnings

python -m py_compile \
  labelu/internal/application/service/remote_yolo_train.py \
  labelu/scripts/remote_train/agent_worker.py
通过
```

新增覆盖：操作世代写入 `phase.json`、旧世代远程轮询被忽略，且旧世代不会替换本地远程阶段文件。

## 并发、失败和真实性边界

- 当前操作世代来自数据库执行租约 owner/token，能隔离旧 Worker；但节点文件系统仍未做签名校验和跨主机防篡改，不能替代真实生产安全证据。
- 尚未在真实 MySQL、真实远程 GPU 节点、systemd 重启和断点续传环境验证旧目录清理、节点接管、GPU 释放和长时运行。
- 未连接真实 S3、AICM、TPU-MLIR 或 BM1688；未提交、未推送、未部署。

## 下一轮依赖

1. 在同版本 MySQL 脱敏快照上做双进程远程接管、锁等待和操作世代对账。
2. 为 payload/spec/phase/result 增加内容摘要或签名，验证节点侧文件未被篡改。
3. 在真实远程 GPU 节点执行重启恢复、断点续传、制品导入和 GPU 释放演练。

本轮结论：远程文件世代隔离达到本地 L1；真实远程执行器和生产放行仍未完成。
