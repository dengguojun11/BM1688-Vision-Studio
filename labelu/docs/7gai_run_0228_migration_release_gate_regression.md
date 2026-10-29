# RUN-0228 迁移、发布闸门与部署契约回归

## 1. 目标

验证一期去项目化迁移、执行租约字段、发布闸门和部署脚本在本地能失败关闭：正式 Alembic 链唯一、候选迁移不会绕过父链校验、缺备份/并发证据不连接数据库；Nginx 将 API/健康探针交给后端而不是 SPA；systemd Worker 单元默认关闭或按门禁启用。

## 2. 角色与交付

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP2 | 去项目化 schema、正式迁移链、租约字段和索引闭包 |
| R04 后端 | WP2/WP5 | 迁移/发布闸门、数据库失败关闭和 API 路由契约 |
| R09 测试 | WP5 | Alembic、MySQL fencing 预演、发布门禁和脚本回归 |
| R10 运维 | OPS | Nginx、systemd、备份恢复和部署顺序契约 |

## 3. 本轮验证

```text
python -m pytest [Alembic/迁移闸门/发布门禁/MySQL fencing/Nginx/systemd 专项文件] -q --disable-warnings --tb=short
78 passed, 144 warnings in 15.40s
```

覆盖正式迁移链、去项目化 schema、执行租约字段/索引、空库升级、失败关闭、Nginx 健康路由和 Worker systemd 契约。

## 4. 失败关闭和性能边界

- 正式 Alembic 没有唯一 head、候选父链未知或 ORM/迁移字段不闭合时，部署闸门失败；
- 缺少备份恢复、`2/10/20/50` 并发和锁等待证据时不连接生产数据库；
- 健康/就绪/版本/OpenAPI 精确路径必须由后端返回 JSON，不能被 SPA fallback 接管；
- Worker systemd 单元、自动训练和边端 Worker 在真实运行环境未验证前保持关闭；
- 本地 SQLite/模拟 MySQL 预演只能证明门禁逻辑，不能证明生产锁等待、在线 DDL 或恢复时间。

## 5. 环境边界与验收等级

本轮达到 **L1**。未连接生产 MySQL、未执行正式迁移、未做备份恢复、未部署 Nginx/systemd；未提交、未推送、未部署。

