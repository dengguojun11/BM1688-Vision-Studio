# RUN-0168 BM1688 Profile 版本化契约与清单一致性

> 日期：2026-09-20  
> 责任角色：R03 架构、R06 模型算法、R07 BM1688 边端、R09 测试  
> 工作包：WP3（编译验证与发布）  
> 范围：本地代码和专项测试；未提交、未推送、未部署，未连接真实设备。

## 1. 本轮目标

将 BM1688 编译、制品清单和后续边端验证使用的模型契约统一为版本化 `BM1688Profile` 快照，至少固定：

- `profile_id`、Profile schema 版本、模型族、实际模型 stem；
- ONNX opset、静态 batch、输入尺寸、NCHW/RGB、归一化、letterbox 和坐标还原；
- 输出布局、输出节点/shape、后处理版本、框格式、置信度/NMS 和最大检测数；
- TPU-MLIR 版本/摘要、Docker 镜像摘要、运行时 ABI；
- INT8 校准集文件摘要、样本数和 qtable 文件名/摘要；
- 类别名称顺序和训练权重摘要（有真实文件摘要时记录）。

## 2. 实现内容

### 2.1 编译契约

`labelu/internal/common/bm1688_compile_contract.py` 新增：

- `build_bm1688_profile_snapshot()`：按实际模型 stem、输入尺寸、类别、校准目录和 qtable 生成快照；
- `profile_snapshot_digest()`：对去除摘要字段后的规范化 JSON 计算稳定 SHA256；
- `validate_bm1688_profile_snapshot()`：校验版本、BM1688 芯片、输入/ONNX、前后处理、输出和工具链结构；
- `evaluate_bm1688_compile_contract()` 支持可选 Profile。新链路传入 Profile 时，清单缺 Profile 或摘要不一致会阻断；历史调用没有 Profile 时仍保留旧兼容行为。

### 2.2 配置兼容

`labelu/internal/common/bm1688_compiler_config.py` 增加嵌套 `bm1688_profile` 默认配置，默认仍为既有 `yolov11n`、Sophon 导出和 BM1688 口径；旧 JSON/数据库配置没有该字段时自动使用默认值，不改变已有编译参数。

### 2.3 制品清单

`labelu/internal/common/model_asset_manifest.py` 增加 `bm1688_profile`、`profile_digest` 和编译器侧 Profile 摘要，并将 Profile 纳入执行签名。`yolo_bm1688_export._persist_artifact_manifest()` 按本次实际训练权重 stem 和导出尺寸传入快照，因此不再依赖默认 `yolov11n` 推断清单身份。

## 3. 本地验证

```text
python -m pytest labelu/tests/internal/common/test_bm1688_compile_contract.py labelu/tests/internal/common/test_model_asset_manifest.py -q
12 passed

python -m pytest labelu/tests/internal/application/service/test_bm1688_compile_flow.py labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py -q
8 passed

python -m compileall -q
通过
```

覆盖内容：默认 Profile 字段、校准/qtable 摘要、摘要稳定性、清单嵌入、清单缺 Profile 阻断、清单 Profile 篡改阻断，以及现有编译命令和边端业务流回归。

## 4. 明确边界

- 本轮没有执行真实 TPU-MLIR，不证明 ONNX 可被生产工具链编译。
- 本轮没有加载或运行真实 BM1688，不证明输出布局、前后处理、精度、P95、错误率或短稳。
- Profile 中的默认输出布局和后处理字段是一期 YOLO11 检测契约的工程占位，正式放量前必须由真实代表模型、TPU-MLIR 输出和 BM1688 真机证据确认。
- 三种 BModel 仍是同一模型版本的 FP32/FP16/INT8 制品，不拆成三个业务模型。
- 未修改生产数据库、AICM、S3、设备发布或回滚机制；未提交、未推送、未部署。

## 5. 后续依赖

1. 在真实编译节点生成真实工具链版本/摘要和 qtable 摘要。
2. 使用至少一个真实 YOLO11 代表模型生成静态 ONNX 和三种 BModel，核对 Profile 输出节点及 shape。
3. 在同一封存评估集和 BM1688 真机上完成加载、精度、P95、错误率和短稳证据。
4. 将同一 Profile 摘要传入 Board Agent/SSH 验证和 AICM 发布门禁。

