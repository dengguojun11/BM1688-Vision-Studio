"""部署后自动填充 BM1688 编译配置，使页面无需手工填写即可导出/编译。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from labelu.internal.common import bm1688_compiler_config as cfg_io
from labelu.internal.common.config import settings


def _current_user_is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    try:
        return geteuid() == 0
    except OSError:
        return False


def _deploy_base_dir() -> Path:
    """deploy.sh 布局：<BASE>/labeIuMedia + <BASE>/labelu_appdata + <BASE>/tpu-mlir。"""
    media = Path(settings.MEDIA_ROOT).resolve()
    appdata = Path(settings.BASE_DATA_DIR).resolve()
    if media.parent == appdata.parent:
        return media.parent
    return appdata.parent


def discover_tpu_mlir_root() -> str:
    """TPU_MLIR_ROOT 用于脚本占位；pip 编译不依赖 envsetup.sh。"""
    for candidate in (
        (os.environ.get("TPU_MLIR_ROOT") or "").strip(),
        (settings.TPU_MLIR_ROOT or "").strip(),
        str(_deploy_base_dir() / "tpu-mlir"),
        str(Path(settings.BASE_DATA_DIR).resolve().parent / "tpu-mlir"),
    ):
        if not candidate:
            continue
        p = Path(candidate)
        if not p.is_dir():
            continue
        if (p / "envsetup.sh").is_file():
            return str(p.resolve())
        tpu_venv = Path("/data/labeIU/labeIuMedia/venv-tpu-mlir/bin/python3")
        if tpu_venv.is_file():
            return str(p.resolve())
    return ""


def discover_sophon_det_root() -> Path:
    for candidate in (
        (os.environ.get("SOPHON_DET_ROOT") or "").strip(),
        (settings.SOPHON_DET_ROOT or "").strip(),
        str(_deploy_base_dir() / "sophon-demo" / "sample" / "YOLOv8_plus_det"),
    ):
        if not candidate:
            continue
        p = Path(candidate)
        if p.is_dir():
            return p.resolve()
    return Path()


def discover_calibration_and_qtable(
    det_root: Path,
) -> Tuple[str, str]:
    cali_env = (os.environ.get("YOLO_BM1688_CALIBRATION_DATASET_DIR") or "").strip()
    if not cali_env:
        cali_env = (settings.YOLO_BM1688_CALIBRATION_DATASET_DIR or "").strip()

    qt_env = (os.environ.get("YOLO_BM1688_QTABLE_PATH") or "").strip()
    if not qt_env:
        qt_env = (settings.YOLO_BM1688_QTABLE_PATH or "").strip()

    cali = cali_env
    qtable = qt_env

    if det_root.is_dir():
        if not cali:
            p = det_root / "datasets" / "coco128"
            if p.is_dir():
                cali = str(p.resolve())
        if not qtable:
            for sub in ("models/onnx", "onnx"):
                p = det_root / sub / cfg_io.DEFAULT_QTABLE_FILENAME
                if p.is_file():
                    qtable = str(p.resolve())
                    break
            if not qtable:
                for sub in ("models/onnx", "onnx"):
                    d = det_root / sub
                    if not d.is_dir():
                        continue
                    for f in sorted(d.iterdir()):
                        if f.is_file() and "qtable" in f.name.lower():
                            qtable = str(f.resolve())
                            break
                    if qtable:
                        break

    bundled_qt = cfg_io.default_qtable_path()
    if not qtable and bundled_qt.is_file():
        qtable = str(bundled_qt.resolve())

    assets_cali = _deploy_base_dir() / "sophon_assets" / "coco128"
    if not cali and assets_cali.is_dir():
        cali = str(assets_cali.resolve())

    assets_qt = _deploy_base_dir() / "sophon_assets" / cfg_io.DEFAULT_QTABLE_FILENAME
    if not qtable and assets_qt.is_file():
        qtable = str(assets_qt.resolve())

    return cali, qtable


def _is_usable_python_exe(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _venv_bin_python_path(bindir: Path, name: str) -> Optional[str]:
    """返回 venv bin 下的解释器路径；禁止 .resolve() 落到 /usr/bin（symlink 链常见）。"""
    p = bindir / name
    if not _is_usable_python_exe(p):
        return None
    # 保留 venv 内路径，使 subprocess 使用 venv 的 site-packages
    return str(p)


def discover_tpu_mlir_python() -> str:
    """BM1688 编译必须用 venv-tpu-mlir/bin/python3（勿存 resolve 后的 /usr/bin）。"""
    bindirs: list[Path] = []
    for base in (
        Path(settings.MEDIA_ROOT) / "venv-tpu-mlir" / "bin",
        Path("/data/labeIU/labeIuMedia/venv-tpu-mlir/bin"),
        Path("/root/miniconda3/envs/tpu310/bin"),
    ):
        if base.is_dir() and base not in bindirs:
            bindirs.append(base)
    prefer_names = ("python3", "python3.10", "python")
    for bindir in bindirs:
        for name in prefer_names:
            found = _venv_bin_python_path(bindir, name)
            if found:
                return found
        for p in sorted(bindir.glob("python3*")):
            if _is_usable_python_exe(p):
                return str(p)
    return ""


def _is_invalid_tpu_mlir_python(path: str) -> bool:
    p = (path or "").strip()
    if not p:
        return True
    if p.startswith("/usr/bin/python"):
        return True
    try:
        real = os.path.realpath(p)
    except OSError:
        return True
    return real.startswith("/usr/bin/python")


def normalize_tpu_mlir_python_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """读取/保存前纠正 tpu_mlir_python（覆盖错误的 /usr/bin/python3.10）。"""
    out = dict(cfg)
    existing = str(out.get("tpu_mlir_python") or "").strip()
    discovered = discover_tpu_mlir_python()
    if discovered:
        out["tpu_mlir_python"] = discovered
    elif _is_invalid_tpu_mlir_python(existing):
        out["tpu_mlir_python"] = ""
    return out


def _compile_platforms_from_env() -> str:
    raw = (os.environ.get("YOLO_BM1688_COMPILE_PLATFORMS") or "").strip()
    if not raw:
        raw = (settings.YOLO_BM1688_COMPILE_PLATFORMS or "").strip()
    if raw:
        return ",".join(cfg_io.parse_compile_platforms(raw))
    return "bm1688"


def ensure_bm1688_compiler_ready(*, force_paths: bool = False) -> Dict[str, Any]:
    """将部署探测到的路径写入 bm1688_compiler_config.json（仅补缺或 force）。"""
    cur = cfg_io.load_bm1688_compiler_config()
    changed = False

    def _set(key: str, value: Any, *, only_if_empty: bool = True) -> None:
        nonlocal changed
        if value is None:
            return
        sval = value
        if isinstance(value, str):
            sval = value.strip()
        if only_if_empty and not force_paths:
            existing = cur.get(key)
            if isinstance(existing, str) and existing.strip():
                return
            if existing is not None and not isinstance(existing, str):
                return
        if cur.get(key) != sval:
            cur[key] = sval
            changed = True

    tpu = discover_tpu_mlir_root()
    _set("tpu_mlir_root", tpu)

    det = discover_sophon_det_root()
    cali, qtable = discover_calibration_and_qtable(det)
    _set("calibration_dataset_dir", cali)
    _set("quantize_table_path", qtable)

    _set("compile_platforms", _compile_platforms_from_env())
    _set("auto_compile", True, only_if_empty=False)
    _set("quantize", "full", only_if_empty=True)
    _set("sophon_export", True, only_if_empty=False)
    _set("onnx_filename", cfg_io.DEFAULT_ONNX_FILENAME, only_if_empty=True)
    _set("qtable_filename", cfg_io.DEFAULT_QTABLE_FILENAME, only_if_empty=True)
    bmodel_base = (
        (os.environ.get("YOLO_BM1688_BMODEL_BASENAME") or "").strip()
        or (getattr(settings, "YOLO_BM1688_BMODEL_BASENAME", None) or "").strip()
        or cfg_io.DEFAULT_BMODEL_BASENAME
    )
    _set("bmodel_basename", bmodel_base, only_if_empty=not force_paths)
    _set("int8_batches", "1", only_if_empty=True)
    _set("calibration_count_mode", "adaptive", only_if_empty=True)
    _set("quantize_backend", "docker", only_if_empty=True)
    _set("docker_fpfwd_outputs", "/model.23/dfl/conv/Conv_output_0_Conv", only_if_empty=True)
    _set("calibration_balance_scene", True, only_if_empty=False)

    normalized = normalize_tpu_mlir_python_config(cur)
    if normalized.get("tpu_mlir_python") != cur.get("tpu_mlir_python"):
        cur["tpu_mlir_python"] = normalized.get("tpu_mlir_python", "")
        changed = True

    if changed:
        cfg_io.save_bm1688_compiler_config(cur)
        logger.info(
            "BM1688 compiler config auto-filled: tpu={} cali={} qtable={}",
            bool(tpu),
            bool(cali),
            bool(qtable),
        )
    return cur


def ensure_bm1688_docker_deploy(*, force: bool = False) -> Tuple[bool, str]:
    """部署/启动时自动准备 BM1688 Docker（需 root，与 deploy.sh 一致）。"""
    from labelu.internal.common import bm1688_compiler_config as cfg_io
    from labelu.internal.common.yolo_bm1688_docker import (
        docker_cli_available,
        ensure_bm1688_docker_ready,
        quantize_backend_from_cfg,
    )

    cfg = cfg_io.load_bm1688_compiler_config()
    if quantize_backend_from_cfg(cfg) != "docker":
        return True, "quantize_backend=host，跳过 Docker"
    if not sys.platform.startswith("linux"):
        return True, f"non-linux host ({sys.platform})，跳过 Docker"

    if not _current_user_is_root() and not docker_cli_available():
        return False, "Docker 未安装且当前非 root，请 sudo deploy.sh update"

    if not force and docker_cli_available():
        from labelu.internal.common.yolo_bm1688_docker import _container_running

        if _container_running():
            return True, "docker container running"

    return ensure_bm1688_docker_ready(allow_bootstrap=True)


def deploy_readiness_summary() -> str:
    cfg = ensure_bm1688_compiler_ready()
    tpu = bool((cfg.get("tpu_mlir_root") or "").strip())
    cali = bool((cfg.get("calibration_dataset_dir") or "").strip())
    qt = bool((cfg.get("quantize_table_path") or "").strip())
    docker_ok, docker_note = ensure_bm1688_docker_deploy()
    parts = [
        f"tpu_mlir={'ok' if tpu else 'missing'}",
        f"coco128={'ok' if cali else 'missing'}",
        f"qtable={'ok' if qt else 'missing'}",
        f"docker={'ok' if docker_ok else 'missing'}",
        f"quantize_backend={cfg.get('quantize_backend', 'docker')}",
        f"auto_compile={cfg.get('auto_compile')}",
    ]
    if not docker_ok:
        parts.append(f"docker_note={docker_note[:120]}")
    return ", ".join(parts)
