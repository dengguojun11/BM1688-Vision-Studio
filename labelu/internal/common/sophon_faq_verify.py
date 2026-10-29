"""按 sophon-demo FAQ + YOLOv8_plus_det models/ 布局验证导出产物与编译环境。

参考: https://github.com/sophgo/sophon-demo/blob/release/docs/FAQ.md
例程: https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/README.md
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Sequence

from labelu.internal.common import bm1688_compiler_config as cfg_io

FAQ_URL = "https://github.com/sophgo/sophon-demo/blob/release/docs/FAQ.md"
DET_README = (
    "https://github.com/sophgo/sophon-demo/blob/release/sample/YOLOv8_plus_det/README.md"
)

MIN_ONNX_BYTES = 1024
MIN_BMODEL_BYTES = 4096


@dataclass
class SophonCheck:
    section: str
    name: str
    status: str  # pass | warn | fail | skip
    message: str
    faq_ref: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class SophonVerifyReport:
    checks: List[SophonCheck] = field(default_factory=list)
    export_root: str = ""

    @property
    def passed(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    def add(
        self,
        section: str,
        name: str,
        status: str,
        message: str,
        *,
        faq_ref: str = "",
    ) -> None:
        self.checks.append(
            SophonCheck(
                section=section,
                name=name,
                status=status,
                message=message,
                faq_ref=faq_ref,
            )
        )

    def summary_counts(self) -> dict[str, int]:
        out = {"pass": 0, "warn": 0, "fail": 0, "skip": 0}
        for c in self.checks:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_root": self.export_root,
            "passed": self.passed,
            "counts": self.summary_counts(),
            "checks": [c.to_dict() for c in self.checks],
            "faq_url": FAQ_URL,
            "det_readme_url": DET_README,
        }


def _file_ok(path: Path, min_bytes: int) -> tuple[bool, str]:
    if not path.is_file():
        return False, "文件不存在"
    size = path.stat().st_size
    if size < min_bytes:
        return False, f"文件过小 ({size} B < {min_bytes} B)"
    return True, f"{size} B"


def _check_onnx_opset(onnx_path: Path, report: SophonVerifyReport) -> None:
    try:
        import onnx  # type: ignore
    except ImportError:
        report.add(
            "2",
            "onnx 包检查 opset",
            "skip",
            "未安装 onnx，跳过算子集检查（pip install onnx）",
            faq_ref="FAQ §2.2",
        )
        return
    try:
        model = onnx.load(str(onnx_path))
        opsets = [op.version for op in model.opset_import]
        max_op = max(opsets) if opsets else 0
        if max_op >= 17:
            report.add(
                "2",
                "ONNX opset",
                "pass",
                f"opset_import max={max_op}（Sophon Export Guide 建议 17）",
                faq_ref="FAQ §2.2 / Export Guide",
            )
        else:
            report.add(
                "2",
                "ONNX opset",
                "warn",
                f"opset_import max={max_op}，低于 17，可能与 TPU-MLIR 不兼容",
                faq_ref="FAQ §2.2",
            )
    except Exception as exc:
        report.add(
            "2",
            "ONNX 解析",
            "warn",
            f"无法解析 ONNX: {exc}",
            faq_ref="FAQ §2.2",
        )


def verify_compiler_config(report: SophonVerifyReport) -> dict[str, Any]:
    """FAQ §1 环境 + §3 编译/量化前置（LabelU 编译配置）。"""
    cfg = cfg_io.load_bm1688_compiler_config()
    root = str(cfg.get("tpu_mlir_root") or "").strip()
    if not root:
        report.add(
            "1",
            "TPU_MLIR_ROOT",
            "fail",
            "未配置 tpu_mlir_root（见 Environment_Install_Guide）",
            faq_ref="FAQ §1.1",
        )
    else:
        envsetup = Path(root) / "envsetup.sh"
        if envsetup.is_file():
            report.add(
                "1",
                "TPU_MLIR_ROOT",
                "pass",
                f"envsetup.sh 存在: {envsetup}",
                faq_ref="FAQ §1.1",
            )
        else:
            report.add(
                "1",
                "TPU_MLIR_ROOT",
                "fail",
                f"缺少 envsetup.sh: {envsetup}",
                faq_ref="FAQ §1.1",
            )

    cali = str(cfg.get("calibration_dataset_dir") or "").strip()
    if cali and Path(cali).is_dir():
        report.add(
            "3",
            "INT8 校准集",
            "pass",
            f"calibration_dataset_dir 可读: {cali}",
            faq_ref="FAQ §3.1 / Calibration_Guide",
        )
    else:
        report.add(
            "3",
            "INT8 校准集",
            "warn",
            "未配置或目录不存在；full/int8 编译将失败（需 datasets/coco128）",
            faq_ref="FAQ §3.1",
        )

    qt = str(cfg.get("quantize_table_path") or "").strip()
    if qt and Path(qt).is_file():
        report.add(
            "3",
            "qtable",
            "pass",
            f"quantize_table_path: {qt}",
            faq_ref="FAQ §3 / YOLOv8_plus_det §3.2",
        )
    else:
        report.add(
            "3",
            "qtable",
            "warn",
            "未找到 qtable；INT8 混合精度可能不符官方（download.sh --onnx）",
            faq_ref="FAQ §7.5",
        )

    try:
        from ultralytics import YOLO  # noqa: F401

        report.add("2", "ultralytics", "pass", "已安装 ultralytics", faq_ref="Export Guide")
    except ImportError:
        report.add(
            "2",
            "ultralytics",
            "warn",
            "未安装 ultralytics，无法在本机导出 ONNX",
            faq_ref="FAQ §2",
        )

    tpu_py = str(cfg.get("tpu_mlir_python") or "").strip()
    tpu_venv = Path("/data/labeIU/labeIuMedia/venv-tpu-mlir/bin/python3")
    if not tpu_py and tpu_venv.is_file():
        tpu_py = str(tpu_venv.resolve())
    if tpu_py:
        _check_tpu_mlir_tools_python(report, tpu_py)
    elif root and (Path(root) / "envsetup.sh").is_file():
        _check_tpu_mlir_psutil(report, root)

    return cfg


def _check_tpu_mlir_tools_python(report: SophonVerifyReport, tpu_py: str) -> None:
    """FAQ §1：pip tpu-mlir venv 下 model_transform 可用（推荐路径）。"""
    import subprocess

    bindir = str(Path(tpu_py).parent)
    cmd = (
        f'export PATH="{bindir}:$PATH" && unset PYTHONPATH && '
        f'"{tpu_py}" -c "import psutil" && '
        f'PYTHONPATH="" model_transform.py --help'
    )
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0:
            report.add(
                "1",
                "TPU-MLIR 编译环境",
                "pass",
                f"venv-tpu-mlir 可用: {tpu_py}（psutil + model_transform.py --help）",
                faq_ref="FAQ §1 / YOLOv8_plus_det §3.2",
            )
        else:
            err = (proc.stderr or proc.stdout or "").strip()[-500:]
            report.add(
                "1",
                "TPU-MLIR 编译环境",
                "fail",
                f"请执行 bash scripts/repair_tpu_mlir_venv.sh。{err}",
                faq_ref="FAQ §1",
            )
    except Exception as exc:
        report.add(
            "1",
            "TPU-MLIR 编译环境",
            "warn",
            f"无法探测 venv-tpu-mlir: {exc}",
            faq_ref="FAQ §1",
        )


def _check_tpu_mlir_psutil(report: SophonVerifyReport, tpu_root: str) -> None:
    """FAQ §1：源码 envsetup 路径（仅当未配置 tpu_mlir_python）。"""
    import subprocess

    cmd = (
        f'export TPU_MLIR_ROOT="{tpu_root}" && export PYTHONPATH="${{PYTHONPATH:-}}" && '
        f"set +u && source \"{tpu_root}/envsetup.sh\" && set -u && "
        'PY="$(command -v python3)"; '
        '"$PY" -c "import psutil"'
    )
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if proc.returncode == 0:
            report.add(
                "1",
                "TPU-MLIR psutil",
                "pass",
                "model_transform 所用 Python 已可 import psutil",
                faq_ref="FAQ §1 / model_transform",
            )
        else:
            err = (proc.stderr or proc.stdout or "").strip()[-400:]
            report.add(
                "1",
                "TPU-MLIR psutil",
                "fail",
                f"缺少 psutil（编译 bmodel 会失败）。可执行: python3 -m pip install psutil。{err}",
                faq_ref="FAQ §1",
            )
    except Exception as exc:
        report.add(
            "1",
            "TPU-MLIR psutil",
            "warn",
            f"无法探测 psutil: {exc}",
            faq_ref="FAQ §1",
        )


def verify_export_tree(
    export_root: Path,
    *,
    cfg: Optional[dict[str, Any]] = None,
    check_env: bool = True,
) -> SophonVerifyReport:
    """验证单次导出目录是否符合 YOLOv8_plus_det models/ 树（FAQ §3–§6 前置）。"""
    report = SophonVerifyReport(export_root=str(export_root.resolve()))
    root = export_root.resolve()
    if not root.is_dir():
        report.add("7", "导出目录", "fail", f"不是目录: {root}")
        return report

    cfg = cfg or cfg_io.load_bm1688_compiler_config()
    if check_env:
        verify_compiler_config(report)

    basename = str(cfg.get("bmodel_basename") or cfg_io.DEFAULT_BMODEL_BASENAME)
    onnx_fn = str(cfg.get("onnx_filename") or cfg_io.DEFAULT_ONNX_FILENAME)
    qtable_fn = str(cfg.get("qtable_filename") or cfg_io.DEFAULT_QTABLE_FILENAME)
    platforms = cfg_io.parse_compile_platforms(cfg.get("compile_platforms"))
    mode = str(cfg.get("quantize") or "full").lower()
    canonical = cfg_io.canonical_bmodel_names(basename)

    models_root = root / "models"
    if not models_root.is_dir():
        report.add(
            "3",
            "models/ 目录",
            "fail",
            "缺少 models/（应为 sophon-demo 标准布局）",
            faq_ref="YOLOv8_plus_det §3.1",
        )
        return report
    report.add("3", "models/ 目录", "pass", str(models_root))

    onnx_dir = models_root / "onnx"
    onnx_path = onnx_dir / onnx_fn
    ok, msg = _file_ok(onnx_path, MIN_ONNX_BYTES)
    report.add(
        "2",
        f"models/onnx/{onnx_fn}",
        "pass" if ok else "fail",
        msg,
        faq_ref="YOLOv8_plus_det §3.2",
    )
    if ok:
        _check_onnx_opset(onnx_path, report)

    qtable_in_tree = onnx_dir / qtable_fn
    ok_q, msg_q = _file_ok(qtable_in_tree, 16)
    if ok_q:
        report.add(
            "3",
            f"models/onnx/{qtable_fn}",
            "pass",
            msg_q,
            faq_ref="YOLOv8_plus_det §3.2",
        )
    else:
        report.add(
            "3",
            f"models/onnx/{qtable_fn}",
            "warn",
            "导出包内无 qtable（INT8 需与官方 qtable 一致）",
            faq_ref="FAQ §3",
        )

    for chip in platforms:
        plat_dir = cfg_io.chip_to_platform_dir(chip)
        pdir = models_root / plat_dir
        if not pdir.is_dir():
            report.add(
                "3",
                f"models/{plat_dir}/",
                "fail" if mode == "full" else "warn",
                "平台目录不存在",
                faq_ref="YOLOv8_plus_det §3.2",
            )
            continue
        missing: List[str] = []
        for name in canonical:
            bp = pdir / name
            ok_b, msg_b = _file_ok(bp, MIN_BMODEL_BYTES)
            if ok_b:
                report.add(
                    "3",
                    f"{plat_dir}/{name}",
                    "pass",
                    msg_b,
                    faq_ref="YOLOv8_plus_det §3.2",
                )
            else:
                missing.append(name)
                report.add(
                    "3",
                    f"{plat_dir}/{name}",
                    "fail" if mode == "full" else "warn",
                    msg_b,
                    faq_ref="YOLOv8_plus_det §3.2",
                )
        if mode == "full" and not missing:
            report.add(
                "4",
                f"{plat_dir} 例程就绪",
                "pass",
                f"3 个 bmodel 齐全，可复制到 YOLOv8_plus_det/models/{plat_dir}/ 做 §4 测试",
                faq_ref="YOLOv8_plus_det §4",
            )

    _check_bmrt_test_optional(models_root, platforms, canonical, basename, report)
    return report


def _check_bmrt_test_optional(
    models_root: Path,
    platforms: Sequence[str],
    canonical: Sequence[str],
    basename: str,
    report: SophonVerifyReport,
) -> None:
    """FAQ §6.1：若板端有 bmrt_test，可对 fp16_1b 做理论性能探测。"""
    bmrt = shutil.which("bmrt_test")
    if not bmrt:
        report.add(
            "6",
            "bmrt_test",
            "skip",
            "PATH 中无 bmrt_test（需在 Sophon 设备/SDK 环境执行 §6）",
            faq_ref="FAQ §6.1",
        )
        return
    prefer = f"{basename}_fp16_1b.bmodel"
    bmodel_path: Optional[Path] = None
    for chip in platforms:
        plat = cfg_io.chip_to_platform_dir(chip)
        candidate = models_root / plat / prefer
        if candidate.is_file():
            bmodel_path = candidate
            break
    if not bmodel_path:
        report.add(
            "6",
            "bmrt_test",
            "skip",
            f"未找到 {prefer}，跳过性能探测",
            faq_ref="FAQ §6.1",
        )
        return
    try:
        proc = subprocess.run(
            [bmrt, "--bmodel", str(bmodel_path)],
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ.copy(),
        )
        if proc.returncode == 0:
            snippet = (proc.stdout or "")[-800:]
            report.add(
                "6",
                "bmrt_test",
                "pass",
                f"exit=0; 输出末尾:\n{snippet}",
                faq_ref="FAQ §6.1 / YOLOv8_plus_det §6.1",
            )
        else:
            report.add(
                "6",
                "bmrt_test",
                "warn",
                f"exit={proc.returncode}; stderr={(proc.stderr or '')[-400:]}",
                faq_ref="FAQ §6.1",
            )
    except subprocess.TimeoutExpired:
        report.add("6", "bmrt_test", "warn", "执行超时 120s", faq_ref="FAQ §6.1")
    except OSError as exc:
        report.add("6", "bmrt_test", "warn", str(exc), faq_ref="FAQ §6.1")


def format_report_text(report: SophonVerifyReport) -> str:
    counts = report.summary_counts()
    lines = [
        "=== Sophon FAQ 验证报告 ===",
        f"FAQ: {FAQ_URL}",
        f"例程: {DET_README}",
        f"目录: {report.export_root or '(config only)'}",
        f"结果: {'通过' if report.passed else '未通过'} "
        f"(pass={counts.get('pass', 0)} warn={counts.get('warn', 0)} "
        f"fail={counts.get('fail', 0)} skip={counts.get('skip', 0)})",
        "",
    ]
    current_section = ""
    for c in report.checks:
        if c.section != current_section:
            current_section = c.section
            titles = {
                "1": "§1 环境安装",
                "2": "§2 模型导出",
                "3": "§3 编译与量化",
                "4": "§4 例程测试（布局）",
                "5": "§5 精度测试（前置）",
                "6": "§6 性能测试",
                "7": "§7 其他",
            }
            lines.append(f"--- {titles.get(c.section, c.section)} ---")
        ref = f" [{c.faq_ref}]" if c.faq_ref else ""
        lines.append(f"  [{c.status.upper():4}] {c.name}: {c.message}{ref}")
    if not report.passed:
        lines.extend(
            [
                "",
                "排障提示（FAQ）:",
                "  §2.2 检查 PyTorch/ONNX opset；§3.1 校准见 Calibration_Guide",
                "  §7.5 download.sh 网络/依赖；§7.3 设备内存 bm_ion_alloc",
            ]
        )
    return "\n".join(lines)


def print_report(report: SophonVerifyReport, *, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report_text(report))
    return 0 if report.passed else 1
