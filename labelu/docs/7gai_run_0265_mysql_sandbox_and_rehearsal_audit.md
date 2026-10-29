# RUN-0265 本机 MySQL 沙箱与迁移演练脚本只读审查

- 日期：2026-09-20（Asia/Shanghai）
- 类型：环境能力与演练覆盖范围审查
- 责任角色：R03 架构、R09 测试、R10 运维
- 工作包：WP2 数据库迁移与并发、WP5 测试放行、OPS
- 安全边界：只检查进程、服务、监听、环境变量和脚本源码/测试；不连接数据库，不执行 DDL/DML，不读取业务数据
- 验收等级：L1 只读审查；迁移放行仍失败关闭

## 1. 本机环境事实

### 1.1 MySQL 服务

只读检查结果：

| 检查项 | 结果 | 说明 |
|---|---|---|
| Windows 服务 | `MySQL90` 为 `Running`，`Automatic` | 本机确实存在 MySQL 服务 |
| MySQL 进程 | 发现两个 `mysqld.exe` 进程 | 进程信息未用于连接或查询 |
| 监听端口 | `0.0.0.0:3306`、`[::]:3306` | 对所有本机网卡监听，不能据此认定为安全沙箱 |
| 服务配置 | `D:\mysql\bin\mysqld.exe`，数据目录为 MySQL Server 9.0 Data | 版本/目录信息来自服务配置只读检查 |
| Docker 服务 | `com.docker.service` 为 `Stopped` | Docker Desktop Linux 引擎不可用 |
| Docker 客户端 | 客户端存在，但连接 `dockerDesktopLinuxEngine` 失败 | 无法创建隔离 MySQL 容器 |
| `LABELU_MYSQL_REHEARSAL_URL` | 当前进程未配置 | 没有显式沙箱 URL |
| `LABELU_MIGRATION_SANDBOX_URL` | 当前进程未配置 | 没有迁移闸门沙箱 URL |
| 仓库 `.env` | `DATABASE_URL` 指向 `localhost:3306/labelu` | 这是业务库名，不是 `labelu_migration_sandbox*`，本轮未连接 |

结论：本机“有 MySQL”不等于“有可放行的 MySQL 沙箱”。当前实例没有通过仓库安全门禁所要求的 `labelu_migration_sandbox*` 库名证明，也没有脱敏快照、备份恢复目标或演练授权证据。严禁把 `.env` 的 `labelu` 直接传给演练脚本。

## 2. 现有演练脚本覆盖情况

| 能力 | 现有脚本 | 已覆盖 | 未覆盖/限制 |
|---|---|---|---|
| 候选结构首次升级 | `rehearse_dispatch_lease_migration.py`、`rehearse_phase_one_migration.py` | 在合成三张表上执行候选 `upgrade` | 不是完整生产快照，不验证当前正式 head 的全链升级 |
| 重复升级 | 同上 | 同一候选连续执行两次并检查字段/索引 | 只证明脚本级幂等，未在真实生产行数/真实 MySQL 沙箱留证 |
| 正式租约迁移 | `test_execution_lease_formal_migration.py` | SQLite 中验证正式迁移字段、索引和禁止危险降级 | SQLite 不能证明 MySQL 在线 DDL、锁等待和数据兼容 |
| 备份与恢复 | `migration_release_gate.py`、`verify_staging_and_hil.py` | 只读校验 JSON 证据字段、摘要格式和恢复结果字段 | 没有脚本实际执行 `mysqldump`、恢复到独立实例、校验表/行/索引或计算摘要 |
| 锁等待 | `rehearse_mysql_execution_fencing.py` | 真实 MySQL 模式下创建临时 InnoDB 表，持锁并记录竞争等待 | 使用合成表，不是四张业务表；一次运行只有一个并发档位 |
| 旧 fencing token 拒绝 | `rehearse_mysql_execution_fencing.py` | 检查旧 token 更新影响行数为 0，新 token 完成写入为 1 | 只验证通用租约 SQL，不验证训练/BM1688/边端/S3 真实服务写路径 |
| `2/10/20/50` 并发矩阵 | `rehearse_mysql_execution_fencing.py` | 参数允许 `2-64`，单次可运行某一档 | 没有一次性执行并汇总 `2/10/20/50` 四档；发布闸门只消费外部矩阵 JSON |
| 迁移发布顺序 | `scripts/ops/migrate_deploy.sh` | 只读闸门在 `alembic upgrade head` 前调用，缺证据失败关闭 | 闸门不验证证据由哪个数据库、哪个提交和哪次演练生成 |

## 3. 本地测试证据

执行：

```text
python -m pytest labelu/tests/alembic/test_execution_lease_formal_migration.py labelu/tests/alembic/test_dispatch_lease_migration_candidate.py labelu/tests/alembic/test_phase_one_migration_rehearsal.py labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py labelu/tests/internal/common/test_migration_release_gate.py scripts/tests/test_migration_release_deploy_gate.py -q --disable-warnings --tb=short
```

结果：`29 passed, 124 warnings in 10.70s`。

这组结果只证明安全边界、脚本输入约束、SQLite 合成结构演练和闸门字段校验可运行；没有产生真实 MySQL、备份恢复、锁等待或四档并发通过证据。

## 4. 迁移放行缺口

1. **沙箱身份缺口**：MySQL90 监听的是通用 `3306`，当前没有可核验的独立沙箱库、实例标识、脱敏快照和权限边界。
2. **备份恢复缺口**：现有实现只验证 JSON 形状，不会创建备份、恢复到独立目标，也不会对恢复后的表结构和关键行数做自动对账。
3. **矩阵执行缺口**：通用 fencing 脚本每次只跑一个 `--并发数`；需要外层编排依次跑 `2/10/20/50`，保存每档锁等待、重复领取、旧 token 拒绝、进程退出和最终状态。
4. **业务表覆盖缺口**：当前并发演练只建一张合成表，不能替代训练、BM1688、边端和 S3 任务表各自的 MySQL 领取/接管证据。
5. **证据溯源缺口**：迁移闸门不会校验证据文件关联的 Git 提交、数据库版本、快照摘要、演练脚本版本和实际目标库；可读 JSON 不等于实际执行证明。

## 5. 下一步放行条件（本轮不执行）

R10 需提供隔离实例或容器，明确：独立主机/端口、库名以 `labelu_migration_sandbox` 开头、同版本 MySQL、脱敏快照、临时账号和备份恢复目标。取得后按顺序完成：

1. 在沙箱恢复脱敏快照，记录实例版本、`alembic_version`、表结构和索引摘要。
2. 执行正式候选首次升级和重复升级，保存日志与结构对账；禁止使用仓库 `.env` 的业务库。
3. 使用真实备份工具恢复到独立目标，验证关键表行数、字段、索引和摘要。
4. 对训练、BM1688、边端、S3 四类业务表分别执行锁等待和旧 token 接管演练。
5. 由外层脚本自动执行 `2/10/20/50` 四档并发，输出不可覆盖的原始结果、汇总 JSON 和摘要。
6. 让 `migration_release_gate.py` 只读取上述证据；证据不完整或来源无法追溯时继续失败关闭。

## 6. 交付状态

- 本轮变更：新增本只读审查文档。
- 数据库：未连接、未创建表、未执行迁移、未写入业务数据。
- 正式 Alembic：未修改。
- 提交、推送、部署：未执行，等待用户明确授权。
- 结论：本机具备 MySQL 服务但不具备已证明的迁移沙箱；现有演练脚本可作为结构/通用锁竞争组件，不能单独满足备份恢复和 `2/10/20/50` 生产放行证据。
