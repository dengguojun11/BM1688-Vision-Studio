# Sophon YOLOv8_plus_det 资源



与 [YOLOv8_plus_det README](https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/README.md) §3.1–3.2 的 `models/` 布局一致。LabelU **full** 模式在每个已选平台目录下仅生成：



- `yolov11s_fp32_1b.bmodel`

- `yolov11s_fp16_1b.bmodel`

- `yolov11s_int8_1b.bmodel`



导出包目录结构示例：



```text

models/

├── onnx/

│   ├── yolo11s.onnx

│   └── yolov11s_qtable

├── BM1688/

│   ├── yolov11s_fp32_1b.bmodel

│   ├── yolov11s_fp16_1b.bmodel

│   └── yolov11s_int8_1b.bmodel

├── BM1684X/   # 若 compile_platforms 含 bm1684x

└── CV186X/    # 若含 cv186x

```



在 sophon-demo 例程目录准备资源：



```bash

cd sample/YOLOv8_plus_det

chmod -R +x scripts/

./scripts/download.sh --onnx

./scripts/download.sh --dataset

```



将 `datasets/coco128` 与 `models/onnx/yolov11s_qtable` 路径填入 LabelU「BM1688 编译设置」（**仅宿主机 TPU-MLIR 脚本链回退**；《模型训练常规 SOP》3.5 未写 qtable，以对齐 Docker `model_quant.py` + 敏感层为准）。

**推荐（对齐《模型训练常规 SOP》3.5）**：训练完成后在平台发起 BM1688 导出；校准图从 `dataset/images/train` 抽样（`YOLO_BM1688_USE_JOB_CALIBRATION=true`）；INT8 优先 **`quantize_backend=docker`**（非交互敏感层，同 SOP 的 `/model.23/dfl/conv/...` 一类）。宿主机脚本才使用本目录或 sophon-demo 的 `*_qtable`。



多平台编译：编译设置里 `compile_platforms` 填 `bm1684x,bm1688,cv186x`（耗时约为单平台的 3 倍）。



§4 例程、§5 精度（`tools/eval_coco.py`）、§6 性能（`bmrt_test`）请将整包 `models/` 复制到例程根目录后按官方 README 操作。

## FAQ 验证（[sophon-demo FAQ](https://github.com/sophgo/sophon-demo/blob/release/docs/FAQ.md)）

```bash
# 仅检查 TPU-MLIR / 校准集 / qtable / ultralytics 等（§1、§3）
labelu verify-sophon --config-only

# 检查某次导出的 models/ 树（§2–§4 前置）
labelu verify-sophon --export-id 1
# 或
python -m labelu.scripts.verify_sophon_export --export-dir /path/to/yolo_bm1688_exports/1

# 在 Sophon 设备上若已安装 bmrt_test，会自动尝试 §6.1 探测
EXPORT_ID=1 ./scripts/ops/verify_sophon_faq.sh
```


