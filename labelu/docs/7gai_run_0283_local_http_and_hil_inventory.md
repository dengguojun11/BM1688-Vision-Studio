# RUN-0283 本地 HTTP 路由验收与 BM1688/HIL 环境盘点

日期：2026-09-20（Asia/Shanghai）  
责任角色：R03 架构、R07 BM1688 边端、R08 前端/UI、R09 测试、R10 运维  
工作包：WP3、WP5、OPS

## 1. 本轮目的

验证当前工作树的健康探针、一期 OpenAPI 和退休边端入口是否在真实本地 HTTP 进程中生效，并盘点是否具备直接执行真实 `WP3-HIL-01` 的 TPU-MLIR/BM1688 环境。

## 2. 本地 HTTP 验收

启动方式：

```text
LABELU_DISABLE_BACKGROUND_WATCHDOGS=1
APP_ENV=test
LABELU_DEV_DATABASE_URL=sqlite:///./.tmp/labelu-dev.sqlite
python -m uvicorn labelu.main:app --host 127.0.0.1 --port 8002
```

只读命令：

```text
python scripts/staging_acceptance.py \
  --base-url http://127.0.0.1:8002 \
  --readonly-route-audit \
  --evidence .tmp/7gai-local-readonly-route-audit-current.json
```

结果：

- `passed=true`，所有探针通过。
- `/healthz`、`/readyz`、`/version` 返回 JSON `200`。
- `/openapi.json` 返回 JSON `200`，当前路径数量 `347`。
- `/api/v1/algorithms`、`datasets`、`model-versions`、`workspace/summary` 等一期业务路径存在并在未认证时返回 JSON `403`。
- `/api/v1/edge-device-jobs` 返回 JSON `404`，未进入一期 OpenAPI。
- `method_allowlist=["GET"]`，未认证、无请求体、未执行退休写探针。

证据文件：`.tmp/7gai-local-readonly-route-audit-current.json`。

## 3. BM1688/HIL 环境盘点

- 本机未发现 `bmrt_test`、`model_deploy`、`model_transform`、`model_tool` 或 `convert.py` 可执行命令。
- 未发现可证明真实 BM1688 板卡、TPU-MLIR 运行时或 Board Agent 在线的本机证据。
- 生产配置文件包含 BM1688/Board Agent 参数，但配置存在不等于工具链、板卡和真实执行证据存在。
- 因此本轮不能执行或宣称真实 `PT -> ONNX -> FP32/FP16/INT8 BModel -> BM1688` 验收。

## 4. 边界与风险

- 本地启动使用隔离 SQLite；启动过程自动建本地表并创建默认测试账号，未连接生产数据库。
- 未发送生产请求，未执行迁移、提交、推送或部署。
- 生产旧部署仍需在用户明确“提交代码”后，经过备份、迁移和部署，再重新执行同一只读验收。

## 5. 下一依赖

1. 获取一台真实 BM1688 和可复核的 TPU-MLIR/Sophon 工具链，执行 `WP3-HIL-01`。
2. 准备固定评估集、三精度同批编译产物、板端性能/短稳、租约/fencing 和 AICM/本地目录/S3 对账证据。
3. 在提交部署后用标注人员、管理人员两类账号执行页面/API 回放。

