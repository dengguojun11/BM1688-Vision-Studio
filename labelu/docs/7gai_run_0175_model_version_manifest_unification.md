# RUN-0175 ModelVersion 制品链清单校验收口

**日期**：2026-09-20  
**环境级别**：本地 L1（未提交、未推送、未部署）  
**对应工作包**：WP3 模型仓库与发布；WP5 测试验收  
**责任角色**：R03 架构、R04 后端、R06 模型算法、R09 测试；R10 运维协作

## 1. 发现的问题

YOLO 导出主链已经使用 `artifact_manifest_json`，但 `ModelVersion` 资源还有独立的制品接口，
调用方可以直接提交相对路径和 SHA256。发布时原逻辑主要检查数据库行是否为 READY，不能证明磁盘
文件仍然是登记时的文件，也没有把清单绑定到具体模型版本。

## 2. 本轮改动

- `ModelVersion` 创建时由服务端补齐 `manifest_id=model-version:{id}` 和 `model_version_id`。
- 配置服务端 ModelManifest 密钥时，服务端为该模型版本清单签名；生产无密钥时写入明确的
  `MISSING_KEY` 状态，发布不会伪造通过。
- 生产发布前验证清单签名、密钥版本和模型版本 ID。
- 生产发布前重新解析所有 READY 制品路径，禁止路径越出媒体根目录，重新计算文件 SHA256 和大小。
- 保留 BM1688 的 FP32/FP16/INT8 三精度数据库门禁；不允许第二套 ModelVersion 接口绕过该规则。
- 非生产无签名历史数据继续保持读取兼容，避免破坏已有本地测试和历史记录。

## 3. 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_model_version_manifest_gate.py -q --disable-warnings --tb=short
2 passed

python -m pytest labelu/tests/internal/adapter/routers/test_model_version.py -q --disable-warnings --tb=short
6 passed
```

## 4. 生产边界和下一依赖

本轮没有配置生产密钥、连接生产数据库或发布真实模型，没有改变 AICM、BM1688 和第三方 S3
外部状态。仍需在同版本 MySQL 和真实媒体目录中验证清单签名密钥轮换、真实三精度文件、AICM
目录关联和下载控制。

生产 OpenAPI 只读复核同时显示：`/api/v1/model-versions`、`/api/v1/models` 和
`/api/v1/experiment-datasets` 当前未形成线上可验收路径；旧的 `/api/v1/yolo-bm1688-exports`、
S3 配置入口和已退休的 `edge-device-jobs` 路径仍存在。这说明代码主链虽已收口，生产部署和路由
分流仍是独立阻断项。

设备端仍不能持有 HMAC 签名密钥；BM1688/外部平台正式下载包需要接入 Ed25519 公钥验签和防重放。

当前保持本地 L1，用户未明确“提交代码”前不提交、不推送、不部署。
