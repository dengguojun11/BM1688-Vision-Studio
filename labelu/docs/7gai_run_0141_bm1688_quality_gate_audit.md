# RUN-0141 BM1688 三精度与边端质量门禁审计

## 1. 本轮结论

按 R06 模型算法、R07 BM1688 边端、R09 测试职责核对 PT、静态 ONNX、FP32/FP16/INT8 BModel、冻结评估集和真机验证链路。本轮没有修改编译业务代码，原因是现有代码已经具备主要失败关闭能力，贸然改变诊断编译行为会影响生产兼容。

## 2. 已确认的本地规则

| 项目 | 本地结论 | 发布要求 |
|---|---|---|
| 制品数量 | Board Runner 和证据模型要求 FP32、FP16、INT8 三种制品；质量规则按 `bm1688-yolo11-detection-v1` 版本化 | 三种制品必须同一模型版本、同批生成并留存 |
| 编译顺序 | 训练输出 PT 后先导出静态 ONNX，再由编译节点生成 BModel；BM1688 设备只加载和推理 | 缺 ONNX 或制品签名不能进入真机验证 |
| 诊断编译 | `full`/`fp32_fp16` 档案要求三精度；`fp16`/`int8` 单模式可用于诊断或补历史制品 | 单精度诊断结果不能直接发布，发布门禁必须回到三精度完整证据 |
| 掉点规则 | 由版本化质量规则读取 PT 与各 BModel 的固定评估结果，不使用默认宽松阈值 | 阈值必须随规则版本、模型系列和评估集摘要一起保存 |
| 冻结评估集 | 证据结构支持 `fixed_eval_signature`，缺少签名或评估结果时失败关闭 | PT、三种 BModel 和真机必须使用同一冻结评估集；当前生产尚未证明这一点 |
| 真机执行通道 | Board Agent 和 SSH 都只负责设备执行/结果回传，不参与发布控制 | 主路径采用 Board Agent 主动拉取；SSH 只作为受控兼容验证通道 |
| 缺证据 | 缺制品、规则、评估签名、设备身份、有效推理数、精度、P95、错误率或短稳证据时不得发布 | 写入不完整状态，不把缺失指标写成 0 |

## 3. 为什么不能把单精度编译当发布

单精度编译允许用于定位工具链问题、补历史文件或做快速诊断，但它没有证明三个精度版本之间的转换一致性，也没有证明量化后的 INT8 在同一评估集上满足生产掉点要求。因此页面可以显示“诊断编译完成”，但模型中心的发布按钮必须继续被三精度、固定评估集和真机门禁拦截。

## 4. 本地验证

```text
python -m pytest labelu/tests/internal/common/test_bm1688_quality_rules.py labelu/tests/internal/common/test_bm1688_compile_contract.py -q
7 passed

python -m pytest labelu/tests/internal/application/service/test_auto_training_bm1688_edge_flow.py labelu/tests/internal/application/service/test_yolo_test_val_fencing.py labelu/tests/internal/common/test_edge_validation_evidence.py -q
22 passed
```

## 5. 生产边界

尚未连接真实 TPU-MLIR、编译节点、BM1688 设备或生产 Worker；没有真实三精度同批制品、冻结评估集签名、板端有效推理数/P95/短稳和 AICM 发布证据。以上本地测试只证明门禁逻辑，不能写成边端模型已经生产可用。

提交/推送/部署状态：未提交、未推送、未部署，等待用户明确“提交代码”。
