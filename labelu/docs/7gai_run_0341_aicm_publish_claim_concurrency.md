# RUN-0341 AICM 发布租约并发本地契约（2026-09-21）

## 目标

确认模型中心的 AICM 发布不会因为重复点击、两个 Worker 同时扫描或旧 Worker 延迟返回而把本地发布状态写乱。该轮只做本地代码路径和 SQLite 专项验证，不把本地结果当成生产放行证据。

## 已核对的真实代码路径

1. `start_publish_job()` 在独立 Worker 开关打开时只创建持久化发布任务并将导出记录置为 `PENDING`，不在网页进程执行 AICM 上传。
2. `aicm_publish_worker_runtime.run_worker_once()` 扫描待处理记录后调用 `crud_yolo_bm1688_export.claim_publish()`。
3. `claim_publish()` 使用导出记录行锁、状态、租约过期时间和条件更新，并递增 `publish_fencing_token`。
4. `_run_publish_job_sync()` 续租和终态写入均携带任务号、租约持有者和 fencing token；条件不匹配时拒绝写入。
5. AICM 上传已经发生后无法撤回，因此“失租”只能阻止 LabelU 本地完成状态写入，不能宣称外部平台具备回滚能力。问题模型按方案由管理人员关闭本平台后续下载，并发布替代版本。

## 本轮测试

新增 `test_yolo_aicm_publish_claim.py` 两个场景：

- 活动租约下第二个 Worker 领取同一导出记录返回 `BUSY`；
- 旧租约过期后被新 Worker 接管，旧 Worker 使用旧 token 写完成状态返回失败，接管者状态保持不变。

执行：

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_aicm_publish_claim.py labelu/tests/internal/application/service/test_aicm_publish_worker_runtime.py -q --disable-warnings --tb=short
10 passed
```

## 尚未证明的内容

- MySQL 真实行锁、隔离级别、连接池和 2/10/20/50 并发容量；
- AICM 是否支持以业务幂等键去重外部上传；
- systemd 重启、网络超时、上传中进程终止后的真实接管；
- 真实 AICM `model_set_id`、本地平台下载目录和第三方 S3 回流 metadata 的三方对账。

## 状态

本地 L1 契约完成。生产 Worker 开关保持关闭，未连接生产、未修改生产数据、未提交、未推送、未部署。只有完成真实环境证据并经用户明确“提交代码”后，才进入提交和部署流程。
