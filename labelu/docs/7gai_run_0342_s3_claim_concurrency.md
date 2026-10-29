# RUN-0342 第三方 S3 回流并发领取本地证据（2026-09-21）

## 本轮目的

验证第三方策略系统通过 S3 回流的问题数据包，在进入导入状态机前，同一回流记录不会被多个进程重复领取。生产问题图片仍以第三方 S3 对象包为唯一来源；本轮不连接 S3、不下载图片、不创建任务。

## 代码路径

- `experiment_dataset_import_worker.run_import_worker_once()` 通过 `claim_pending()` 批量领取 `PENDING` 记录。
- `crud_experiment_dataset_import.claim_pending()` 使用状态、租约过期时间、持有者和 fencing token 的条件更新。
- `run_import_job_now()` 在每包、每样本及任务/附件/样本/预标注副作用前复用租约守卫；旧 token 不能写入完成或失败终态。

## 本地测试

测试文件：`labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py`。

新增并发档位 `2、10、20、50`。每个档位都使用同一个隔离 SQLite 文件，启动对应数量的独立进程，同时争抢同一条 `PENDING` 回流记录，并验证：

1. 只有一个进程返回一条领取记录；
2. 数据库只保存一个租约持有者；
3. 首次领取 fencing token 为 `1`，没有重复领取副作用。

执行结果：

```text
python -m pytest labelu/tests/internal/application/service/test_experiment_dataset_import_worker.py -q --disable-warnings --tb=short
14 passed
```

## 证据边界

这证明了本地 SQLite 条件领取契约，不证明生产 MySQL 的隔离级别、锁等待、连接池容量、Worker 重启接管或真实 S3/AICM 三方对账。生产 S3 回流 Worker 仍保持失败关闭，直到完成同版本 MySQL 和真实外部环境验收。

## 状态

本地 L1 完成。未提交、未推送、未部署，未修改生产数据。
