# 7GAI 本地 Provider 联调

`scripts/ops/provider_http_simulator.py` 是一个仅用于开发和验收的 HTTP
模型服务模拟器。它不执行真实推理，不读取凭据，也不会注册到生产 Provider
目录；用途是验证 `HttpProviderAdapter` 与 Provider Worker 的网络和数据契约。

启动模拟器：

```powershell
python scripts/ops/provider_http_simulator.py --host 127.0.0.1 --port 18190
```

服务提供：

- `GET /health`：健康检查。
- `POST /` 或 `POST /predict`：接收 `image_url`、`labels`、`constraints` 和 `prompt`，返回一个确定性的检测结果。

自动化验证：

```powershell
python -m pytest labelu/tests/internal/application/service/test_provider_http_simulator.py -q
```

管理员也可以在本地平台调用运行时控制面：

- `POST /api/v1/providers/{provider_key}/runtime-check`：探测已显式注册的
  Adapter 健康/就绪状态，并保存 `runtime_evidence` 快照。
- `POST /api/v1/providers/{provider_key}/local-hil`：通过真实本地 HTTP
  Adapter 执行一次无副作用预测合同探针，保存带 SHA-256 的 `local_hil` 报告。

这两个接口都要求平台管理员。`local-hil` 不创建 ProviderJob、PredictionSet，
也不会将 `hil_verified` 或 `production_ready` 置为通过；真实 GPU/芯片 HIL 仍须
按 staging/HIL 验收手册提供外部证据。

测试覆盖真实本地 TCP 请求、响应 JSON、结果载荷 SHA-256、Provider Worker
队列执行、`PredictionSet` 持久化和输入资产 lineage。模拟器通过随机端口运行，
测试结束后自动关闭，不会触碰生产服务或数据库。
