# RUN-0173 ModelManifest 签名与制品完整性基础契约

**日期**：2026-09-20  
**环境级别**：本地 L1（未提交、未推送、未部署）  
**对应工作包**：WP3 模型制品、边端验证与发布；WP5 测试验收  
**责任角色**：R03 架构、R06 模型算法、R07 BM1688 边端、R09 测试；R04 后端协作

## 1. 当前真实缺口

现有导出清单已经记录 PT、ONNX、BModel、校准集、类别契约、`execution_signature`、
`evidence_manifest_sha256` 和 `BM1688Profile`。这些 SHA256/摘要可以发现文件变化，但不是
服务器盖章：有写权限的调用方可以修改文件后重新计算摘要。当前 AICM 和板端入口尚未统一验证
一份受管签名的 ModelManifest。

## 2. 本轮落地内容

新增 `labelu/internal/common/model_manifest_signature.py`，提供可复用的基础契约：

- 规范化 ModelManifest 内容，排除签名字段和 AICM 发布后才产生的 `aicm_model_set_id`、
  `aicm_published_at` 字段。
- 生成带 `manifest_schema_version`、算法、`key_id` 和 HMAC-SHA256 的平台内部签名。
- 验签时校验密钥版本、签名、导出编号和执行摘要，失败返回稳定原因码。
- 逐文件重算清单中的字节数/SHA256，并要求清单显式声明且实际存在 FP32、FP16、INT8 三份 BModel；旧清单才允许按文件名兼容识别精度。

本轮已接入现有 YOLO 主链的服务端门禁：导出阶段在配置密钥时写入签名清单；AICM 发布列表和
实际发布前会校验签名、密钥版本、导出编号、执行摘要，并重新校验清单文件。未把 HMAC 密钥
下发到 BM1688；Board Agent/SSH 仍只消费边端验证摘要。`ModelVersion` 独立接口和设备端公钥
验签尚未接入，不能把两条制品链视为已经统一。

## 3. 安全边界

- HMAC 只能用于 LabelU 服务与受管 Worker 之间的内部验证；设备端不能持有可签名的共享密钥。
- BM1688 真机和外部下载链路后续应采用服务器私钥签名、设备/平台公钥验签的 Ed25519 方案，
  并把 `model_version_id`、`export_id`、`execution_signature`、`profile_digest`、固定评估集摘要
  和执行世代绑定，防止旧合法清单重放。
- AICM 返回的 `model_set_id` 是发布后关联字段，不能回写到已签名核心内容；平台目录和第三方
  回流使用该 ID 做血缘对账。

## 4. 本地验证

```text
python -m pytest \
  labelu/tests/internal/common/test_model_manifest_signature.py \
  labelu/tests/internal/common/test_model_asset_manifest.py \
  labelu/tests/internal/application/test_aicm_publish_sop_gate.py -q --disable-warnings --tb=short
37 passed
```

覆盖签名通过、AICM 发布字段回填不破坏签名、字段篡改、错误密钥、导出编号不匹配、磁盘文件
变化和三精度缺失。

## 5. 尚未完成与下一步

本轮没有配置生产密钥、生成真实生产 ModelManifest、执行真机验证或连接生产环境。下一轮必须
继续统一 `yolo_bm1688_export -> Board Agent/SSH -> yolo_aicm_publish` 的清单对象，再决定服务
内部 HMAC 和设备/外部平台 Ed25519 的密钥生命周期、轮换、
公钥分发和重放检查。生产 TPU-MLIR、BM1688、AICM、S3、MySQL 和 systemd 证据仍未完成。

当前等级仍为本地 L1；用户未明确“提交代码”前不提交、不推送、不部署。
