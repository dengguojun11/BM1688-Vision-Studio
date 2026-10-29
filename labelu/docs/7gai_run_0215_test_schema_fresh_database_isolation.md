# RUN-0215 一期去项目化测试库结构隔离

## 本轮目的

全量回归发现主动学习全局工作流、第三方预标注任务和媒体资产写入被旧 SQLite 文件中的 `project_id NOT NULL` 约束拦截。代码 ORM 与正式 Alembic 已经把这些历史字段改为可空，问题来自 SQLite `create_all` 不会修改已存在表结构。

## 角色与工作包

| 角色 | 工作包 | 本轮交付 | 验收结果 |
|---|---|---|---|
| R01 产品 | WP0 | 确认一期全局数据链不恢复项目默认值，历史项目字段只兼容 | 规则确认 |
| R03 架构 | WP2 | 核对正式迁移 `z5a6b7c8d9e0`、`z6b7c8d9e0f1`、`z9e2f3a4b5c6` 已覆盖媒体、供应商任务和工作流 | 不新增重复迁移 |
| R04 后端 | WP1/WP2 | 保持 ORM 三表 `project_id nullable=True` 和全局写入服务不变 | 现有实现符合一期 |
| R09 测试 | WP5 | 默认测试库名改为进程号加本次启动时间，避免复用旧 SQLite 非空约束；显式 `LABELU_TEST_DB_NAME` 仍支持迁移演练 | 去项目化专项 `43 passed` |
| R10 运维 | WP5 | 记录本地测试库与生产数据库边界 | 未连接生产 |

## 修改范围

- `labelu/tests/conftest.py` 默认测试数据库改为 `test-category-scoped-pools-{进程号}-{启动时间}.db`。
- 保留 `LABELU_TEST_DB_NAME` 覆盖入口，迁移预演和结构兼容测试仍可固定数据库文件。
- 不修改生产 ORM、Alembic、MySQL 表结构、媒体存储或业务接口。

## 验证结果

```text
去项目化相关路由/服务专项：43 passed
内部全量回归：2283 passed, 25 skipped, 391 warnings
python -m py_compile：通过
git diff --check：通过
前端 `npm run type-check`：通过
前端 `npm run build`：通过（存在既有依赖 `use client` 与大包体警告）
```

此前失败的全局工作流、摄取、供应商任务、历史预标注桥接和供应商 Worker 用例均在新鲜测试库中通过。

## 并发与失败关闭

- 测试数据库继续按进程隔离，媒体目录继续按进程隔离；本次额外按启动时间隔离，避免进程号复用旧文件。
- 生产仍依赖正式迁移闸门、备份恢复和 MySQL 结构证据，不把本地新鲜 SQLite 结果当成生产迁移证据。
- 显式指定旧测试库时仍会暴露真实结构问题，不通过隐式修补掩盖迁移缺口。

## 生产边界与遗留项

本轮只读探针实测生产 `http://113.141.72.175:10000`：`/openapi.json` 返回 JSON 200；`/healthz`、`/readyz`、`/version` 返回 SPA HTML。OpenAPI 实际暴露的 `/api/v1/published-models`、`/api/v1/yolo-trained-models`、`/api/v1/training-evaluation-datasets`、`/api/v1/dataset-categories` 和 `/api/v1/tasks` 未登录均返回 401；另测简化路径 `/api/v1/datasets`、`/api/v1/model-versions`、`/api/v1/algorithms` 返回 404。由此只能确认旧部署可访问 OpenAPI 和认证门禁，不能确认两类账号业务链和健康探针已完成验收。

本轮未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688，未提交、未推送、未部署。生产仍需完成正式 head 对账、同版本 MySQL 迁移/备份恢复、两类账号和真实边端证据。
