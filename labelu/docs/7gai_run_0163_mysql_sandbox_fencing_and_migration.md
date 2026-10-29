# RUN-0163 本机 MySQL 沙箱执行租约与第01段迁移演练

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R07 BM1688 边端、R09 测试、R10 运维；WP0/WP2/WP3/WP5。
- 目标：把执行租约和迁移候选从 SQLite 证据推进到本机 MySQL 沙箱证据，不连接 `labelu` 业务库和生产环境。

## 环境证据

- MySQL：`9.0.1`，`REPEATABLE-READ`。
- 沙箱库：`labelu_migration_sandbox_20260919`，本机 `127.0.0.1`。
- 业务库 `labelu` 未作为演练目标；脚本只接受 `labelu_migration_sandbox*`。

## 执行租约演练

命令：

```text
python -m labelu.scripts.rehearse_mysql_execution_fencing --url <本机沙箱URL> --模式 演练 --允许修改 --并发数 20 --持锁秒数 1
```

结果：

- 20 个竞争进程中只有 1 个获得新 fencing token；19 个未领取。
- 旧 token=1 的迟到写入返回 0 行，新 token 写入 1 行。
- 最终状态为 `COMPLETED`，演练表自动清理。
- 锁等待约 1.017～1.049 秒，符合释放行锁后的竞争行为。

## 第08段候选结构演练

命令：

```text
python -m labelu.scripts.rehearse_phase_one_migration --replacement-versions .tmp/phase-one-rebuild/versions --url <本机沙箱URL> --允许修改
```

结果：三张执行表 15 个租约字段、15 个租约索引，重复升级幂等，降级只移除候选租约对象；八段离线审计仍为唯一 `gai_p1_08` head。

## 本轮新增脚本

- `labelu/scripts/rehearse_dataset_project_fk_rebuild.py`
- `labelu/tests/scripts/test_rehearse_dataset_project_fk_rebuild.py`

该脚本只接受本机沙箱，创建两张带非空历史行的表，运行 `.tmp/phase-one-rebuild/supplemental/gai_p1_01_project_fk_rebuild.py`，核对 `project_id` 移除、历史行保留和 `dataset_version.dataset_id` 外键保留，最后删除自己创建的测试表。

首次 MySQL 演练发现并修复了候选迁移的外键命名问题：MySQL 外键名在库级别唯一，直接使用 SQLite batch 重建会触发 `errno 1826`。候选迁移现对 MySQL 显式删除旧外键、移除列并以稳定新名称重建保留外键；SQLite 继续使用 batch 重建路径。

## 真实边界

本轮证明的是本机 MySQL 沙箱，不是生产数据库。仍未完成：脱敏生产快照升级、生产规模 `EXPLAIN`、元数据锁、在线 DDL、真实 GPU/BM1688、S3/AICM 和部署后线上验证。未提交、未推送、未部署。
