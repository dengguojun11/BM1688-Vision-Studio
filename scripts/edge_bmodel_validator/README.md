# BM1688 BModel 通用边端验证程序

这个目录可以整体复制到 BM1688 边端设备，同时支持普通 YOLO 检测、车牌检测单模型和完整车牌识别。日常使用时只需更换模型目录和图片，然后执行一次命令；不依赖 LabelU 页面、数据库或网络下载。

## 每次验证

1. 清空 `model/`，按下面的模型类型放入模型文件。
2. 清空 `images/`，放入本次验证图片。
3. 在当前目录执行：

```bash
bash run.sh
```

程序会自动识别模型类型，不需要选择运行模式：

- 普通 YOLO 检测：`model/` 中放入且只放入一个 `.bmodel`。
- 车牌检测单模型：`model/` 中同时放入 `plate_detection_fp16_1b.bmodel` 和本次导出配套的 `plate_detection_contract.json`。
- 复合车牌识别：`model/` 中同时放入 `plate_recognition_fp16_1b.bmodel` 和本次导出配套的 `plate_recognition_contract.json`。

车牌检测契约包含网络名、输入尺寸、类别和四角点顺序；完整识别契约还包含 OCR 字符集、车身颜色、车牌类型和 OCR 裁剪策略。契约必须与 BModel 成对更换，不能沿用其他模型的契约。

结果统一写入 `output/current/`：

- `images/`：按检测 JSON 重新绘制的带框图片。
- `detections.json`：包含类别名称的标准化检测结果。
- `raw_detections.json`：Sophon Demo 原始检测结果。
- `summary.json`：运行状态、图片数、检出图片数、检测框总数、耗时和类别统计。
- `bmrt_test.log`：BModel 加载和理论性能日志。
- `inference.log`：真实图片推理日志。

`summary.json` 中 `model_type` 会标记 `yolo_detection`、`plate_detection` 或 `plate_recognition`；`runtime_ok=true` 表示模型在 BM1688 上成功完成推理；`objects_detected=true` 表示本批图片至少检出了一个目标。这些状态不能混为一谈。

## 类别名称

`classes.txt` 只用于普通 YOLO 模型，是可选文件。如果需要在结果中显示业务类别名，按训练时类别顺序每行填写一个名称：

```text
person
car
helmet
```

不填写时使用 `class_0`、`class_1` 等类别编号，不影响模型推理和检测框输出。两种车牌模型的业务信息直接读取配套契约。OpenCV 无法稳定绘制中文文字时，带框图片可能无法完整显示中文，但 `detections.json` 和 `summary.json` 仍保留原始中文结果。

## 边端环境要求

- BM1688 设备驱动和 libsophon 已正常安装。
- `bmrt_test` 可用；缺失时程序会跳过理论性能测试，仍尝试真实图片推理。
- 普通 YOLO 检测需要已安装 Sophon Demo 的 `sample/YOLOv8_plus_det`，并且 Python 环境能运行 `python/yolov8_bmcv.py`。
- 两种车牌模型都不依赖 Sophon Demo，使用包内的专用推理程序。
- 两种类型通常都需要设备已有的 `cv2` 和 Sophon BMCV 运行库。

程序会按以下顺序自动查找 Sophon Demo：

```text
$SOPHON_DEMO_ROOT
/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det
```

如果安装在其他位置，只需在首次使用前设置：

```bash
export SOPHON_DEMO_ROOT=/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det
bash run.sh
```

## 可选参数

正常使用不需要参数。临时调整阈值时可以执行：

```bash
bash run.sh --conf 0.35 --nms 0.70
```

车牌模型临时调整定位阈值：

```bash
bash run.sh --conf 0.35 --iou 0.50
```

查看全部高级参数：

```bash
bash run.sh --help
```
