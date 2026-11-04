# RUN-0348 生产 SQLite 迁移 MySQL 与 18001 切换（2026-11-04）

## 1. 目标与结论

将 18001 生产库从 SQLite 切换到本机 MySQL 8.0，解决批量上传导致 SQLAlchemy 连接池耗尽、Web 进程假死的问题。

- 目标库：`labelu_prod`（utf8mb4 / utf8mb4_unicode_ci，114 张业务表）
- 应用账号：`labelu_app`（仅授权 `labelu_prod`）
- 迁移工具：项目既有 `labelu/scripts/migrate_to_mysql.py`，本次修复后使用
- 源库：`/data/7G_AI/data/labelu.sqlite`（2.6GB）
- 结果：迁移后行数与内容哈希一致，18001 冷启动、登录、任务列表、标注页、心跳、上传/删除均通过

## 2. 根因

生产使用 SQLite，但 `db.py` 只对 MySQL 应用连接池配置，SQLite 保持 SQLAlchemy 默认 `5 + 10`。批量上传时 `attachment.create` 在流式读文件期间一直占用请求级连接，15 个连接耗尽后请求排队 30 秒并抛 `QueuePool`，同步线程池被占满，`/healthz` 也无法响应。

## 3. 代码改动

| 提交 | 内容 |
|---|---|
| `45c9ad986` | SQLite 应用连接池配置（20+30）；上传流式读取前 `db.close()`；`/healthz`、`/version` 改 async |
| `f26ea4821` | MySQL 迁移支持：跳过 SQLite 中已退休表的悬空外键、`resolve_fks=False`、循环依赖回退为名称顺序、复制期间关闭外键检查、行级失败 fail-closed、复制后逐表行数校验 |
| 本 RUN | `phase_one_schema` 白名单补齐 `annotation_review_event`、`system_operation_log`、`user_activity_session`；MySQL 目标库删除 20 个指向已退休 `project` 表的外键 |

## 4. 执行步骤

1. 本地与预演库验证：SQLite 快照 → `labelu_rehearsal`，复制 161 秒，114 表行数校验通过；用 MySQL 启动独立 8010 实例验证登录、任务、心跳。
2. 创建 `labelu_prod`，以 `_bootstrap_empty_database` 创建一期 114 表并 stamp Alembic head `zr4s5t6u7v8`。
3. 删除目标库中 20 个 `REFERENCES project` 外键（一期已退休项目控制面）。
4. 停止 supervisor 全部服务，冻结写入。
5. 以修复后的 `DatabaseMigrator` 从 SQLite 直接复制到 `labelu_prod`，167 秒，114 表行数校验通过。
6. 修改 `/data/7G_AI/config/app.env`：
   - `DATABASE_URL=mysql+pymysql://labelu_app:***@127.0.0.1:3306/labelu_prod?charset=utf8mb4`
   - `LABELU_DEV_DATABASE_URL=`（清空）
   - `DATABASE_POOL_SIZE=30`、`DATABASE_MAX_OVERFLOW=60`、`DATABASE_POOL_TIMEOUT=30`
7. **重启 supervisord 本身**。`guarded_supervisor.py` 启动时 `load_dotenv(override=True)`，旧进程把 SQLite 的 `DATABASE_URL` 写进了 supervisord 环境，子进程会覆盖 app.env；仅重启 7gai-web 不够。
8. 启动 `7gai-web`，再启动 8 个 Worker。
9. 将 SQLite 重命名为 `labelu.sqlite.pre-mysql-20261104`，使 `need_migration_to_mysql` 不再触发，并保留回滚副本。

## 5. 数据校验

| 表 | SQLite | MySQL |
|---|---:|---:|
| user | 15 | 15 |
| task | 18 | 18 |
| task_sample | 36371 | 36371 |
| task_pre_annotation | 81284 | 81284 |
| task_attachment | 38025 | 38025 |
| sample_edit_log | 1636 | 1636 |
| system_config_history | 60731 | 60731 |

`user`、`task`、`task_sample`、`task_pre_annotation` 前 100 行内容 SHA256 摘要一致；中文任务名、预标注 JSON 解析正常。

## 6. 线上功能验证

- `/healthz`、`/readyz`、`/tasks` 均 200
- 登录、任务列表、任务详情、样本列表、标注页标签渲染正常，无 5xx
- `/user-activity/heartbeat` 写入 MySQL（会话 `mysql-final-check`、`mysql-coldstart-check`），SQLite 不再新增
- 任务 27 上传 4×4 PNG：201，2.15 秒；删除附件：200
- 重命名 SQLite 后重启 7gai-web：冷启动成功，数据库 `ok`
- 重启后日志：`ERROR=0`、`QueuePool=0`、5xx=0

## 7. 回滚

1. `supervisorctl stop all`
2. 恢复 `/data/7G_AI/config/app.env.bak.20261104173216`（SQLite 配置）
3. `mv /data/7G_AI/data/labelu.sqlite.pre-mysql-20261104 /data/7G_AI/data/labelu.sqlite`
4. 重启 supervisord（不是只重启 7gai-web，原因同上）
5. 校验 `/healthz`、`/tasks`

MySQL `labelu_prod` 可保留用于重试；SQLite 文件与备份不删除。

## 8. 风险与后续

- 目标库删除了 20 个 `project` 外键，列 `project_id` 仍保留；一期代码不再创建项目控制面，写入以 NULL 为主。
- MySQL 数据目录在 `/`（剩余约 20GB），需关注 binlog 与旧库 `labelu`、`labelu_snapshot_20260710` 的磁盘占用。
- SQLite 回滚副本保留 24-48 小时；确认稳定后可另行归档。
- 后续新装实例需使用补齐后的 `phase_one_schema` 白名单，避免再次缺表。
