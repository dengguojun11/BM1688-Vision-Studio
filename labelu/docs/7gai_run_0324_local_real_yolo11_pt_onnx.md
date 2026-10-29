# RUN-0324 本地真实 YOLO11 训练与 PT→ONNX 验证（2026-09-21）

## 1. 本轮目标

在本地 CUDA 环境用真实 `yolo11n.pt` 完成一次最小训练和静态 ONNX 导出，验证平台训练器使用真实 YOLO11 运行时，而不是模拟器或仅写入数据库的假作业。

本轮只使用工作区样例数据和本地 `.tmp` 输出，不调用生产 API，不连接 AICM、S3、TPU-MLIR 或 BM1688。

## 2. 责任角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R06 模型算法 | WP1/WP3 | 使用 YOLO11n 基础权重完成真实检测训练并导出 ONNX |
| R03 架构 | WP2/WP3 | 核对 PT、ONNX、输入尺寸、opset 和输出形状的一致性 |
| R07 BM1688 边端 | WP3 | 确认 ONNX 是后续 TPU-MLIR 编译输入；本轮不宣称 BModel |
| R09 测试 | WP5 | 执行训练完成、ONNX 结构和 ONNX Runtime 加载验证 |
| R10 运维 | OPS | 确认训练只使用本机 CUDA 和本地数据目录 |

## 3. 训练环境与数据

- 权重：`yolo11n.pt`
- Ultralytics：`8.4.48`
- Python：`3.11.0`
- PyTorch：`2.7.0+cu126`
- GPU：`NVIDIA GeForce RTX 4060 Laptop GPU`，显存约 `8188MiB`
- 训练参数：`epochs=1`、`imgsz=320`、`batch=2`、`device=0`、`workers=0`、`amp=false`
- 数据：本地电动车/安全帽样例，`19` 张图片、`19` 个标签文件、`115` 个实例、`5` 个类别
- 输出目录：`runs/detect/.tmp/real-yolo11-acceptance/yolo11n-epoch1-absolute-data/weights/`

关闭 AMP 是因为本机网络无法访问 Ultralytics AMP 自检所需的 GitHub 资源；这不改变训练模型或数据，只跳过自动混合精度自检。

## 4. 结果证据

训练成功输出：

- `best.pt`：`5,421,402` 字节
- `last.pt`：训练完成后同时生成
- 验证集：`19` 张图片、`115` 个实例
- 本轮 `mAP50=0.000577`、`mAP50-95=0.000219`
- 该指标仅证明训练链路跑通，不代表业务质量达标

静态 ONNX 导出成功：

- 文件：`best.onnx`
- 大小：`10,442,097` 字节
- opset：`17`
- 输入：`images`，`[1, 3, 320, 320]`，`float32`
- 输出：`output0`，`[1, 9, 2100]`，`float32`
- PT SHA256：`00916e23e49b740f1e36841294c9272b30d335f5240011ec7e3d2607cec8ece2`
- ONNX SHA256：`2ba89daf5d49d0cb46c13d0772fbedbbd75fcc09c7750a1925da1d72c15cf52a`

ONNX 通过 `onnx.checker.check_model`，并由 ONNX Runtime `CPUExecutionProvider` 成功加载，输入输出签名与导出结果一致。

平台代码路径复核确认：`yolo_dataset._write_dataset_yaml_files()` 会把本次训练目录的绝对路径写入 `data.yaml`；`yolo_train.py` 也会在训练前关闭 Ultralytics 在线 AMP 自检。因此本轮外部样例的相对路径问题不会复制到平台正式训练主链。相关回归：`25 passed, 114 warnings`。

本轮随后补充了 `test_write_dataset_yaml_only_data_yaml` 的绝对路径断言，复跑同一专项后仍为 `25 passed, 114 warnings`。

## 5. 未完成与放行边界

- 本机工具链探针：`tpu_mlir`、`model_transform.py`、`model_deploy.py`、`model_tool`、`bmrt_test` 均不存在。
- 未执行 TPU-MLIR 编译，未生成 FP32、FP16、INT8 BModel。
- 未在 BM1688 上加载、推理、测 P95、短稳、断网或重启接管。
- 未把本地小样本指标写入模型中心、AICM 或生产数据库。
- 真实生产代表模型、冻结评估集、校准集和掉点阈值仍需 R06/R07/R10 在受控节点完成。

## 6. 提交与部署状态

本轮只产生本地训练/导出证据和文档，未提交、未推送、未部署、未重启生产。继续等待用户明确说“提交代码”。
