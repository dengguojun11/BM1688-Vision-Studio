# RUN 0255 生产预发布验收探针

## 目标

使用仓库内的 `scripts/staging_acceptance.py` 对生产入口执行匿名只读验收，核对健康探针、OpenAPI 一期路径、退休边端写入口和未授权访问边界。

## 执行边界

- 责任角色：R03 架构、R09 测试、R10 运维。
- 工作包：WP2、WP5、OPS。
- 未提供密码；不登录、不创建任务、不训练、不编译、不发布、不下载、不修改生产数据。

## 命令与证据

```text
python scripts/staging_acceptance.py --base-url http://113.141.72.175:10000 --evidence .tmp/production-readonly-acceptance-20260920.json
```

本地证据文件：`.tmp/production-readonly-acceptance-20260920.json`。

本地契约回归：`python -m pytest labelu/tests/internal/common/test_7gai_role_execution_playbook.py labelu/tests/scripts/test_staging_acceptance.py -q --disable-warnings --tb=short`，结果为 `12 passed, 113 warnings`。

## 真实结果

| 探针 | 结果 | 结论 |
|---|---|---|
| `/healthz` | `200`，但返回文本 | 失败：被 SPA HTML 接管 |
| `/readyz` | `200`，但返回文本 | 失败：被 SPA HTML 接管 |
| `/version` | `200`，但返回文本 | 失败：被 SPA HTML 接管 |
| `/docs` | `200` | 文档页可访问 |
| `/openapi.json` | `200` | JSON 可访问，但路径集合为旧部署 |
| 一期路径对账 | 缺少 `/api/v1/algorithms`、`/api/v1/datasets`、`/api/v1/model-versions`、`/api/v1/workspace/summary` | 失败 |
| 退休边端写入口 | `/api/v1/edge-device-jobs`、`claim`、`result` 仍暴露 | 失败 |
| 未授权 `/api/v1/tasks` | `401` | 访问控制边界正常 |
| 登录 | 未执行 | 未提供密码 |

## 结论

生产验收失败关闭，线上仍未加载当前工作树。HTTP 200 不能替代 JSON 探针和当前 OpenAPI 路径证据；未执行任何生产写入。

## 下一依赖

提交授权后，先完成备份、同版本 MySQL 迁移、Nginx/API 精确路由部署，再使用两类真实账号重复执行本探针和任务、训练、模型、S3、AICM、BM1688 业务回放。

## 状态

生产只读 L2；未提交、未推送、未部署。
