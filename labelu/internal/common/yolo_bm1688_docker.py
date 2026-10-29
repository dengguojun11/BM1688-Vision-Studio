"""BM1688 INT8 量化：Docker 容器生命周期与调用（对齐文档 3.5 Docker 流程）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple

from loguru import logger

from labelu.internal.common.config import settings

# 写死在代码中，不进 .env
BM1688_DOCKER_CONTAINER = "labelu_bm1688"
BM1688_DOCKER_IMAGE = "sophgo/tpuc_dev:v3.2"
BM1688_DOCKER_TAR_NAME = "sophgo-tpuc_dev-v3.2_191a433358ad.tar.gz"
BM1688_DOCKER_MOUNT_TARGET = "/mnt/labelu"
DEFAULT_DOCKER_FPFWD_OUTPUTS = "/model.23/dfl/conv/Conv_output_0_Conv"
DEFAULT_QUANTIZE_BACKEND = "docker"  # docker | host
WINDOWS_DOCKER_POLL_INTERVAL_SECONDS = 0.5
WINDOWS_DOCKER_STALL_ARTIFACT_GRACE_SECONDS = 2.0
WINDOWS_DOCKER_START_TIMEOUT_SECONDS = 120


def _resolve_docker_executable() -> str:
    explicit = (os.environ.get("LABELU_DOCKER_BIN") or "").strip()
    if explicit:
        return explicit

    discovered = shutil.which("docker") or shutil.which("docker.exe")
    if discovered:
        return discovered

    if os.name == "nt":
        for candidate in (
            Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"),
            Path(r"C:\Program Files\Docker\cli-plugins\docker.exe"),
        ):
            if candidate.is_file():
                return str(candidate)
    return "docker"


def _current_user_is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    try:
        return geteuid() == 0
    except OSError:
        return False


def _repo_root() -> Path:
    import labelu

    # .../labelU/labelu/__init__.py → 项目根 labelU
    return Path(labelu.__file__).resolve().parent.parent


def _docker_script_path() -> Path:
    import labelu

    return Path(labelu.__file__).resolve().parent / "scripts" / "bm1688_docker_model_quant.py"


def _bootstrap_script_path() -> Path:
    import labelu

    return Path(labelu.__file__).resolve().parent.parent / "scripts" / "bm1688_docker_bootstrap.sh"


def _docker_mount_base() -> Path:
    """与 deploy.sh 一致：挂载部署根 <BASE>，覆盖 labeIuMedia 下导出路径。"""
    from labelu.internal.common.bm1688_deploy_bootstrap import _deploy_base_dir

    env_base = (os.environ.get("LABELU_DEPLOY_RESOLVED_BASE") or "").strip()
    if env_base:
        return Path(env_base).resolve()
    return _deploy_base_dir()


def _docker_mount_target() -> str:
    raw = (os.environ.get("LABELU_DOCKER_MOUNT_TARGET") or "").strip()
    return raw or BM1688_DOCKER_MOUNT_TARGET


def _docker_container_path(path: Path) -> str:
    resolved = Path(path).resolve()
    if os.name != "nt":
        return str(resolved)

    base = _docker_mount_base().resolve()
    try:
        relative = resolved.relative_to(base)
    except ValueError:
        return str(resolved)
    return f"{_docker_mount_target().rstrip('/')}/{relative.as_posix()}"


def docker_cli_available() -> bool:
    try:
        proc = subprocess.run(
            [_resolve_docker_executable(), "version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _ensure_windows_docker_engine() -> Tuple[bool, str]:
    if os.name != "nt":
        return False, "non-Windows host"
    desktop = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
    docker_desktop_running = False
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Docker Desktop.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        tasklist = str(proc.stdout or "").lower()
        docker_desktop_running = proc.returncode == 0 and "docker desktop.exe" in tasklist
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Docker Desktop process check failed: {}", exc)

    if not docker_desktop_running:
        if not desktop.is_file():
            return False, f"Docker Desktop not found: {desktop}"
        try:
            subprocess.Popen(
                [str(desktop)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except OSError as exc:
            return False, f"Docker Desktop start failed: {exc}"

    deadline = time.monotonic() + WINDOWS_DOCKER_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if docker_cli_available():
            return True, "Docker Desktop Linux engine ready"
        time.sleep(2)
    return False, "Docker Desktop Linux engine did not become ready"


def _container_exists(name: str = BM1688_DOCKER_CONTAINER) -> bool:
    proc = subprocess.run(
        [_resolve_docker_executable(), "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return False
    return name in (proc.stdout or "").splitlines()


def _container_running(name: str = BM1688_DOCKER_CONTAINER) -> bool:
    proc = subprocess.run(
        [_resolve_docker_executable(), "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return False
    return name in (proc.stdout or "").splitlines()


def ensure_bm1688_docker_ready(
    *,
    mount_path: Optional[Path] = None,
    allow_bootstrap: bool = True,
) -> Tuple[bool, str]:
    """
    确保 Docker 可用且 labelu_bm1688 容器已创建并完成首次 pip 安装。
    allow_bootstrap=True 时在 root 下可自动执行 deploy 同款 bootstrap 脚本。
    """
    docker_ready = docker_cli_available()
    if not docker_ready and os.name == "nt":
        docker_ready, docker_note = _ensure_windows_docker_engine()
        if not docker_ready:
            logger.warning("BM1688 Docker unavailable: {}", docker_note)

    if docker_ready and _container_running():
        return True, f"container {BM1688_DOCKER_CONTAINER} already running"

    if not docker_ready:
        if not allow_bootstrap:
            return False, "未检测到 docker 命令"
        try:
            from labelu.scripts.bootstrap_bm1688_docker import bootstrap_bm1688_docker

            mount = str((mount_path or _docker_mount_base()).resolve())
            return bootstrap_bm1688_docker(mount_path=mount)
        except Exception as exc:
            return False, f"Docker 自动安装失败: {exc}"

    mount = (mount_path or _docker_mount_base()).resolve()
    bootstrap_sh = _bootstrap_script_path()
    if allow_bootstrap and bootstrap_sh.is_file() and _current_user_is_root():
        try:
            proc = subprocess.run(
                ["bash", str(bootstrap_sh), str(mount)],
                capture_output=True,
                text=True,
                timeout=7200,
                env={**os.environ, "LABELU_DEPLOY_RESOLVED_BASE": str(mount)},
            )
            if proc.returncode == 0 and _container_running():
                return True, (proc.stdout or "")[-1500:] or "container ready"
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            return False, f"bm1688_docker_bootstrap 失败: {tail}"
        except subprocess.TimeoutExpired:
            return False, "Docker bootstrap 超时（>7200s）"
        except OSError as exc:
            return False, f"无法执行 bootstrap: {exc}"

    if _container_exists():
        subprocess.run(
            [_resolve_docker_executable(), "start", BM1688_DOCKER_CONTAINER],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if _container_running():
            return True, "container started"
    return False, "Docker 容器未就绪（请执行 sudo bash deploy.sh update）"


def quantize_backend_from_cfg(file_cfg: Optional[dict[str, Any]]) -> str:
    cfg = file_cfg or {}
    backend = str(cfg.get("quantize_backend") or DEFAULT_QUANTIZE_BACKEND).strip().lower()
    return backend if backend in ("docker", "host") else DEFAULT_QUANTIZE_BACKEND


def docker_fpfwd_from_cfg(file_cfg: Optional[dict[str, Any]]) -> str:
    cfg = file_cfg or {}
    raw = str(cfg.get("docker_fpfwd_outputs") or "").strip()
    return raw or DEFAULT_DOCKER_FPFWD_OUTPUTS


def docker_cali_inference_num_from_cfg(file_cfg: Optional[dict[str, Any]]) -> int:
    cfg = file_cfg or {}
    try:
        return max(1, int(cfg.get("cali_inference_num") or 10))
    except (TypeError, ValueError):
        return 10


def _int8_bmodel_signature(out_dir: Path, model_stem: str) -> Optional[Tuple[str, int, int]]:
    target = Path(out_dir) / f"{model_stem}_int8_1b.bmodel"
    if not target.is_file():
        return None
    try:
        stat = target.stat()
    except OSError:
        return None
    return (target.name, int(stat.st_size), int(stat.st_mtime_ns))


def _bmodel_set_signature(
    out_dir: Path, model_stem: str, *, include_fp32_fp16: bool
) -> Optional[Tuple[Tuple[str, int, int], ...]]:
    suffixes = ("fp32", "fp16", "int8") if include_fp32_fp16 else ("int8",)
    signatures = []
    for suffix in suffixes:
        target = Path(out_dir) / f"{model_stem}_{suffix}_1b.bmodel"
        if not target.is_file():
            return None
        try:
            stat = target.stat()
        except OSError:
            return None
        signatures.append((target.name, int(stat.st_size), int(stat.st_mtime_ns)))
    return tuple(signatures)


def run_docker_int8_quant(
    *,
    pt_path: Path,
    calib_dir: Path,
    out_dir: Path,
    model_stem: str,
    file_cfg: Optional[dict[str, Any]] = None,
    onnx_path: Optional[Path] = None,
    imgsz: int = 640,
    timeout_seconds: int = 7200,
    line_callback: Optional[Callable[[str, bool], None]] = None,
    emit_fp32_fp16: bool = False,
) -> Tuple[bool, str]:
    """
    在容器内执行 BM1688 编译脚本，产出 INT8；full 模式同时补齐 FP32/FP16。
    """
    pt_path = pt_path.resolve()
    calib_dir = calib_dir.resolve()
    out_dir = out_dir.resolve()
    script = _docker_script_path()
    if not script.is_file():
        return False, f"缺少脚本: {script}"

    ok, msg = ensure_bm1688_docker_ready()
    if not ok:
        return False, msg

    fpfwd = docker_fpfwd_from_cfg(file_cfg)
    inference_num = docker_cali_inference_num_from_cfg(file_cfg)
    pt_in_container = _docker_container_path(pt_path)
    calib_in_container = _docker_container_path(calib_dir)
    out_in_container = _docker_container_path(out_dir)
    onnx_part = ""
    if onnx_path and Path(onnx_path).is_file():
        onnx_part = (
            f" --onnx {_docker_container_path(Path(onnx_path).resolve())}"
            f" --imgsz {int(imgsz or 640)}"
        )
    inner = (
        f"python3 /labelu/scripts/bm1688_docker_model_quant.py "
        f"--pt {pt_in_container} --calib-dir {calib_in_container} "
        f"--fpfwd {fpfwd} --model-stem {model_stem} --out-dir {out_in_container} "
        f"--inference-num {inference_num}{onnx_part}"
    )
    if emit_fp32_fp16:
        inner += " --emit-fp32-fp16"
    logger.info("BM1688 docker quant: {}", inner[:500])
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []
    preexisting_bmodels = _bmodel_set_signature(
        out_dir, model_stem, include_fp32_fp16=emit_fp32_fp16
    )

    def _emit_callback_fragments(raw: str, *, is_stderr: bool) -> None:
        if line_callback is None or not raw:
            return
        normalized = raw.replace("\r", "\n")
        if "\n" in normalized:
            for part in normalized.split("\n"):
                if part:
                    line_callback(part, is_stderr)
            return
        line_callback(raw, is_stderr)

    def _pump(stream, chunks: List[str], *, is_stderr: bool) -> None:
        if stream is None:
            return
        while True:
            raw = stream.read(1024)
            if not raw:
                break
            chunks.append(raw)
            _emit_callback_fragments(raw, is_stderr=is_stderr)
        stream.close()

    try:
        proc = subprocess.Popen(
            [_resolve_docker_executable(), "exec", BM1688_DOCKER_CONTAINER, "bash", "-lc", inner],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        out_thread = threading.Thread(
            target=_pump, args=(proc.stdout, stdout_chunks), kwargs={"is_stderr": False}
        )
        err_thread = threading.Thread(
            target=_pump, args=(proc.stderr, stderr_chunks), kwargs={"is_stderr": True}
        )
        out_thread.start()
        err_thread.start()
        started_at = time.monotonic()
        artifact_seen_at: Optional[float] = None
        last_bmodel_sig = preexisting_bmodels
        rc: Optional[int] = None
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.monotonic()
            if now - started_at >= timeout_seconds:
                raise subprocess.TimeoutExpired(
                    cmd=[_resolve_docker_executable(), "exec", BM1688_DOCKER_CONTAINER],
                    timeout=timeout_seconds,
                )
            if os.name == "nt":
                current_sig = _bmodel_set_signature(
                    out_dir, model_stem, include_fp32_fp16=emit_fp32_fp16
                )
                if current_sig and current_sig != preexisting_bmodels:
                    if current_sig != last_bmodel_sig:
                        artifact_seen_at = now
                        last_bmodel_sig = current_sig
                    elif artifact_seen_at is not None and (
                        now - artifact_seen_at
                    ) >= WINDOWS_DOCKER_STALL_ARTIFACT_GRACE_SECONDS:
                        proc.kill()
                        proc.wait(timeout=5)
                        out_thread.join(timeout=5)
                        err_thread.join(timeout=5)
                        stdout = "".join(stdout_chunks)
                        stderr = "".join(stderr_chunks)
                        log = ((stdout or "") + "\n" + (stderr or "")).strip()
                        note = (
                            "Detected completed BM1688 INT8 artifact on Windows after "
                            f"docker exec stalled: {current_sig[0]}"
                        )
                        return True, f"{log}\n{note}".strip()
                else:
                    artifact_seen_at = None
                    last_bmodel_sig = current_sig
            time.sleep(WINDOWS_DOCKER_POLL_INTERVAL_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        out_thread.join(timeout=5)
        err_thread.join(timeout=5)
        partial_log = "".join(stdout_chunks + stderr_chunks).strip()
        if partial_log:
            return False, partial_log + f"\nDocker 量化超时（>{timeout_seconds}s）"
        return False, f"Docker 量化超时（>{timeout_seconds}s）"
    out_thread.join()
    err_thread.join()

    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    log = (stdout or "") + "\n" + (stderr or "")
    if rc != 0:
        return False, log or f"exit_code={rc}"
    if emit_fp32_fp16 and _bmodel_set_signature(
        out_dir, model_stem, include_fp32_fp16=True
    ) is None:
        return False, (
            f"{log}\nDocker full 编译完成但未找到 {model_stem}_fp32_1b.bmodel、"
            f"{model_stem}_fp16_1b.bmodel、{model_stem}_int8_1b.bmodel"
        ).strip()
    return True, log
