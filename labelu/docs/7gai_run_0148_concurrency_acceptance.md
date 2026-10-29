# RUN-0148 并发与 Worker 重启接管验收契约

- 日期和时区：2026-09-19，Asia/Shanghai。
- 负责角色：R03 架构、R09 测试、R10 运维；覆盖 WP2 训练执行、WP3 BM1688/边端执行、WP5 生产运维。
- 验收等级：本地 L1；没有连接生产、没有修改生产数据库、没有启动真实 GPU/TPU-MLIR/BM1688 或 Ubuntu systemd。

## 本轮目标

把“同一任务不能被两个 Worker 同时执行”和“Worker 重启后能由新进程接管”变成可执行的验收契约：

1. 真实 MySQL 沙箱中由进程 A 持有 InnoDB 行锁，N 个进程同时领取同一条记录；N=20 是生产标准验收值。
2. 领取必须只有一个成功者，其余返回空；记录每个竞争者的真实锁等待时间，不能使用 SQLite 结果替代。
3. 旧进程 token 在新进程接管后不能写入终态；新 token 可以写入完成态。
4. systemd 单元默认关闭；开启后使用失败自动重启、停止超时和 SIGTERM，部署/停止时先清理旧单元，避免 Web 进程和独立 Worker 双消费。

## 实施内容

### 1. MySQL 多进程演练

`labelu/scripts/rehearse_mysql_execution_fencing.py` 新增 `--并发数` 参数，允许 2-64 个真实竞争进程，默认 2，生产放行必须显式执行 `--并发数 20`。脚本继续要求：

- URL 必须是本机 MySQL（`127.0.0.1`、`localhost` 或 `::1`）；
- 库名必须以 `labelu_migration_sandbox` 开头；
- 写入必须同时传 `--模式 演练 --允许修改` 和 `LABELU_MYSQL_REHEARSAL_CONFIRM=YES`；
- 不接受 SQLite、远程地址、生产库名，也不会把“未配置沙箱”写成通过。

演练输出包含并发数、锁等待记录、唯一领取、旧 token=1 拒绝、新 token 写入和最终行状态。演练结束删除临时表。

### 2. systemd 生命周期契约

训练、BM1688、边端验证、第三方 S3 回流四个独立 Worker 单元均要求：

- 开关默认值为 `0`，未完成真实设备/迁移/并发证据不得开启；
- `Restart=on-failure`、`RestartSec=5`、`TimeoutStopSec=120`、`KillSignal=SIGTERM`；
- 关闭开关时执行 `systemctl disable --now` 并删除单元；
- 开启独立 Worker 时，API 单元显式关闭对应 Web 进程消费者，防止重启后双领取；
- Worker 入口使用受限的读写目录和资源参数。

训练 Worker 单元补齐停止超时和终止信号；API 单元同步具备显式停止边界。

## 本地证据

执行命令：

```text
$env:LABELU_TEST_DB_NAME='run0148-concurrency'
python -m pytest labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py labelu/tests/scripts/test_worker_systemd_restart_contract.py -q --disable-warnings --tb=short
python -m py_compile labelu/scripts/rehearse_mysql_execution_fencing.py
git diff --check
```

结果：`8 passed`，脚本编译通过，差异检查通过。测试验证并发参数范围（2、20、64 可接受，1 和 65 拒绝）、MySQL/沙箱/写入授权门禁，以及四类 systemd 单元的重启和双消费者隔离契约。

## 真实 MySQL 沙箱执行方式

在与生产同版本的本机隔离库准备完成后，才允许执行：

```text
$env:LABELU_MYSQL_REHEARSAL_URL='mysql+pymysql://<账号>:<密码>@127.0.0.1:3306/labelu_migration_sandbox_0148'
$env:LABELU_MYSQL_REHEARSAL_CONFIRM='YES'
python labelu/scripts/rehearse_mysql_execution_fencing.py --模式 演练 --允许修改 --并发数 20 --持锁秒数 1
```

通过条件：20 个竞争者只有 1 个领取成功、19 个领取失败；旧 token 更新影响行数为 0；新 token 更新影响行数为 1；锁等待时间、隔离级别和最终行状态均有 JSON 证据。未执行该命令前，MySQL 并发、锁等待、接管均保持“未验收”。

## 仍未完成的生产验收

- 当前机器没有可确认的同版本 MySQL 沙箱，本轮未执行真实 20 进程演练；SQLite 只能验证代码门禁，不能证明 InnoDB 锁等待和执行计划。
- 未执行生产快照副本的 `SHOW CREATE TABLE`、`SHOW INDEX`、`EXPLAIN FORMAT=JSON`、元数据锁观察和短 `lock_wait_timeout` 测试。
- 未在 Ubuntu/systemd 上实际停止、重启、接管训练/BM1688/边端/S3 Worker，未测 GPU 释放和板卡资源独占。
- 未连接生产地址、生产 MySQL、第三方 S3、AICM、TPU-MLIR 或 BM1688；生产 Worker 开关继续保持关闭。

## 交付状态

- 修改：`labelu/scripts/rehearse_mysql_execution_fencing.py`、`labelu/tests/scripts/test_rehearse_mysql_execution_fencing.py`、`labelu/tests/scripts/test_worker_systemd_restart_contract.py`、`deploy.sh`。
- 未提交、未推送、未部署；等待用户明确说“提交代码”。
- 本轮没有模型回滚动作；已发布模型如发现问题仍按既定人工关闭下载处理。

