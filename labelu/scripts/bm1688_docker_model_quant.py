#!/usr/bin/env python3
"""
容器内非交互 INT8 量化（对齐 TPU-MLIR 官方 search_qtable 流程）。

用法:
  python3 bm1688_docker_model_quant.py --pt /data/.../best.pt --calib-dir /data/.../calibration \\
    --fpfwd /model.23/dfl/conv/Conv_output_0_Conv --model-stem yolov11n --out-dir /data/.../export
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
import time
from pathlib import Path


MIN_DETECTOR_HEAD_F16_OPS = 12


def run_subprocess(command: list[str]) -> None:
    command = [str(item) for item in command]
    print("run command:", " ".join(command), flush=True)
    process = subprocess.run(command)
    if process.returncode != 0:
        print(f"command failed ({process.returncode}): {command[0]}", file=sys.stderr)
        sys.exit(process.returncode)


def sigmoid_f16_qtable_line(fpfwd_outputs: str) -> str:
    """生成检测头 Sigmoid 的预置混精规则，避免置信度被 INT8 压低。"""
    fpfwd = (fpfwd_outputs or "").strip()
    if "/model." in fpfwd:
        layer = fpfwd.split("/model.", 1)[1].split("/", 1)[0]
    else:
        layer = "23"
    return f"/model.{layer}/Sigmoid_output_0_Sigmoid F16"


def detector_head_f16_qtable_lines(
    mlir_path: Path,
    fpfwd_outputs: str,
) -> list[str]:
    """Protect the complete YOLO detect head instead of only its Sigmoid."""
    fpfwd = (fpfwd_outputs or "").strip()
    if "/model." in fpfwd:
        layer = fpfwd.split("/model.", 1)[1].split("/", 1)[0]
    else:
        layer = "23"
    prefix = f"/model.{layer}/"
    location_re = re.compile(r'loc\("([^"]+)"\)')
    location_alias_re = re.compile(
        r'^\s*(#[A-Za-z0-9_.-]+)\s*=\s*loc\("([^"]+)"\)'
    )
    location_ref_re = re.compile(r'loc\((#[A-Za-z0-9_.-]+)\)')
    operation_re = re.compile(
        r'^\s*(%[A-Za-z0-9_.-]+)\s*=\s*"top\.([A-Za-z0-9_]+)"\(([^)]*)\)'
    )
    operand_re = re.compile(r'%[A-Za-z0-9_.-]+')
    excluded_ops = {"Input", "None", "Weight"}
    protected: list[str] = []
    seen: set[str] = set()

    mlir_lines = mlir_path.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines()
    location_aliases = {
        match.group(1): match.group(2)
        for raw_line in mlir_lines
        if (match := location_alias_re.match(raw_line))
    }
    operations: list[tuple[str, str, list[str], str]] = []
    for raw_line in mlir_lines:
        operation_match = operation_re.match(raw_line)
        if not operation_match:
            continue
        location_match = location_re.search(raw_line)
        location_ref_match = location_ref_re.search(raw_line)
        op_name = (
            location_match.group(1)
            if location_match
            else location_aliases.get(location_ref_match.group(1), "")
            if location_ref_match
            else ""
        )
        operations.append(
            (
                operation_match.group(1),
                operation_match.group(2),
                operand_re.findall(operation_match.group(3)),
                op_name,
            )
        )

    def _protect(op_name: str) -> None:
        if op_name and op_name not in seen:
            seen.add(op_name)
            protected.append(f"{op_name} F16")

    selected_sigmoid = False
    for _result, op_type, _operands, op_name in operations:
        if op_type in excluded_ops:
            continue
        if op_name.startswith(prefix) or op_name.startswith("output0"):
            _protect(op_name)
            selected_sigmoid = selected_sigmoid or op_type == "Sigmoid"

    if len(protected) < MIN_DETECTOR_HEAD_F16_OPS:
        # Newer Ultralytics/Torch exporters replace /model.23/... operation
        # names with generic names such as conv2d_63_Conv. Follow the dataflow
        # from model.23 weights instead of relying on those unstable names.
        dotted_prefix = f"model.{layer}."
        head_values = {
            result
            for result, op_type, _operands, op_name in operations
            if op_type == "Weight"
            and (op_name.startswith(prefix) or op_name.startswith(dotted_prefix))
        }
        for result, op_type, operands, op_name in operations:
            if result not in head_values and not any(
                operand in head_values for operand in operands
            ):
                continue
            head_values.add(result)
            if op_type not in excluded_ops:
                _protect(op_name)
                selected_sigmoid = selected_sigmoid or op_type == "Sigmoid"

    sigmoid_line = sigmoid_f16_qtable_line(fpfwd_outputs)
    if not selected_sigmoid and sigmoid_line.split()[0] not in seen:
        protected.append(sigmoid_line)
    return protected


def ensure_qtable_lines(qtable: Path, required_lines: list[str]) -> int:
    """Append mandatory mixed-precision rules omitted by search_qtable output."""
    text = qtable.read_text(encoding="utf-8", errors="replace")
    existing = {
        line.strip().split()[0]
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing = [line for line in required_lines if line.split()[0] not in existing]
    if missing:
        with qtable.open("a", encoding="utf-8") as stream:
            if text and not text.endswith("\n"):
                stream.write("\n")
            stream.write("\n".join(missing) + "\n")
    return len(missing)


def build_search_qtable_command(
    *,
    mlir_path: Path,
    calib_dir: Path,
    input_num: int,
    inference_num: int,
    chip: str,
    expected_cos: float,
    max_float_layers: int,
    quantize_method_list: str,
    pre_qtable: Path,
    qtable: Path,
    cali_table: Path,
) -> list[str]:
    """构造 TPU-MLIR 官方自动混精校准命令。"""
    return [
        "run_calibration.py",
        str(mlir_path),
        "--dataset",
        str(calib_dir),
        "--input_num",
        str(input_num),
        "--inference_num",
        str(inference_num),
        "--chip",
        chip,
        "--max_float_layers",
        str(max_float_layers),
        "--expected_cos",
        str(expected_cos),
        "--quantize_method_list",
        quantize_method_list,
        "--search",
        "search_qtable",
        "--pre_qtable",
        str(pre_qtable),
        "--quantize_table",
        str(qtable),
        "-o",
        str(cali_table),
    ]


def patch_ultralytics_tasks_if_present() -> None:
    src = Path("/labelu/scripts/sophon_assets/tasks_onnx.py")
    dst = Path("/usr/local/lib/python3.10/dist-packages/ultralytics/nn/tasks.py")
    backup = dst.with_name("tasks_labelu_original.py")
    if src.is_file() and dst.parent.is_dir():
        import shutil

        if not backup.is_file():
            current = dst.read_text(encoding="utf-8", errors="ignore")
            if "Ultralytics tasks.py ONNX export shim" in current:
                print(
                    "ultralytics tasks.py is already the LabelU shim but backup is missing; "
                    "reinstall ultralytics in the container and retry",
                    file=sys.stderr,
                    flush=True,
                )
                sys.exit(1)
            shutil.copy2(dst, backup)
            print(f"backed up ultralytics tasks to {backup}", flush=True)
        shutil.copy2(src, dst)
        print(f"patched ultralytics tasks from {src}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="BM1688 Docker INT8 quant (non-interactive)")
    parser.add_argument("--pt", default="", help="best.pt absolute path（与 --onnx 二选一）")
    parser.add_argument(
        "--onnx",
        default="",
        help="已导出的 Sophon ONNX（优先于容器内从 pt 重导，对齐 permute 补丁）",
    )
    parser.add_argument("--imgsz", type=int, default=0, help="输入尺寸，--onnx 时必填或由 pt 推断")
    parser.add_argument("--calib-dir", required=True, help="calibration images directory")
    parser.add_argument(
        "--fpfwd",
        required=True,
        help="legacy detector-head output used to locate the Sigmoid protection rule",
    )
    parser.add_argument("--model-stem", default="yolov11n", help="mlir model stem, e.g. yolov11n")
    parser.add_argument("--out-dir", required=True, help="directory to place *_int8.bmodel")
    parser.add_argument("--chip", default="bm1688")
    parser.add_argument("--inference-num", type=int, default=10)
    parser.add_argument("--expected-cos", type=float, default=0.99)
    parser.add_argument("--max-float-layers", type=int, default=5)
    parser.add_argument("--quantize-method-list", default="kl,mse")
    parser.add_argument(
        "--emit-fp32-fp16",
        action="store_true",
        help="在同一 MLIR 上额外生成 FP32/FP16 1b bmodel",
    )
    args = parser.parse_args()

    pt_path = Path(args.pt).resolve() if str(args.pt).strip() else None
    onnx_arg = Path(args.onnx).resolve() if str(args.onnx).strip() else None
    calib_dir = Path(args.calib_dir).resolve()
    out_dir = Path(args.out_dir).resolve()
    if not onnx_arg and (not pt_path or not pt_path.is_file()):
        print("需要 --onnx 或有效 --pt", file=sys.stderr)
        return 1
    if not calib_dir.is_dir():
        print(f"calib dir not found: {calib_dir}", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    work_dir = Path(f"/workspace/labelu_quant_{int(time.time())}")
    work_dir.mkdir(parents=True, exist_ok=True)
    model_stem = args.model_stem.strip() or "yolov11n"
    mlir_path = work_dir / f"{model_stem}.mlir"
    imgsz = int(args.imgsz or 0)

    if onnx_arg and onnx_arg.is_file():
        import shutil

        onnx_path = work_dir / onnx_arg.name
        shutil.copy2(onnx_arg, onnx_path)
        if imgsz <= 0:
            imgsz = 640
        print(f"use pre-exported onnx imgsz={imgsz} -> {onnx_path}", flush=True)
    else:
        patch_ultralytics_tasks_if_present()
        from ultralytics import YOLO

        yolo_cfg = work_dir / ".ultralytics"
        yolo_cfg.mkdir(parents=True, exist_ok=True)
        os.environ["YOLO_CONFIG_DIR"] = str(yolo_cfg)

        def _cleanup_spurious_onnx_beside_pt() -> None:
            spurious = pt_path.with_suffix(".onnx")
            if spurious.is_file():
                try:
                    spurious.unlink()
                    print(f"removed spurious onnx beside weights: {spurious}", flush=True)
                except OSError as exc:
                    print(f"WARN: could not remove {spurious}: {exc}", file=sys.stderr, flush=True)

        model = YOLO(str(pt_path))
        try:
            imgsz = int(model.ckpt["train_args"]["imgsz"])
        except Exception as exc:
            print(f"cannot read train imgsz: {exc}", file=sys.stderr)
            return 1

        onnx_path = work_dir / pt_path.name.replace(".pt", ".onnx")
        print(f"export onnx imgsz={imgsz} -> {onnx_path}", flush=True)
        model.export(format="onnx", imgsz=imgsz, opset=17, dynamic=False, batch=1)
        exported = Path(str(pt_path).replace(".pt", ".onnx"))
        if exported.is_file():
            import shutil

            shutil.copy2(exported, onnx_path)
        if not onnx_path.is_file():
            model.export(
                format="onnx",
                imgsz=imgsz,
                opset=17,
                dynamic=False,
                batch=1,
                simplify=True,
                nms=False,
                half=False,
            )
            exported = Path(str(pt_path).replace(".pt", ".onnx"))
            if exported.is_file():
                import shutil

                shutil.copy2(exported, onnx_path)
        if not onnx_path.is_file():
            print("onnx export failed", file=sys.stderr)
            return 1
        _cleanup_spurious_onnx_beside_pt()

    run_subprocess(
        [
            "model_transform.py",
            "--model_name",
            str(work_dir / model_stem),
            "--model_def",
            str(onnx_path),
            "--input_shapes",
            f"[[1,3,{imgsz},{imgsz}]]",
            "--mean",
            "0.0,0.0,0.0",
            "--scale",
            "0.0039216,0.0039216,0.0039216",
            "--keep_aspect_ratio",
            "--pixel_format",
            "rgb",
            "--mlir",
            str(mlir_path),
        ]
    )

    if args.emit_fp32_fp16:
        for quantize, suffix in (("F32", "fp32"), ("F16", "fp16")):
            fp_model = work_dir / f"{model_stem}_{suffix}_1b.bmodel"
            run_subprocess(
                [
                    "model_deploy.py",
                    "--mlir",
                    str(mlir_path),
                    "--quantize",
                    quantize,
                    "--chip",
                    args.chip,
                    "--model",
                    str(fp_model),
                ]
            )
            if not fp_model.is_file():
                print(f"bmodel not found: {fp_model}", file=sys.stderr)
                return 1
            import shutil

            shutil.copy2(fp_model, out_dir / f"{model_stem}_{suffix}_1b.bmodel")

    img_list: list[str] = []
    for pattern in ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"):
        img_list.extend(glob.glob(str(calib_dir / pattern)))
    if not img_list:
        print(f"no calibration images in {calib_dir}", file=sys.stderr)
        return 1
    input_num = len(img_list)
    print(f"calibration images={input_num} dir={calib_dir}", flush=True)

    fpfwd = args.fpfwd.strip()
    qtable = work_dir / f"{model_stem}_qtable"
    detector_head_lines = detector_head_f16_qtable_lines(mlir_path, fpfwd)
    if len(detector_head_lines) < MIN_DETECTOR_HEAD_F16_OPS:
        print(
            "detector head discovery is incomplete: "
            f"found={len(detector_head_lines)} required>={MIN_DETECTOR_HEAD_F16_OPS}",
            file=sys.stderr,
        )
        return 1
    pre_qtable = work_dir / f"{model_stem}_pre_qtable"
    pre_qtable.write_text("\n".join(detector_head_lines) + "\n", encoding="utf-8")
    print(
        f"protected detector-head F16 ops={len(detector_head_lines)}",
        flush=True,
    )
    cali_table = work_dir / f"{model_stem}_cali_table"
    run_subprocess(
        build_search_qtable_command(
            mlir_path=mlir_path,
            calib_dir=calib_dir,
            input_num=input_num,
            inference_num=max(1, int(args.inference_num)),
            chip=args.chip,
            expected_cos=min(1.0, max(0.0, float(args.expected_cos))),
            max_float_layers=max(1, int(args.max_float_layers)),
            quantize_method_list=args.quantize_method_list,
            pre_qtable=pre_qtable,
            qtable=qtable,
            cali_table=cali_table,
        )
    )
    if not cali_table.is_file():
        print("calibration table not generated", file=sys.stderr)
        return 1
    if not qtable.is_file():
        print("search_qtable did not generate quantize table", file=sys.stderr)
        return 1
    restored_count = ensure_qtable_lines(qtable, detector_head_lines)
    if restored_count:
        print(
            f"restored detector-head F16 protection ops={restored_count}",
            flush=True,
        )

    final_name = f"{model_stem}_int8.bmodel"
    run_subprocess(
        [
            "model_deploy.py",
            "--mlir",
            str(mlir_path),
            "--quantize",
            "INT8",
            "--chip",
            args.chip,
            "--model",
            str(work_dir / final_name),
            "--calibration_table",
            str(cali_table),
            "--quantize_table",
            str(qtable),
            "--num_core",
            "2",
        ]
    )
    produced = work_dir / final_name
    if not produced.is_file():
        print(f"bmodel not found: {produced}", file=sys.stderr)
        return 1

    import shutil

    canonical = out_dir / f"{model_stem}_int8_1b.bmodel"
    shutil.copy2(produced, canonical)
    canonical_qt = out_dir / f"{model_stem}_qtable"
    shutil.copy2(qtable, canonical_qt)
    print(f"INT8 bmodel OK: {canonical}", flush=True)
    print(f"qtable OK: {canonical_qt}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
