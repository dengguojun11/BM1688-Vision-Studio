# RUN-0260 生产只读探针复核（2026-09-20）

## 目的与边界

重新执行仓库内 `scripts/staging_acceptance.py`，确认生产是否已经加载当前工作树。仅访问公开健康探针、文档和 OpenAPI；没有提供账号，不登录、不创建任务、不训练、不编译、不发布、不下载、不修改生产数据。

## 命令

```text
python scripts/staging_acceptance.py --base-url http://113.141.72.175:10000 --evidence .tmp/production-readonly-acceptance-20260920-r3.json
```

命令退出码：`1`。

## 结果

| 检查项 | 结果 | 判断 |
|---|---|---|
| `/healthz` | `200`，返回文本 | 失败，疑似被前端页面接管 |
| `/readyz` | `200`，返回文本 | 失败，不能证明后端就绪 |
| `/version` | `200`，返回文本 | 失败，不能确认版本 |
| `/docs` | `200` | 文档页可访问 |
| `/openapi.json` | `200` | 仍可访问旧 OpenAPI |
| 一期 API | `algorithms`、`datasets`、`model-versions`、`workspace/summary` 缺失 | 失败，当前部署不是一期工作树 |
| 退休边端写入口 | `edge-device-jobs`、`claim`、`result` 仍暴露 | 失败，旧入口未下线 |
| 未授权任务接口 | `/api/v1/tasks` 返回 `401` | 访问控制边界正常 |
| 登录 | 未执行 | 未提供账号和密码 |

## 结论

生产仍为旧部署，不能把当前本地 L1 代码、测试或构建结果写成生产完成。需要在用户明确“提交代码”后，经过备份、迁移闸门、部署和两类账号回放，重新执行同一探针并记录 JSON 证据。

## 状态

- 生产：只读复核失败关闭。
- 数据库：未连接、未迁移、未写入。
- Git：未提交、未推送。
- 下一依赖：部署权限、备份证据、同版本 MySQL 沙箱、两类生产账号。
