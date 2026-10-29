# RUN-0166 MySQL 沙箱证据收口与正式迁移链放行阻断

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R03 架构、R09 测试、R10 运维；WP0/U0B。
- 本轮性质：证据对账和放行门禁收口，不执行正式 Alembic DDL，不连接生产。

## 已对账证据

RUN-0163 已提供本机 MySQL 9.0.1、`REPEATABLE-READ` 沙箱证据：

- 20 个竞争者领取执行租约时只有 1 个成功；旧 fencing token 的写入为 0 行，新 token 完成写入为 1 行。
- 第08段候选在本机 MySQL 重复升级/降级通过，租约字段和索引不重复创建。
- 第01段历史数据候选在沙箱中保留非空历史行，移除两张表的 `project_id`；
  `dataset_version.dataset_id` 外键继续存在。
- 首次沙箱演练发现 MySQL 外键名为库级唯一，SQLite batch 重建策略会触发 `errno 1826`；
  候选已改为 MySQL 显式删除旧外键、删除列、按稳定名称重建其余外键，SQLite 仍使用
  batch 重建。

## 当前本地复核

```text
python -m pytest labelu/tests/scripts/test_rehearse_dataset_project_fk_rebuild.py labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py -q --disable-warnings --tb=short
7 passed
```

迁移审计与候选链专项继续保持通过；第01段 SQLite 非空历史行演练也已覆盖列/值、行数、
索引、外键和重复执行幂等。

## 正式 Alembic 链阻断

本轮证据只能证明本机沙箱，不等于生产迁移已经安全。候选文件仍在
`.tmp/phase-one-rebuild/supplemental`，正式 `labelu/alembic_labelu/versions` 没有替换；
当前代码图唯一 head 为 `z9f3a4b5c6d7`，而生产基线为 `h7i8j9k0l1m2`，生产基线之后的旧后缀
不能直接执行，也不能追加一个清理迁移制造第三个分支。

以下条件未全部满足前，禁止把迁移等级写成生产放行：

1. 同版本 MySQL 脱敏生产快照从 `h7i8j9k0l1m2` 升级成功；
2. 22 张项目外键表逐表完成列、索引、外键、字符集和行数摘要对账；
3. 大表在线 DDL、元数据锁等待、失败中止和重试策略有实测证据；
4. 升级前备份可恢复到独立实例，并完成应用兼容性检查；
5. 新八段链唯一 head、空 SQLite、空 MySQL 和遗留表白名单验收通过；
6. 训练、BM1688、第三方 S3/AICM 业务 Worker 仍保持关闭，直到迁移和回归证据完成。

## 交付状态

本轮为本地 L2-沙箱。未提交、未推送、未部署，未写生产数据库。下一切片应继续在同版本
MySQL 脱敏快照副本执行结构/行数对账；正式迁移目录和生产数据库保持不变。

