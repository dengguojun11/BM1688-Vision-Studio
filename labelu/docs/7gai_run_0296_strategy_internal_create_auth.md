# RUN-0296 策略演化创建接口服务鉴权

## 1. 目标与入口

- 受保护入口：策略演化内部接口的 `创建任务`、`创建阈值实验`、`创建模型训练`。
- 这些接口不是标注人员或管理人员的浏览器按钮，而是第三方策略平台到本平台的内部服务调用。
- 主责：R04 后端、R05 外部平台、R09 测试；协作 R03 架构、R10 运维。

## 2. 本轮交付

三个 POST 路由统一复用已有 `verify_strategy_evolution_internal_request`：

- `POST /internal/v1/strategy-evolution/tasks`
- `POST /internal/v1/strategy-evolution/threshold-experiments`
- `POST /internal/v1/strategy-evolution/model-trainings`

生产开启 `STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED` 时，调用方必须提供配置的服务令牌或策略演化 HMAC 签名；浏览器 JWT 不作为内部服务凭据。测试和旧协议迁移仍可显式关闭开关。

## 3. 幂等、并发与失败关闭

- 鉴权依赖在业务服务执行前运行，缺少凭据直接 401，不创建任务、不写回流记录。
- 有效凭据只通过认证，不改变原有任务类型校验、幂等和状态机。
- 服务令牌比较使用恒定时间比较；HMAC 路径继续复用时间窗、nonce、防重放和限流。
- 不新增数据库表和迁移，不增加业务并发；失败请求不会进入训练或回流队列。

## 4. 修改范围

- `labelu/internal/adapter/routers/strategy_evolution.py`
- `labelu/tests/internal/common/test_internal_service_auth.py`

## 5. 验证证据

- `python -m pytest labelu/tests/internal/common/test_internal_service_auth.py -q --disable-warnings --tb=short`：7/7 通过。
- `python -m pytest labelu/tests/internal/adapter/routers/test_strategy_evolution.py -q --disable-warnings --tb=short`：35/35 通过。
- 覆盖三条创建接口缺少服务令牌均 401；有效令牌进入原有业务校验；OpenAPI 暴露统一服务认证声明。

## 6. 环境边界与验收等级

- 本地：L1，SQLite/测试客户端和内部路由回归通过。
- 预生产：未完成，未使用真实第三方服务令牌/HMAC 客户端。
- 生产：未连接、未登录、未写入、未迁移、未提交、未推送、未部署；真实密钥轮换、限流和监控待执行。

## 7. 下一依赖

1. 预生产配置独立服务令牌和 HMAC 密钥，回放三类创建调用及重放攻击。
2. 与 S3 回流 Worker、AICM 发布 Worker 的服务身份统一审计。
3. 用户明确“提交代码”前保持未提交、未推送、未部署。

