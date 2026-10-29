# RUN-0269 只读验收探针与证据校验器契约同步（2026-09-20）

## 发现

RUN-0262 将退休边端写接口探针改为默认跳过，但 `scripts/verify_staging_and_hil.py` 仍按旧证据名称要求 HTTP 状态码和 JSON 响应。新脚本生成的只读证据会因此被后续发布闸门误判失败，形成验收链断裂。

## 修改

- `validate_probe_evidence()` 同时支持：
  - 默认安全记录：`退休写接口探针`、`passed=true`、`required=false`、`status=null`；
  - 一次性预生产危险夹具兼容记录：旧名称 `一期已禁用边端作业写入`，必须实际返回预期状态并为 JSON。
- 其余健康、版本、OpenAPI 和匿名鉴权探针仍必须为 JSON/HTTP 通过。
- 默认证据必须包含退休写接口的“跳过记录”，不能静默缺失。

## 验证

```text
python -m pytest labelu/tests/scripts/test_staging_acceptance.py scripts/tests/test_verify_staging_and_hil.py -q --disable-warnings --tb=short
27 passed
```

## 状态

- 本地：验收脚本和证据校验器契约一致，达到 L1。
- 生产：未重新请求生产，旧版本写接口事件仍待运维核查。
- Git/部署：未提交、未推送、未部署，等待用户明确“提交代码”。

