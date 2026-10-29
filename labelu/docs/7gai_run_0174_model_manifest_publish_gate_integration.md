# RUN-0174 ModelManifest 接入导出与 AICM 发布门禁

**日期**：2026-09-20  
**环境级别**：本地 L1（未提交、未推送、未部署）  
**对应工作包**：WP3 模型制品、边端验证与发布；WP5 测试验收  
**责任角色**：R03 架构、R04 后端、R06 模型算法、R09 测试；R07 BM1688、R10 运维协作

## 1. 需求和代码路径核验

本地核对确认：

- `yolo_bm1688_export._persist_artifact_manifest` 是 YOLO 导出清单的唯一写入口。
- AICM 发布列表通过 `_publish_gate_reason_codes` 展示发布阻断原因。
- `publish_to_aicm` 是真正调用 AICM 上传前的硬门禁入口。
- `ModelVersion` 仍有另一套可自报 SHA 的制品接口，本轮没有让它绕过 YOLO 主链。

## 2. 本轮改动

### 2.1 导出阶段

- 新增 `YOLO_MODEL_MANIFEST_HMAC_SECRET`、`YOLO_MODEL_MANIFEST_HMAC_KEY_ID`、
  `YOLO_MODEL_MANIFEST_HMAC_REQUIRED` 配置。
- 导出清单增加 `manifest_schema_version` 和稳定 `manifest_id`。
- 配置服务端签名密钥时，导出清单写入 HMAC 签名和密钥版本。
- 生产环境没有签名密钥时，清单标记 `MISSING_KEY`，后续发布失败关闭，不伪造签名。

### 2.2 发布列表和实际发布

- 发布列表复用同一校验函数，能够展示签名缺失、密钥不匹配、导出编号不匹配、执行摘要不匹配和
  文件完整性失败。
- 真正调用 AICM 前再次验证签名，并逐文件重算清单中的字节数/SHA256。
- AICM 回写的 `model_set_id` 不参与签名核心内容，保持模型目录、第三方回流和签名清单的职责边界。
- 历史无签名清单在非生产兼容模式下仍可读取；生产环境强制失败关闭。

## 3. 安全边界

HMAC 仅用于平台服务和受管 Worker 的内部验证，不下发到 BM1688。设备端和外部平台正式验签仍需
服务器私钥签名、设备公钥验签的 Ed25519 方案，并增加模型版本、导出编号、执行摘要、Profile 摘要、
固定评估集和执行世代的防重放校验。

## 4. 本地验证

```text
python -m pytest labelu/tests/internal/common/test_model_asset_manifest.py \
  labelu/tests/internal/common/test_model_manifest_signature.py \
  labelu/tests/internal/application/test_aicm_publish_sop_gate.py -q --disable-warnings --tb=short
38 passed

python -m pytest labelu/tests/internal/adapter/routers/test_model_version.py \
  labelu/tests/internal/application/service/test_yolo_aicm_publish_helpers.py \
  labelu/tests/internal/application/service/test_published_model_catalog.py -q --disable-warnings --tb=short
125 passed

python -m pytest labelu/tests/internal/application/service/test_yolo_bm1688_export_auto_resume.py \
  labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py \
  labelu/tests/internal/adapter/routers/test_yolo_bm1688_exports.py -q --disable-warnings --tb=short
73 passed, 1 skipped

python -m pytest labelu/tests/e2e/test_yolo_model_publish_frontend.py -q --disable-warnings --tb=short
7 passed
```

## 5. 未完成和生产边界

本轮未配置生产密钥，未生成真实签名制品，未连接 AICM、TPU-MLIR、BM1688、S3、生产 MySQL 或
systemd。Board Agent/SSH 还没有验签 ModelManifest，`ModelVersion` 独立制品接口也尚未统一到该清单。
因此本轮只能证明本地主链门禁，不代表生产发布已经放行。

本轮生产只读探针（2026-09-20，未登录、未写入）：

- `/healthz`、`/readyz`、`/version`：HTTP 200，但返回非 JSON 的 SPA 内容。
- `/openapi.json`：HTTP 200，可解析 JSON。
- `/api-docs/openapi.json`：HTTP 200，但返回非 JSON 的 SPA 内容。
- OpenAPI 仍暴露 `/api/v1/edge-device-jobs`、`claim` 和 `result` 三条已退休设备任务路径。

这组线上事实阻断预生产放行，不能用本地测试结果替代；必须部署后重新执行同一组探针。

下一依赖：真实密钥注入和轮换演练 → 真实三精度清单 → BM1688 固定评估集/HIL → AICM `model_set_id`
对账 → Board Agent/SSH 公钥验签 → 生产只读/小流量验证。

当前保持本地 L1，用户未明确“提交代码”前不提交、不推送、不部署。
