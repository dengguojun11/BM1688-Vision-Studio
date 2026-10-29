# RUN-0099 本地 YOLO 阶段文件执行世代隔离（本地 L1）

- 日期和时区：2026-09-18，Asia/Shanghai。
- 方案章节和执行契约：主方案第 8、11、15.7 节；本地训练阶段文件世代隔离契约 `LOCAL-TRAIN-PHASE-GENERATION-01`。
- 工作包和角色：WP2 训练与执行器；R04 后端主责，R06 模型算法、R09 测试协作。
- 本轮目标：阻止旧本地 YOLO 训练进程在新进程接管作业后迟到写入 `phase.json`，覆盖当前训练阶段；保持没有执行租约的历史本地训练路径兼容。

## 实际代码变化

- `_write_train_phase()` 增加可选 `execution_owner`、`execution_fencing_token` 参数。
- 带执行上下文的写入，每次先按作业数据库中的 owner/token/未过期条件确认租约；校验失败或数据库不可确认时拒绝写入。
- 不带执行上下文的历史路径，在发现作业存在有效执行租约时拒绝写入；没有有效租约时保持原有本地文件进度行为。
- 带租约写入的 `phase.json` 增加 `execution_owner`、`execution_fencing_token` 和 `phase_generation`（格式为 `owner:token`），用于现场审计和旧文件识别。
- 本地 YOLO 训练、训练收尾、暂停/失败收尾以及训练子进程父包装均透传当前执行上下文；未新增菜单、角色、项目维度或数据库字段。

## 本地证据

```text
python -m pytest labelu/tests/internal/application/service/test_yolo_train_phase_generation.py labelu/tests/internal/application/service/test_yolo_train_summary.py -q
10 passed

python -m compileall -q \
  labelu/internal/application/service/yolo_train.py \
  labelu/internal/application/service/yolo_train_queue.py \
  labelu/tests/internal/application/service/test_yolo_train_phase_generation.py
通过

git diff --check -- \
  labelu/internal/application/service/yolo_train.py \
  labelu/internal/application/service/yolo_train_queue.py \
  labelu/tests/internal/application/service/test_yolo_train_phase_generation.py
通过
```

专项测试覆盖：有效租约存在时无上下文旧写入被拒绝；旧 token 的带上下文写入被拒绝；当前 token 写入成功并记录阶段世代；既有阶段摘要读取测试保持通过。

## 并发、失败和真实性边界

- 阶段文件写入从“按 job_id 直接覆盖”收口为“先租约确认再原子替换”；原子替换只解决单机文件完整性，不能替代数据库租约、HMAC 或跨主机文件系统一致性。
- 本轮没有新增数据库锁和轮询；每次阶段变化多一次轻量作业租约查询，训练大段耗时不受影响。高频 epoch 回调仍写日志而不是频繁写 `phase.json`。
- 尚未在真实 MySQL 双进程、systemd 重启、训练子进程被杀后迟到写入、GPU 释放、远程节点和生产文件系统中验证；独立训练 Worker 在全链路 fencing 完成前继续保持 fail-closed。

## 本地、预生产和生产证据

- 当前仅有隔离本地单测、编译和差异检查证据，未连接生产数据库、真实 GPU、S3、AICM、TPU-MLIR 或 BM1688。
- 未提交、未推送、未部署；等待用户明确说“提交代码”。本轮不能标记为预生产或生产完成。

## 下一轮依赖

1. R03/R09 在同版本 MySQL 脱敏快照上执行双进程接管、锁等待和旧阶段文件迟到写入验证。
2. R04/R06 将相同 owner/token 透传原则补齐车牌训练及仍存在的生命周期收尾路径。
3. R07/R10 在真实训练节点验证进程重启、GPU 释放、共享存储和 systemd 日志；证据完成前不开放独立 YOLO 训练 Worker 生产消费。

本轮结论：本地 YOLO 阶段文件的旧进程覆盖风险达到本地 L1；真实 MySQL、节点重启、GPU/共享存储和生产放量仍未完成。
