# RUN-0187 实验数据集内部查询认证门禁

**日期**：2026-09-20（Asia/Shanghai）  
**环境级别**：本地 L1；未提交、未推送、未部署  
**责任角色**：R03 架构、R04 后端、R05 外部平台、R09 测试；R10 运维协作  
**对应工作包**：WP1 API、WP4 第三方回流、WP5 测试验收

## 1. 问题与代码路径

生产 OpenAPI/本地对账发现，`/internal/v1/experiment-datasets` 的列表、已创建任务和按包详情
三个 GET 接口没有显式挂载内部服务认证依赖，导致路由层 OpenAPI 没有 `APIKeyHeader` 安全声明。
测试环境默认关闭内部认证，因此原有回流业务测试没有暴露该缺口。

## 2. 修改内容

- `labelu/internal/adapter/routers/experiment_dataset_import.py`
  - 三个 GET 接口统一依赖 `verify_strategy_evolution_internal_request`；
  - 复用现有服务令牌/HMAC 校验，不新增第三套密钥；
  - POST 受理接口继续使用既有第三方签名协议，不改变回流入站契约。
- `labelu/tests/internal/common/test_internal_service_auth.py`
  - 增加三个 GET 路由缺少服务凭据时均返回 401 的测试；
  - 保留 OpenAPI `APIKeyHeader` 声明检查。

## 3. 权限与失败关闭

- 生产开启 `STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED` 时，缺少服务令牌/HMAC 直接 401；错误令牌不回退到 HMAC。
- HMAC 继续复用时间戳、随机数、重放和限流保护；不接受普通用户 JWT 作为内部服务身份。
- 测试/旧协议迁移关闭认证的行为只允许显式配置，不改变生产默认值。

## 4. 本地验证

```text
python -m pytest labelu/tests/internal/common/test_internal_service_auth.py labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py -q --disable-warnings --tb=short
28 passed

python -m pytest labelu/tests/internal/common/test_openapi_docs.py labelu/tests/internal/common/test_strategy_integration_s3_config.py -q --disable-warnings --tb=short
11 passed
```

## 5. 真实环境边界

本轮未连接生产、未发送 S3/AICM 请求、未创建回流任务、未写数据库；生产部署后仍需用机器服务令牌和
HMAC 各做一次查询、重放和错误凭据验收，并核对线上 `/openapi.json` 的三个 GET 均包含 `APIKeyHeader`。

**当前等级**：本地 L1；本轮未提交、未推送、未部署。

