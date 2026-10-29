# RUN 0247 MySQL 沙箱能力探针与迁移证据边界

## 目标

验证本机是否具备执行 LabelU 正式迁移前置演练所需的真实 MySQL 沙箱，并把能力探针结果作为角色团队交付记录。该记录只覆盖能力和证据边界，不把 SQLite 测试或 Docker 客户端存在误判为 MySQL 通过。

## 涉及范围

- 菜单：训练中心、模型中心、进化中心、系统管理。
- 工作包：WP2 数据库与并发、WP5 测试放行、OPS 生产运维。
- 主责角色：R03 架构、R09 测试、R10 运维。
- 协作角色：R04 后端、R06 模型算法、R07 BM1688 边端。

## 检查内容

1. 检查本机 Docker 客户端和引擎状态。
2. 检查本机 `mysqld`、`mysql`、`mariadb` 命令是否可用。
3. 执行 `rehearse_mysql_execution_fencing.py` 的严格 URL 校验与只读能力路径。
4. 执行迁移发布闸门、MySQL 演练和异步评估专项测试。

## 结果

- Docker 客户端存在，但 Docker Desktop Linux 引擎未启动，无法创建隔离 MySQL 容器。
- 本机未发现 `mysqld`、`mysql` 或 `mariadb` 可执行文件。
- 未提供 `LABELU_MYSQL_REHEARSAL_URL`，真实 MySQL 演练保持阻断。
- 不连接生产数据库，不执行生产迁移，不修改生产数据。
- 专项验证命令：

```text
python -m pytest labelu/tests/internal/common/test_migration_release_gate.py labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py labelu/tests/internal/application/test_yolo_auto_eval.py -q
```

- 本地结果：`23 passed, 123 warnings`。

## 放行结论

本轮为 L1 证据边界记录，不是 L2 迁移放行。正式迁移仍须在同版本本机 MySQL 沙箱完成首次升级、重复升级、备份恢复、锁等待和 `2/10/20/50` 并发矩阵，并验证旧执行代次的迟到写入被拒绝。没有这些证据，迁移和生产 Worker 开关继续保持失败关闭。

## 变更与生产状态

- 代码：无业务代码变更。
- 数据库：无迁移、无写入。
- 生产：未连接、未部署、未验证。
- 提交：未提交，等待用户明确“提交代码”。
- 下一依赖：R10 提供可复用的本机隔离 MySQL 沙箱；获得能力后再创建 RUN-0248 候选迁移验证记录。
