"""训练 / BM1688 编译 / 量化 / AICM 发布的过程日志（与对应 job/export 工作目录同目录存放）。"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, TextIO

from loguru import logger

from labelu.internal.common.config import settings

# 镜像到 loguru 的单行上限；stderr tee 回写时须跳过 loguru 自身报错，避免日志爆炸
_LOG_MIRROR_MAX_CHARS = 2000
_LOG_MIRROR_SKIP_MARKERS = (
    "--- Logging error in Loguru Handler",
    "Record was:",
    "Message: '",
    "Arguments: (",
)


def _escape_loguru_message(line: str) -> str:
    return str(line).replace("{", "{{").replace("}", "}}")


def _should_skip_log_mirror(line: str) -> bool:
    text = str(line or "")
    if len(text) > _LOG_MIRROR_MAX_CHARS:
        return True
    return any(marker in text for marker in _LOG_MIRROR_SKIP_MARKERS)


def _mirror_process_log_line(logger_prefix: str, line: str) -> None:
    if not logger_prefix or _should_skip_log_mirror(line):
        return
    safe = _escape_loguru_message(line)
    if len(safe) > _LOG_MIRROR_MAX_CHARS:
        safe = safe[:_LOG_MIRROR_MAX_CHARS] + "...(truncated)"
    logger.info("{} {}", logger_prefix, safe)


def yolo_train_work_root(job_id: int) -> Path:
    return Path(settings.MEDIA_ROOT) / settings.YOLO_TRAIN_WORK_SUBDIR / f"job-{int(job_id)}"


def yolo_train_log_path(job_id: int) -> Path:
    """{MEDIA_ROOT}/yolo_train/job-{id}/process.log"""
    return yolo_train_work_root(job_id) / "process.log"


def bm1688_export_work_root(export_id: int) -> Path:
    return Path(settings.MEDIA_ROOT) / settings.YOLO_BM1688_EXPORT_SUBDIR / str(int(export_id))


def bm1688_compile_log_path(export_id: int) -> Path:
    """{MEDIA_ROOT}/yolo_bm1688_exports/{export_id}/compile.log"""
    return bm1688_export_work_root(export_id) / "compile.log"


def bm1688_quantize_log_path(export_id: int) -> Path:
    """{MEDIA_ROOT}/yolo_bm1688_exports/{export_id}/quantize.log"""
    return bm1688_export_work_root(export_id) / "quantize.log"


def aicm_publish_log_path(job_id: str) -> Path:
    """{BASE_DATA_DIR}/aicm_publish_jobs/{job_id}/process.log"""
    from labelu.internal.common.aicm_publish_job import job_work_dir

    return job_work_dir(job_id) / "process.log"


class ProcessLogWriter:
    """追加写入带时间戳的过程日志；同时可选镜像到 loguru。"""

    def __init__(
        self,
        path: Path,
        *,
        logger_prefix: str = "",
        header: Optional[str] = None,
        append_if_exists: bool = False,
    ) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._logger_prefix = (logger_prefix or "").strip()
        self._fp: Optional[TextIO] = open(self.path, "a", encoding="utf-8", buffering=1)
        if append_if_exists and self.path.is_file() and self.path.stat().st_size > 0:
            self.write(header or f"===== process log resumed path={self.path} =====")
        else:
            self.write(header or f"===== process log path={self.path} =====")

    def write(self, msg: str) -> None:
        if not msg:
            return
        for line in str(msg).splitlines():
            self._write_line(line)

    def write_block(self, title: str, body: str) -> None:
        self.write(f"--- {title} ---")
        self.write(body)
        self.write(f"--- end {title} ---")

    def _write_line(self, line: str) -> None:
        if self._fp is None:
            return
        if _should_skip_log_mirror(line):
            return
        ts = datetime.now().isoformat(timespec="seconds")
        formatted = f"[{ts}] {line}"
        self._fp.write(formatted + "\n")
        self._fp.flush()
        if self._logger_prefix:
            _mirror_process_log_line(self._logger_prefix, line)

    def close(self) -> None:
        if self._fp is None:
            return
        try:
            self._write_line("===== process log closed =====")
            self._fp.close()
        finally:
            self._fp = None

    def __enter__(self) -> "ProcessLogWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            self.write(f"EXCEPTION: {exc_type.__name__}: {exc}")
        self.close()


class _TeeStream:
    def __init__(self, original: TextIO, writer: ProcessLogWriter, *, prefix: str = "") -> None:
        self._original = original
        self._writer = writer
        self._prefix = prefix
        self._buf = ""

    def write(self, data: str) -> int:
        if not data:
            return 0
        self._original.write(data)
        self._original.flush()
        if "\r" in data and "\n" not in data:
            line = data.split("\r")[-1].rstrip()
            if line.strip() and not _should_skip_log_mirror(line):
                self._writer.write(f"{self._prefix}{line}" if self._prefix else line)
            return len(data)
        self._buf += data
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip() and not _should_skip_log_mirror(line):
                self._writer.write(f"{self._prefix}{line}" if self._prefix else line)
        return len(data)

    def flush(self) -> None:
        self._original.flush()
        if self._buf.strip() and not _should_skip_log_mirror(self._buf):
            self._writer.write(
                f"{self._prefix}{self._buf.rstrip()}" if self._prefix else self._buf.rstrip()
            )
            self._buf = ""

    def fileno(self) -> int:
        return self._original.fileno()

    def isatty(self) -> bool:
        return False


@contextmanager
def tee_stdio(writer: ProcessLogWriter, *, prefix: str = "stdout: ") -> Iterator[None]:
    """将 stdout/stderr 同时写入过程日志文件（用于 Ultralytics 训练等）。"""
    out_tee = _TeeStream(sys.stdout, writer, prefix=prefix)
    err_tee = _TeeStream(sys.stderr, writer, prefix="stderr: ")
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_tee, err_tee  # type: ignore[assignment]
    try:
        yield
    finally:
        out_tee.flush()
        err_tee.flush()
        sys.stdout, sys.stderr = old_out, old_err


def append_log_line(path: Path, line: str, *, logger_prefix: str = "") -> None:
    """轻量追加单行（供阶段心跳写入，无需持有 ProcessLogWriter）。"""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    formatted = f"[{ts}] {line}"
    with open(path, "a", encoding="utf-8") as f:
        f.write(formatted + "\n")
    if logger_prefix:
        _mirror_process_log_line(logger_prefix, line)


def append_related_files(writer: ProcessLogWriter, paths: list[Path], *, max_bytes: int = 500_000) -> None:
    """将关联产物（如 Ultralytics results.csv）尾部追加到过程日志。"""
    for p in paths:
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except OSError as exc:
            writer.write(f"SKIP read {p}: {exc}")
            continue
        if len(raw) > max_bytes:
            raw = raw[-max_bytes:]
            writer.write(f"--- tail of {p} (last {max_bytes} bytes) ---")
        else:
            writer.write(f"--- contents of {p} ---")
        try:
            writer.write(raw.decode("utf-8", errors="replace"))
        except Exception as exc:
            writer.write(f"SKIP decode {p}: {exc}")
