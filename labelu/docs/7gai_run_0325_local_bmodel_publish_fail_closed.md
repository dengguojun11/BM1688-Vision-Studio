# RUN-0325 本地 BModel 编译与发布门禁失败关闭验证（2026-09-21）

## 1. 本轮目标

使用 RUN-0324 生成的真实 YOLO11 `best.pt` 和静态 `best.onnx`，执行平台 BM1688 编译契约检查，验证没有 FP32、FP16、INT8 BModel、校准集或固定评估集时，系统会阻断发布，而不会把 PT/ONNX 半成品当成可发布模型。

## 2. 责任角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP3 | 复用 `evaluate_bm1688_compile_contract` 检查实际制品、Profile 和编译输入契约 |
| R06 模型算法 | WP3 | 确认同一 PT/ONNX 输入的三精度 BModel 是发布前置条件 |
| R07 BM1688 边端 | WP3 | 确认本地无 BModel 时不允许进入边端发布链 |
| R05 发布集成 | WP3 | 确认编译契约阻断会阻止 AICM 发布前进 |
| R09 测试 | WP5 | 保存阻断原因和通过项，避免只看 ONNX 成功就误判整体成功 |

## 3. 使用的真实制品

- PT：`runs/detect/.tmp/real-yolo11-acceptance/yolo11n-epoch1-absolute-data/weights/best.pt`
- ONNX：`runs/detect/.tmp/real-yolo11-acceptance/yolo11n-epoch1-absolute-data/weights/best.onnx`
- ONNX：opset `17`、静态输入、batch `1`、Sophon 导出契约
- 检查模式：`full`
- 目标平台：`bm1688`

## 4. 门禁结果

```json
{
  "passed": false,
  "status": "阻断",
  "reasons": [
    "profile:profile_missing_in_manifest",
    "bmodel_missing:bm1688:fp32,fp16,int8",
    "calibration_set_missing",
    "fixed_eval_missing"
  ],
  "onnx_contract": {
    "backend": "sophon",
    "opset": 17,
    "dynamic": false,
    "batch": 1,
    "passed": true
  }
}
```

结论：ONNX 输入契约通过，但三种 BModel、校准集、固定评估集和清单 Profile 证据缺失，整体必须失败关闭，不能生成可发布资格。

关联发布门禁回归：`python -m pytest labelu/tests/internal/application/test_aicm_publish_sop_gate.py labelu/tests/internal/common/test_model_asset_manifest.py -q --disable-warnings --tb=short`，结果为 `33 passed, 114 warnings`。

## 5. 风险与下一依赖

- 本轮未创建数据库导出记录、未调用 AICM、未上传 S3、未触碰生产。
- 该结果只证明发布门禁能阻断不完整制品，不代表真实 TPU-MLIR 编译或 BM1688 推理成功。
- 下一依赖是提供锁定版本 TPU-MLIR 编译节点、独立 INT8 校准集、冻结评估集以及 BM1688 真机；完成后再执行三精度制品和边端证据对账。

## 6. 提交与部署状态

本轮未提交、未推送、未部署、未重启生产。继续等待用户明确说“提交代码”。
