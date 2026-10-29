from typing import Literal, Optional

from pydantic import BaseModel, Field


class Bm1688CompilerConfigResponse(BaseModel):
    tpu_mlir_root: str = ""
    auto_compile: bool = True
    quantize: Literal["fp16", "f32", "int8", "full"] = "full"
    bmodel_basename: str = "yolov11n"
    onnx_filename: str = "yolo11n.onnx"
    qtable_filename: str = "yolov11n_qtable"
    compile_platforms: str = "bm1688"
    custom_compile_command: str = ""
    calibration_dataset_dir: str = ""
    quantize_table_path: str = ""
    int8_batches: str = "1"
    cali_input_num: int = 128
    cali_inference_num: int = 10
    calibration_balance_scene: bool = True
    calibration_filename_prefix: str = ""
    calibration_infer_lighting_opencv: bool = False
    calibration_count_mode: str = "adaptive"
    quantize_backend: str = "docker"
    docker_fpfwd_outputs: str = "/model.23/dfl/conv/Conv_output_0_Conv"
    sophon_export: bool = True
    tpu_mlir_python: str = Field(
        default="",
        description="TPU-MLIR 编译用 python3（需能 import mlir、psutil），如 /root/miniconda3/bin/python3",
    )
    builtin_script_path: str = Field(default="", description="内置编译脚本路径")
    sophon_doc_url: str = ""
    sophon_export_guide_url: str = ""
    config_backend_label: str = Field(
        default="MySQL 治理表（system_config_*）",
        description="当前配置主读写来源",
    )
    legacy_sync_file: str = Field(default="", description="兼容同步文件路径（非主读写来源）")
    doc_hint: str = Field(default="", description="使用说明摘要")
