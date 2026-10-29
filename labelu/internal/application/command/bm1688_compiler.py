from typing import Literal, Optional

from pydantic import BaseModel, Field


class PatchBm1688CompilerConfigCommand(BaseModel):
    tpu_mlir_root: Optional[str] = Field(
        default=None,
        description="TPU-MLIR 安装根目录（含 envsetup.sh）",
        max_length=1024,
    )
    auto_compile: Optional[bool] = Field(
        default=None,
        description="导出 ONNX 后是否自动编译 .bmodel",
    )
    quantize: Optional[Literal["fp16", "f32", "int8", "full"]] = Field(
        default=None,
        description="fp32/fp16/int8 或 full（仅生成 {bmodel_basename}_fp32/fp16/int8_1b.bmodel 三个文件）",
    )
    bmodel_basename: Optional[str] = Field(
        default=None,
        description="BModel 文件名前缀，默认 yolov11n（与训练权重 yolo11n.pt 一致）",
        max_length=64,
    )
    onnx_filename: Optional[str] = Field(
        default=None,
        description="ONNX 文件名，默认 yolo11n.onnx（写入 models/onnx/）",
        max_length=128,
    )
    qtable_filename: Optional[str] = Field(
        default=None,
        description="qtable 文件名，默认 yolov11n_qtable（复制到 models/onnx/）",
        max_length=128,
    )
    compile_platforms: Optional[str] = Field(
        default=None,
        description="编译目标平台，逗号分隔：bm1684x,bm1688,cv186x",
        max_length=128,
    )
    custom_compile_command: Optional[str] = Field(
        default=None,
        description="非空时优先；占位符 {onnx} {out_dir} {imgsz} {platform}",
        max_length=8000,
    )
    calibration_dataset_dir: Optional[str] = Field(
        default=None,
        description="INT8 校准图片目录（如 sophon-demo datasets/coco128）",
        max_length=2048,
    )
    quantize_table_path: Optional[str] = Field(
        default=None,
        description="INT8 混合精度 qtable 文件路径（如 models/onnx/yolov11n_qtable）",
        max_length=2048,
    )
    int8_batches: Optional[str] = Field(
        default=None,
        description="保留兼容，仅使用 batch=1",
        max_length=32,
    )
    cali_input_num: Optional[int] = Field(
        default=None,
        ge=1,
        le=1000,
        description="从训练集抽取的校准图片数（文档建议 100～1000，常用 128）",
    )
    cali_inference_num: Optional[int] = Field(default=None, ge=1, le=1000)
    calibration_balance_scene: Optional[bool] = Field(
        default=None,
        description="按场景/类别均衡抽样（需训练导出含 labelsxml/train）",
    )
    calibration_filename_prefix: Optional[str] = Field(
        default=None,
        description="仅抽样文件名以此前缀开头的图，逗号分隔，如 garbage",
        max_length=256,
    )
    calibration_infer_lighting_opencv: Optional[bool] = Field(
        default=None,
        description="缺 lighting 标注时用 OpenCV 推断（需安装 opencv-python）",
    )
    calibration_count_mode: Optional[str] = Field(
        default=None,
        description="校准张数：adaptive（按 train 规模）或 fixed（用 cali_input_num）",
        max_length=16,
    )
    quantize_backend: Optional[str] = Field(
        default=None,
        description="int8 量化方式：docker（容器 model_quant 流程）或 host（TPU-MLIR 脚本）",
        max_length=16,
    )
    docker_fpfwd_outputs: Optional[str] = Field(
        default=None,
        description="Docker 量化敏感层（非交互），如 /model.23/dfl/conv/Conv_output_0_Conv",
        max_length=256,
    )
    sophon_export: Optional[bool] = Field(
        default=None,
        description="ONNX 导出是否按 sophon Export Guide（opset17、dynamic、permute）",
    )
    tpu_mlir_python: Optional[str] = Field(
        default=None,
        description="TPU-MLIR 编译用 python3 绝对路径（需 import mlir、psutil）",
        max_length=1024,
    )
