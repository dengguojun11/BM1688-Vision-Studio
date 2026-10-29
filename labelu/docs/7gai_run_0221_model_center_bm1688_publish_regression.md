# RUN-0221 模型中心、BM1688 三精度与发布目录回归

## 1. 目标

验证模型中心一期主链：训练模型版本 -> PT/ONNX/BModel 制品血缘 -> FP32/FP16/INT8 编译契约 -> BM1688 边端证据 -> AICM `model_set_id` 发布 -> 本平台模型发布列表与下载禁用边界。

一期顺序固定为 PT 训练、导出静态 ONNX、服务器 TPU-MLIR 编译三种 BModel、BM1688 真机加载/推理；BM1688 不负责编译。问题模型不回滚已下载的外部文件，只由管理人员或授权发布人员禁止本平台后续下载并发布替代模型。

## 2. 角色与覆盖范围

| 角色 | 工作包 | 验收点 |
|---|---|---|
| R03 架构 | WP2/WP3 | 模型版本、DatasetVersion、制品、质量证据和发布记录血缘不可断裂 |
| R04 后端 | WP3/WP4 | 模型版本权限、发布幂等、下载开关和 AICM ID 对账 |
| R05 外部平台 | WP4 | AICM 发布请求、`model_set_id` 稳定保存和错误重试契约 |
| R06 模型算法 | WP3 | 三精度同一模型版本、固定评估集和质量门禁 |
| R07 BM1688 边端 | WP3 | 清单签名、制品完整性、设备加载、推理证据和防重放 |
| R08 前端/UI | WP4 | 模型版本、编译、真机验证、发布记录、下载目录按钮状态 |
| R09 测试 | WP5 | 路由、服务、发布、清单、质量和边端联合回归 |
| R10 运维 | OPS | 真机、TPU-MLIR、密钥、Worker 和生产部署证据 |

## 3. 本轮验证

```text
python -m pytest \
  labelu/tests/internal/adapter/routers/test_model_version.py \
  labelu/tests/internal/adapter/routers/test_published_model_catalog.py \
  labelu/tests/internal/adapter/routers/test_yolo_bm1688_exports.py \
  labelu/tests/internal/adapter/routers/test_yolo_edge_validation.py \
  labelu/tests/internal/application/service/test_model_version_manifest_gate.py \
  labelu/tests/internal/application/service/test_model_version_no_project.py \
  labelu/tests/internal/application/service/test_published_model_catalog.py \
  labelu/tests/internal/application/service/test_yolo_aicm_publish_claim.py \
  labelu/tests/internal/application/service/test_yolo_aicm_publish_helpers.py \
  labelu/tests/internal/application/service/test_yolo_bm1688_auto_compile.py \
  labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py \
  labelu/tests/internal/application/service/test_yolo_edge_validation_pack.py \
  labelu/tests/internal/application/service/test_yolo_edge_validation_plate_detection_bundle.py \
  labelu/tests/internal/common/test_aicm_publish_config.py \
  labelu/tests/internal/common/test_aicm_publish_job.py \
  labelu/tests/internal/common/test_bm1688_compile_contract.py \
  labelu/tests/internal/common/test_bm1688_quality_rules.py \
  labelu/tests/internal/common/test_edge_validation_evidence.py \
  labelu/tests/internal/common/test_model_asset_manifest.py \
  labelu/tests/internal/common/test_model_manifest_signature.py \
  labelu/tests/internal/common/test_phase_one_algorithm_gate.py \
  -q --disable-warnings --tb=short

283 passed, 1 skipped, 128 warnings in 13.85s
```

覆盖：

- 模型版本无项目维度和血缘关联；
- PT/ONNX/BModel 清单完整性、签名和三精度同版本约束；
- BM1688 编译参数、校准集、质量规则和边端验证证据；
- AICM 发布声明、`model_set_id` 和本平台发布目录；
- 发布前门禁、下载禁用、发布幂等和旧执行者 fencing；
- 车牌检测普通矩形框边端包。

## 4. 失败关闭与并发

- 缺少任一 BModel、清单签名、固定评估集或真机证据时禁止发布；
- 发布请求按模型版本和 AICM ID 幂等，重复提交不生成第二个业务模型；
- 旧租约不能覆盖新编译、边端验证或发布状态；
- 下载禁用只影响本平台后续下载，不宣称撤回外部平台已下载文件；
- Board Agent/SSH runner 只验签和推理，不在 BM1688 板端编译。

## 5. 环境边界与验收等级

本轮仅本地测试和模拟外部接口，未连接生产 AICM、TPU-MLIR、BM1688、S3 或生产 MySQL，未提交、未推送、未部署。达到 **L1**；真实三精度编译、BM1688 加载/P95/短稳、AICM 对账和本平台下载仍需 L2/L3/L4 证据。

