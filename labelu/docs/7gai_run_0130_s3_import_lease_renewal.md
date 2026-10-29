# RUN-0130 第三方 S3 回流长任务租约续期

## 1. 发现的问题

RUN-0128/0129 已经做到数据库领取和 Worker 部署，但一个大 S3 数据包的下载、图片落盘和任务写入可能超过 300 秒。若只在开始时领取一次租约，旧 Worker 仍在执行时可能被新 Worker 误认为过期并接管。

## 2. 本轮处理

- 新增 `renew_worker_lease`，只能由当前 `owner + fencing_token` 延长租约。
- Worker 执行期间启动独立短 Session 心跳，按租约的三分之一周期续期，周期限制在 5 至 60 秒。
- 心跳续期失败或 owner/token 已变化时标记失租；当前执行不会再写入最终状态，旧租约释放也受 owner/token 条件保护。
- 不改变 S3 下载、metadata 校验、去重、任务创建、预标注和人工确认后 DatasetVersion 冻结门槛。

## 3. 验证

- Worker 租约与续期专项：`3 passed`。
- 原第三方 S3 导入路由回归：`19 passed`（本轮未重复长跑，RUN-0128 已有证据）。
- 相关 Python `compileall`：通过。
- `git diff --check`：通过。

## 4. 边界

续期心跳只解决数据库租约过期和旧 Worker 终态写入问题，不等于真实 S3 下载超时、临时文件清理、MySQL 锁等待或大包容量已经通过。生产前仍需 Ubuntu/systemd、同版本 MySQL 双进程和真实 S3 大包重启接管验证。

本轮未提交、未推送、未部署。

