# RUN-0169：预发布 JSON 探针与一期禁用写入口门禁

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R03 架构、R04 后端、R09 测试、R10 运维；协同 WP0/WP5/OPS。
- 范围：健康探针、规范 OpenAPI、`edge-device-jobs` 兼容地址和预发布证据校验。
- 环境边界：仅本地工作树和测试夹具；未连接生产、预生产、MySQL、设备、S3 或 AICM；未提交、未推送、未部署。

## 本轮问题

此前 `scripts/staging_acceptance.py` 只判断 HTTP 状态码。服务入口被前端单页应用回退时，
`/healthz`、`/readyz`、`/version` 或 `/openapi.json` 可能返回 HTTP 200 的 HTML，验收会误判
为通过。另一期已退休的 `POST /api/v1/edge-device-jobs` 虽然已经隐藏于 OpenAPI 且服务层
有退休门禁，预发布脚本没有实际验证写请求被拒绝。

## 本轮改动

1. `scripts/ops/7gai_openapi_contract.json` 增加 `retired_write_probes`，以共享契约声明
   一个 schema 合法、无副作用的边端作业写入探针，预期状态为 403 或 404。
2. `scripts/staging_acceptance.py` 为探针证据记录 `response_kind`；健康、版本、OpenAPI
   和退休写入口必须解析成 JSON。即使状态为 200，只要响应是文本/HTML，也会失败并明确
   提示不能用 SPA HTML 放行。
3. 登录后的验收调用退休写入口，要求其在任何业务写入前被拒绝；请求体满足接口模型，避免
   把 422 参数错误误当成退休门禁。
4. `scripts/verify_staging_and_hil.py` 的发布证据校验要求包含退休写探针，并要求所有
   JSON 探针记录 `response_kind=json`。缺少探针、响应类型或 required 失败时，证据不能放行。
5. 增加 SPA HTML、退休写入口和证据缺失/非 JSON 响应的聚焦测试。

## 本地验证

```text
python -m pytest labelu/tests/scripts/test_staging_acceptance.py scripts/tests/test_verify_staging_and_hil.py -q --disable-warnings --tb=short
23 passed
```

## 生产只读阻断证据（上游本轮观测）

以下是主任务本轮提供的生产入口只读观测，记录为阻断证据，不作为本地代码通过或生产修复
证明；未在本轮重复连接生产，也未执行任何写请求：

- `healthz`：HTTP 200，但 `Content-Type: text/html`，实际为前端单页 HTML；不能判定存活探针通过。
- `readyz`：HTTP 200，但 `Content-Type: text/html`，实际为前端单页 HTML；不能判定就绪探针通过。
- `version`：HTTP 200，但 `Content-Type: text/html`，实际为前端单页 HTML；不能判定版本接口通过。
- `/openapi.json`：HTTP 200，返回 JSON；只能证明该规范入口当前可解析，不能替代三个运维探针。
- `/api/v1/edge-device-jobs`：生产 OpenAPI/路由仍可见，说明一期退休写入口隔离尚未在线生效。

因此当前生产不能放行。部署本轮代码后，必须重新保存四个入口的状态码、响应头、响应首字符、
版本号、OpenAPI 退休路径结果，以及带管理员令牌的退休写探针结果；在 `healthz/readyz/version`
仍返回 HTML 或 `edge-device-jobs` 写入未被 403/404 拒绝前，不得启用一期放量。

## 验收解释

- `response_kind=json` 只证明脚本成功解析了 JSON；上线验收仍需同时保存 HTTP 状态、
  `Content-Type`、响应首字符、版本号和时间戳。任何真实入口返回 SPA HTML 都必须阻断放量。
- `edge-device-jobs` 是兼容地址，不是一期菜单或主流程。保留历史只读兼容不等于允许写入；
  该探针只验证退休门禁拒绝请求，不创建任务、不连接设备、不改变数据。
- 404 表示入口已完全移除，403 表示保留兼容地址但被一期能力门禁拒绝；两者都不能进入业务
  写链。若返回 200、201、202、204、422 或其他状态，验收失败。

## 遗留和下一依赖

- 尚未在真实预生产/生产执行脚本；需用户明确“提交代码”后，按部署流程发布，再对实际入口
  保存 JSON 证据并核对 Nginx/API 分流。
- 尚未把真实 BM1688、TPU-MLIR、训练 Worker、S3/AICM 证据写成本轮通过；本轮等级为本地 L1。
