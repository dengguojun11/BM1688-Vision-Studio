# RUN-0172 S3 回流 Worker 程序化入口 readiness 门禁

**日期**：2026-09-20  
**环境级别**：本地 L1（未提交、未推送、未部署）  
**对应工作包**：WP4 第三方回流、WP5 测试验收、OPS Worker 切换  
**责任角色**：R03 架构、R04 后端、R09 测试、R10 运维；R01 产品协作

## 1. 本轮问题

第三方 S3 回流 Worker 有命令行 `main()` 和程序化 `run_import_worker_once()` 两个入口。原实现只有
`main()` 在循环前调用 `dedicated_worker_ready()`；API 看门狗、测试调度器或其他程序化调用可以直接
进入 `claim_pending()`，绕过数据库租约字段、生产安全配置和迁移状态检查。

这会造成“Worker 看起来开启，但实际 schema/安全门禁未通过仍领取任务”的生产风险。

## 2. 已落地改动

- `dedicated_worker_ready()` 增加可选的 `enabled` 参数，既能检查生产配置，也能让受控的程序化调用保持
  与本次 `active` 开关一致。
- `run_import_worker_once()` 在任何 `claim_pending()` 之前统一调用 readiness 检查。
- readiness 失败时返回 `enabled=True, ready=False` 和明确原因，不创建数据库租约、不领取任务、不执行
  S3 下载或任务写入。
- 命令行入口仍保留启动前预检；循环调用 `run_import_worker_once()` 时会再次保护，避免未来新增调用方绕过门禁。

## 3. 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q --disable-warnings --tb=short
10 passed

python -m pytest \
  labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py \
  labelu/tests/internal/application/service/test_yolo_train_worker.py \
  labelu/tests/internal/application/service/test_bm1688_worker_runtime.py -q --disable-warnings --tb=short
29 passed
```

新增关键断言：程序化 readiness 失败时 `claim_pending` 调用次数为零。

同时修正文档事实：训练 Worker 当前主链路是 `training.worker.command.v1` Outbox 消费，不再把训练表扫描
写成当前主路径；BM1688、边端和 S3 仍按各自任务表租约说明。

## 4. 生产边界与未完成项

本轮未连接生产 MySQL、S3、AICM、systemd 或第三方平台，未领取生产任务，未执行真实回流。
本地 SQLite 和单元测试不能替代生产证据。

仍需完成：

1. 同版本 MySQL 上验证 readiness 字段检查、20 并发唯一领取、锁等待和过期接管。
2. Ubuntu/systemd 启动、重启、旧进程退出和新进程接管演练。
3. 真实第三方 S3 `metadata.json`、图片对象、AICM `model_set_id` 和任务血缘对账。

在上述证据和用户明确“提交代码”之前，Worker 开关保持关闭，当前改动保持本地 L1，不提交、不推送、不部署。
