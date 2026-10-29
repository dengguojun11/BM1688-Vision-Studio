# RUN-0288 生产只读路由复核

日期：2026-09-20（Asia/Shanghai）  
责任角色：R03 架构、R04 后端、R09 测试、R10 运维  
工作包：WP0/WP1/WP5/OPS

## 1. 执行命令

```text
python scripts/staging_acceptance.py \
  --base-url http://113.141.72.175:10000 \
  --readonly-route-audit \
  --timeout 10 \
  --evidence .tmp/7gai-production-readonly-route-audit-0288.json
```

该模式只发送 GET，不登录、不带请求体、不调用退休写接口。

## 2. 结果

证据：`.tmp/7gai-production-readonly-route-audit-0288.json`。

| 检查 | 结果 | 说明 |
|---|---|---|
| `/healthz`、`/readyz`、`/version` | FAIL | HTTP 200 但返回 SPA HTML，不是 JSON 健康/版本响应 |
| `/openapi.json` | PASS（可访问） | 文档可读，但契约对账失败 |
| `/api/v1/algorithms` | 404 | 一期算法路由缺失 |
| `/api/v1/datasets` | 404 | 一期数据集路由缺失 |
| `/api/v1/model-versions` | 404 | 一期模型版本路由缺失 |
| `/api/v1/workspace/summary` | 404 | 一期工作台路由缺失 |
| 受保护业务 GET | 401 | 未认证，符合只读探针边界 |
| `/api/v1/edge-device-jobs` | GET 404；OpenAPI 仍登记相关路径 | 旧边端路径仍暴露于契约，需部署后清理 |
| 总体 | FAIL | OpenAPI 缺失四个一期路径并暴露退休边端路径 |

## 3. 生产阻断

- 线上当前不是工作树对应版本，不能进行两类账号业务验收。
- 不能以 HTTP 200 健康页面、未认证 401 或单个业务 GET 通过推断一期已上线。
- 本轮未执行登录、写入、数据库迁移、S3 操作、AICM 操作、SSH 或设备操作。

## 4. 下一依赖

用户明确“提交代码”后，按备份、同版本 MySQL 迁移闸门、部署和线上只读复核顺序推进；部署前保持训练、编译、边端和 S3 Worker 的生产开关失败关闭。

