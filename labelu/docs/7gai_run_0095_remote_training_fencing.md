# RUN-0095 远程训练状态与制品导入 fencing（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；`CONCURRENCY-EXEC-02`、`REMOTE-TRAIN-FENCE-01`。
- 工作包和角色：WP2 训练与执行器；R04 后端主责，R03 架构、R06 模型算法、R09 测试协作。
- 本轮目标：将远程 YOLO 训练的派发、数据准备、上传/下载进度、远程轮询、失败收尾和 `best.pt` 导入纳入统一执行租约，阻止旧远程线程或迟到轮询覆盖接管后的作业。

## 实际代码变化

- `remote_yolo_train.py` 复用 `YoloTrainJob.execution_lease_owner`、`execution_lease_expires_at` 和递增 `execution_fencing_token`，远程派发开始前领取租约；字段缺失或租约被占用时失败关闭，不启动 SSH/SFTP 副作用。
- 远程状态 `remote_status_json`、作业状态、数据集统计和错误信息均改为 owner/token/未过期条件更新；旧 token 的上传进度、轮询状态和失败结果返回拒绝，不覆盖当前执行器。
- 远程训练仍在节点执行时，将租约交给轮询器按固定间隔续租；远程完成或失败后释放租约并停止轮询。上传进度回调也会续租，避免长文件传输期间失租。
- 远程最终 `best.pt` 导入调用已有 YOLO 终态写入 fencing，传递当前 owner/token；类别契约校验仍在导入前执行。
- 未新增菜单、用户角色、数据库字段或迁移；沿用现有执行期租约字段和执行器边界。

## 测试与本地证据

```text
$env:LABELU_TEST_DB_NAME='run0095-remote2'; python -m pytest \
  labelu/tests/internal/application/service/test_remote_yolo_train_runtime.py \
  labelu/tests/internal/application/service/test_remote_payload_download.py \
  -q --disable-warnings --tb=short
19 passed, 112 warnings

$env:LABELU_TEST_DB_NAME='run0095-train'; python -m pytest \
  labelu/tests/internal/application/service/test_yolo_train_queue.py \
  labelu/tests/internal/application/service/test_plate_recognition_train.py \
  labelu/tests/internal/application/service/test_yolo_test_val_fencing.py \
  labelu/tests/internal/application/service/test_yolo_train_summary.py \
  -q --disable-warnings --tb=short
71 passed, 117 warnings

python -m py_compile labelu/internal/application/service/remote_yolo_train.py
通过
```

新增测试 `test_remote_status_stale_fencing_token_is_rejected`，验证旧 token 的状态写入被拒绝，新 token 才能更新远程状态。

## 并发、失败和真实性边界

- 本轮只证明本地 SQLite/模拟节点中的条件写入和旧 token 隔离；尚未证明真实 MySQL 行锁、锁等待、跨进程 SSH 接管、远程 GPU 训练、重启恢复或长时间租约续租。
- 远程节点的 `phase.json`、日志和制品文件仍是节点侧文件系统，平台只在当前 token 有效时读取并导入；旧文件不会直接覆盖数据库终态，但文件世代/清理仍需后续专项治理。
- 未连接真实 GPU、TPU-MLIR、BM1688、第三方 S3、AICM 或生产训练节点；未提交、未推送、未部署。
- 通用执行租约测试中两条用例使用固定 `2026-09-18 13:00` 作为租约时间，而当前本机时间已超过该时间，出现预期外过期；这是测试时间假设问题，本轮未修改通用租约逻辑。

## 下一轮依赖

1. 在同版本 MySQL 脱敏快照上验证远程状态条件更新、锁等待和双进程接管。
2. 给远程 `phase.json`、日志和制品目录增加操作世代/执行签名，防止旧节点文件被误读。
3. 将远程训练真正切换到独立 Worker/systemd，并完成重启恢复、GPU 释放和容量压测。

本轮结论：远程训练数据库写入已达到本地 L1 fencing 边界；不能据此宣称远程 GPU 训练或生产链路完成。
