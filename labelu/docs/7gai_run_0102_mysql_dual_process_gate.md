# RUN-0102 真实 MySQL 双进程与锁等待演练入口

## 1. 范围与边界

- 日期：2026-09-18，Asia/Shanghai。
- 工作包：WP2 训练与执行器并发、WP5 测试与验收、WP6 运维与发布门禁。
- 责任角色：R03 架构主责，R09 测试验收，R10 运维协作；R04 后端复核执行租约调用边界。
- 目标：补齐真实 MySQL 双进程、行锁等待、唯一领取、旧 token 拒绝和新 token 接管的可执行入口。
- 严格边界：不连接、不修改生产数据库；不使用生产 URL、生产库名或远程主机；不把 SQLite 测试结果当作 MySQL 证据；未提交、未推送、未部署。

## 2. 本轮实现

新增 `labelu/scripts/rehearse_mysql_execution_fencing.py`，与原有候选迁移演练脚本分离：

1. 默认运行“探针”模式，只执行 `SELECT VERSION()`、当前库、隔离级别和 InnoDB 锁等待超时查询，不建表、不写行、不执行迁移。
2. 没有 `LABELU_MYSQL_REHEARSAL_URL` 时直接返回“阻断”，不自动创建 SQLite 文件，不模拟 MySQL 成功。
3. URL 必须是 MySQL、本机主机（`127.0.0.1`、`localhost` 或 `::1`），数据库名必须以 `labelu_migration_sandbox` 开头。
4. 写入演练还必须同时满足 `--模式 演练`、`--允许修改` 和环境确认 `LABELU_MYSQL_REHEARSAL_CONFIRM=YES`；确认值不打印到页面、日志或作业清单。
5. 演练模式在沙箱库建立本次运行独有的临时 InnoDB 表（支持同一沙箱库并行演练），进程 A 持有行锁；进程 B/C 同时竞争同一行，释放后仅一个取得 `fencing_token=2`，另一个因有效租约返回空；旧 `token=1` 条件写入必须为 0 行，新 token 终态写入必须为 1 行；最后只删除本次运行的临时表。
6. 进程采用 Python `spawn`，每个连接设置有限的连接和锁等待超时；异常、超时或子进程残留均以失败/阻断退出，不输出通过。

## 3. 代码、测试与数据影响

| 类型 | 文件 | 影响 |
|---|---|---|
| 演练入口 | `labelu/scripts/rehearse_mysql_execution_fencing.py` | 新增严格 MySQL 沙箱探针和双进程演练，不新增菜单、用户角色、项目维度或正式迁移 |
| 契约测试 | `labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py` | 覆盖非 MySQL、生产库名、远程主机、未授权写入、无 URL 阻断和演练开关门禁 |
| 生产数据 | 无 | 默认只读；写入仅允许显式命名沙箱库，表创建、行更新和清理均限制在该库 |
| API/页面 | 无 | 不改变用户操作、模型发布、下载、设备验证或训练接口 |

## 4. 验证证据

- `python -m pytest labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py -q`：`5 passed`（存在仓库既有弃用警告）。
- `python -m py_compile labelu/scripts/rehearse_mysql_execution_fencing.py`：通过。
- `git diff --check`（本轮目标文件）：通过。
- 无 URL 执行入口：返回状态“阻断”，原因包含“未执行 SQLite 模拟，MySQL 能力为阻断”。
- 使用本机沙箱 URL 只读探针：因本机实例未提供有效凭据返回 MySQL `1045 Access denied`；没有执行建表或写入。
- 真实双进程、真实行锁等待、唯一领取和接管：本轮**未通过/未完成**，原因是没有获得受控沙箱凭据；不得将上述契约测试写成真实 MySQL 通过。

## 5. 运行方式（仅预生产/本机沙箱）

```powershell
$env:LABELU_MYSQL_REHEARSAL_URL = "mysql+pymysql://<沙箱账号>:<密码>@127.0.0.1:3306/labelu_migration_sandbox_0102"
python labelu/scripts/rehearse_mysql_execution_fencing.py --模式 探针
$env:LABELU_MYSQL_REHEARSAL_CONFIRM = "YES"
python labelu/scripts/rehearse_mysql_execution_fencing.py --模式 演练 --允许修改
Remove-Item Env:LABELU_MYSQL_REHEARSAL_URL, Env:LABELU_MYSQL_REHEARSAL_CONFIRM
```

账号必须是专用沙箱账号，至少拥有该沙箱库临时表的创建、读写和删除权限；禁止填入生产库连接串。运行报告需要保存版本、库名、进程结果、锁等待耗时、最终 token 和清理结果，密码不得进入报告。

## 6. 结论、风险和下一依赖

- 本轮达到本地 L1：安全入口、失败关闭和门禁契约已落地。
- 未达到 MySQL L2：缺少同版本 MySQL 沙箱凭据、`SELECT ... FOR UPDATE` 锁等待、双进程接管和20并发证据。
- SQLite 跨进程测试仍仅证明应用层 token 语义，不证明 MySQL 锁等待、隔离级别、索引和容量。
- 下一依赖：R10 提供与生产版本一致的本机/预生产 `labelu_migration_sandbox*` 数据库和临时账号；R09 执行双进程、20并发、锁等待和失败清理；R03/R04 检查执行计划及正式迁移后的字段索引。
- 在真实证据完成前，训练、编译、边端验证和远程 Worker 继续保持 fail-closed，不切换生产独立消费路径。

## 7. 交付状态

- 提交：未提交，等待用户明确“提交代码”。
- 推送：未推送。
- 部署：未部署、未连接生产。
- 方案等级：本地 L1，真实 MySQL 演练未完成。
