# RUN-0177 固定评估集与三精度掉点契约收口

**日期**：2026-09-20  
**环境级别**：本地 L1（未提交、未推送、未部署）  
**对应工作包**：WP3 模型制品、边端验证与发布；WP5 测试验收  
**责任角色**：R06 模型算法、R07 BM1688 边端、R09 测试；R03 架构、R04 后端协作

## 1. 审计结论

- 现有一期规则已经固定 FP32 为边端基线，FP16/INT8 分别比较绝对 mAP50 掉点；默认起点为 FP16 `0.010`、INT8 `0.020`。这两个数值仍标记为“工程起点、待生产标定”，不能宣称已经通过真实生产模型验证。
- 原边端证据只有一个总体评估签名，不能证明三个精度使用了同一封存评估集；规则格式异常时也存在回退宽松默认值的风险。
- 本轮没有虚构 BM1688 真机、TPU-MLIR、生产掉点或性能结果；真实设备和生产标定仍是放行前置条件。

## 2. 本轮改动

### 2.1 规则快照

- `bm1688_quality_rules.py` 增加规则快照校验：版本、指标、单位、FP32 基线、FP16/INT8 掉点范围和转换阈值顺序必须合法；非法快照失败关闭，不再静默替换默认值。
- 默认规则增加 `threshold_status=engineering_default_pending_production_calibration`，明确 `0.005/0.010/0.020` 只是一期开工值。
- 导出清单的编译配置保存同一质量规则快照，Board Agent/SSH 期望值可复核实际使用的规则版本。

### 2.2 固定评估集

- 新版边端任务把评估包 `manifest_sha256` 写入 `fixed_eval_signature`，并设置 `require_fixed_eval_binding=true`。
- 旧 SSH 打包路径生成包含图片和标注文件 SHA256 的固定评估清单；边端运行前校验清单摘要和图片数，清单篡改、缺失或摘要不一致直接阻断。
- 边端报告记录固定评估集签名、图片数和绑定状态；证据解析会检查各精度报告声明的评估签名和图片数是否一致。

### 2.3 三精度掉点

- 质量门禁继续只对通用 YOLO 检测链要求 FP32、FP16、INT8；FP16 与 INT8 都相对 FP32 单独计算绝对 mAP50 掉点，避免坏的 FP16 被 INT8 或取最大值掩盖。
- 真机/报告具备质量规则快照时，服务端复核 FP16/INT8 掉点是否超过对应精度上限；车牌专用 FP16 路径仍使用独立契约，不强行套用通用 YOLO 三精度规则。

## 3. 本地验证

```text
python -m pytest labelu/tests/internal/common/test_bm1688_quality_rules.py labelu/tests/internal/common/test_edge_validation_evidence.py labelu/tests/scripts/test_edge_board_validate_runner_script.py -q --disable-warnings --tb=short
26 passed

python -m py_compile labelu/internal/common/bm1688_quality_rules.py labelu/internal/common/edge_validation_evidence.py labelu/internal/application/service/yolo_bm1688_export.py labelu/internal/application/service/yolo_edge_validation.py labelu/internal/application/service/edge_ssh_validation.py labelu/internal/application/service/board_agent.py labelu/scripts/edge_board_validate_runner.py
通过

git diff --check
通过（工作区其他文件已有换行提示，无新增空白错误）
```

## 4. 生产边界与下一步

- 尚未连接真实 TPU-MLIR、BM1688、生产 MySQL、AICM 或 S3；没有写生产数据、没有执行真机推理，也没有把本地测试当作线上通过。
- 生产放行前必须用代表性模型建立同一固定评估集的 PT/ONNX/FP32/FP16/INT8 和 BM1688 证据，记录每类关键召回、小目标/场景分层、mAP50 掉点、P95、错误率和短稳结果。
- 由 R06/R07 在真实生产样本上重复标定阈值并发布新的质量规则版本；旧任务继续使用其原快照，不允许就地修改。
- 用户未明确“提交代码”前，本轮保持本地 L1，不提交、不推送、不部署。

