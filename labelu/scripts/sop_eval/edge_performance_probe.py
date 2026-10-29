"""Structured BM1688 latency, resource, environment, and stability probe."""

import gzip
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


STAGE_KEYS = (
    "decode_ms",
    "preprocess_ms",
    "inference_ms",
    "postprocess_ms",
    "total_ms",
)
_PROC_CPU_STATE: Dict[str, float] = {}
_PROC_CPU_LOCK = threading.Lock()


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: Iterable[float], percentile: float) -> Optional[float]:
    rows = sorted(float(value) for value in values)
    if not rows:
        return None
    if len(rows) == 1:
        return rows[0]
    position = (len(rows) - 1) * float(percentile) / 100.0
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return rows[lower]
    weight = position - lower
    return rows[lower] * (1.0 - weight) + rows[upper] * weight


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    return round(value, digits) if value is not None else None


def summarize_series(values: Iterable[Any]) -> Dict[str, Any]:
    rows = [number for number in (_finite(value) for value in values) if number is not None]
    if not rows:
        return {"sample_count": 0}
    q1 = _percentile(rows, 25.0)
    q3 = _percentile(rows, 75.0)
    iqr = float(q3 - q1) if q1 is not None and q3 is not None else 0.0
    low = float(q1 - 1.5 * iqr) if q1 is not None else min(rows)
    high = float(q3 + 1.5 * iqr) if q3 is not None else max(rows)
    average = statistics.mean(rows)
    stddev = statistics.pstdev(rows) if len(rows) > 1 else 0.0
    return {
        "sample_count": len(rows),
        "avg": _round(average),
        "min": _round(min(rows)),
        "max": _round(max(rows)),
        "p50": _round(_percentile(rows, 50.0)),
        "p95": _round(_percentile(rows, 95.0)),
        "p99": _round(_percentile(rows, 99.0)),
        "stddev": _round(stddev),
        "cv": _round(stddev / average if average else 0.0, 6),
        "outlier_count": sum(1 for value in rows if value < low or value > high),
        "outlier_rule": "iqr_1.5",
    }


def _read_text(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        return text or None
    except OSError:
        return None


def _run_command(command: List[str], timeout: float = 3.0) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
            check=False,
        )
        return {
            "command": command,
            "returncode": int(proc.returncode),
            "stdout": (proc.stdout or "").strip()[-4000:],
            "stderr": (proc.stderr or "").strip()[-1000:],
        }
    except Exception as exc:
        return {"command": command, "returncode": None, "error": str(exc)[:1000]}


def _sha256(path: Optional[Path]) -> Optional[str]:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def collect_environment(model_path: Optional[Path] = None) -> Dict[str, Any]:
    os_release = {}
    raw_os_release = _read_text(Path("/etc/os-release")) or ""
    for line in raw_os_release.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os_release[key.strip()] = value.strip().strip('"')

    serial = None
    serial_source = None
    for candidate in (
        Path("/proc/device-tree/serial-number"),
        Path("/sys/firmware/devicetree/base/serial-number"),
        Path("/sys/class/dmi/id/product_serial"),
    ):
        value = _read_text(candidate)
        if value:
            serial = value.replace("\x00", "").strip()
            serial_source = str(candidate)
            break

    sail_version = None
    try:
        import sophon.sail as sail  # type: ignore

        sail_version = str(
            getattr(sail, "__version__", None)
            or getattr(sail, "version", None)
            or "installed_version_not_exposed"
        )
    except Exception as exc:
        sail_version = "unavailable: {}".format(str(exc)[:200])

    bm_smi = shutil.which("bm-smi")
    bm_smi_version = _run_command([bm_smi, "--version"]) if bm_smi else None
    bm_smi_query = _run_command([bm_smi], timeout=5.0) if bm_smi else None
    libsophon = None
    if shutil.which("dpkg-query"):
        libsophon = _run_command(
            ["dpkg-query", "-W", "-f=${Package} ${Version}\\n", "libsophon*"],
            timeout=5.0,
        )

    firmware = None
    firmware_source = None
    for candidate in (
        Path("/sys/class/bm-sophon/card0/device/firmware_version"),
        Path("/sys/class/bm-sophon/card0/device/board_version"),
        Path("/sys/class/bm-sophon/bm-sophon0/device/firmware_version"),
    ):
        value = _read_text(candidate)
        if value:
            firmware = value
            firmware_source = str(candidate)
            break

    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostname": platform.node() or None,
        "device_serial": serial,
        "device_serial_source": serial_source,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "os_release": os_release,
        "sophon_sail_version": sail_version,
        "bm_smi_path": bm_smi,
        "bm_smi_version": bm_smi_version,
        "bm_smi_query": bm_smi_query,
        "libsophon_packages": libsophon,
        "firmware_version": firmware,
        "firmware_version_source": firmware_source,
        "container_image": os.getenv("LABELU_EDGE_RUNTIME_IMAGE", "").strip() or None,
        "sophon_demo_root": os.getenv("SOPHON_DEMO_ROOT", "").strip() or None,
        "model_path": str(model_path) if model_path else None,
        "model_sha256": _sha256(model_path),
    }


def _sysfs_temperature_c() -> Optional[float]:
    values = []
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        raw = _finite(_read_text(path))
        if raw is None:
            continue
        values.append(raw / 1000.0 if raw > 1000.0 else raw)
    return max(values) if values else None


def _sysfs_power_w() -> Optional[float]:
    values = []
    root = Path("/sys/class/hwmon")
    for pattern in ("hwmon*/power*_average", "hwmon*/power*_input"):
        for path in root.glob(pattern):
            raw = _finite(_read_text(path))
            if raw is None or raw < 0:
                continue
            values.append(raw / 1000000.0 if raw > 1000.0 else raw)
    return sum(values) if values else None


def _sysfs_npu_usage() -> Optional[float]:
    paths = [Path("/sys/devices/platform/203c0000.bmtpu/npu_usage")]
    paths.extend(Path("/sys/devices/platform").glob("*bmtpu*/npu_usage"))
    values = []
    seen = set()
    for path in paths:
        normalized = str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        text = _read_text(path) or ""
        for raw in re.findall(r"(?:^|\s)usage\s*:\s*([0-9]+(?:\.[0-9]+)?)", text, re.I):
            value = _finite(raw)
            if value is not None:
                values.append(max(0.0, min(100.0, value)))
    return statistics.mean(values) if values else None


def _bm_smi_metrics() -> Dict[str, Optional[float]]:
    executable = shutil.which("bm-smi")
    if not executable:
        return {"npu_usage": None, "temperature_c": None, "power_w": None}
    result = _run_command([executable], timeout=2.0)
    text = str(result.get("stdout") or "")

    def match(patterns: Iterable[str]) -> Optional[float]:
        for pattern in patterns:
            found = re.search(pattern, text, re.I)
            if found:
                return _finite(found.group(1))
        return None

    return {
        "npu_usage": match((r"(?:npu|tpu)[^\n%]*?([0-9]+(?:\.[0-9]+)?)\s*%",)),
        "temperature_c": match((r"(?:temp|temperature)[^\n0-9]*([0-9]+(?:\.[0-9]+)?)",)),
        "power_w": match((r"(?:power|pwr)[^\n0-9]*([0-9]+(?:\.[0-9]+)?)\s*w",)),
    }


def _proc_resource_metrics() -> Dict[str, Optional[float]]:
    metrics: Dict[str, Optional[float]] = {
        "cpu_usage": None,
        "process_cpu_usage": None,
        "memory_mb": None,
        "system_memory_usage": None,
    }
    try:
        cpu_parts = (_read_text(Path("/proc/stat")) or "").splitlines()[0].split()[1:]
        cpu_ticks = [float(value) for value in cpu_parts]
        total_ticks = sum(cpu_ticks)
        idle_ticks = sum(cpu_ticks[3:5]) if len(cpu_ticks) >= 5 else cpu_ticks[3]
        process_parts = (_read_text(Path("/proc/self/stat")) or "").split()
        process_ticks = float(process_parts[13]) + float(process_parts[14])
        with _PROC_CPU_LOCK:
            previous_total = _PROC_CPU_STATE.get("total")
            previous_idle = _PROC_CPU_STATE.get("idle")
            previous_process = _PROC_CPU_STATE.get("process")
            _PROC_CPU_STATE.update(
                {"total": total_ticks, "idle": idle_ticks, "process": process_ticks}
            )
        if previous_total is not None and total_ticks > previous_total:
            total_delta = total_ticks - previous_total
            idle_delta = idle_ticks - float(previous_idle or 0.0)
            process_delta = process_ticks - float(previous_process or 0.0)
            metrics["cpu_usage"] = max(
                0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)
            )
            metrics["process_cpu_usage"] = max(
                0.0,
                process_delta / total_delta * float(os.cpu_count() or 1) * 100.0,
            )
    except (OSError, ValueError, IndexError):
        pass

    try:
        status = _read_text(Path("/proc/self/status")) or ""
        match = re.search(r"^VmRSS:\s+([0-9.]+)\s+kB", status, re.M)
        if match:
            metrics["memory_mb"] = float(match.group(1)) / 1024.0
    except (ValueError, OSError):
        pass

    try:
        meminfo = _read_text(Path("/proc/meminfo")) or ""
        values = {
            key: float(raw)
            for key, raw in re.findall(r"^(MemTotal|MemAvailable):\s+([0-9.]+)\s+kB", meminfo, re.M)
        }
        if values.get("MemTotal"):
            metrics["system_memory_usage"] = (
                1.0 - values.get("MemAvailable", 0.0) / values["MemTotal"]
            ) * 100.0
    except (ValueError, OSError):
        pass
    return metrics


def collect_resource_sample() -> Dict[str, Any]:
    sysfs_npu_usage = _sysfs_npu_usage()
    sysfs_power_w = _sysfs_power_w()
    sample = {
        "at_monotonic_s": round(time.monotonic(), 6),
        "cpu_usage": None,
        "process_cpu_usage": None,
        "memory_mb": None,
        "system_memory_usage": None,
        "npu_usage": sysfs_npu_usage,
        "temperature_c": _sysfs_temperature_c(),
        "power_w": sysfs_power_w,
        "unavailable_reasons": {},
    }
    try:
        import psutil  # type: ignore

        process = psutil.Process()
        sample.update(
            {
                "cpu_usage": _finite(psutil.cpu_percent(interval=None)),
                "process_cpu_usage": _finite(process.cpu_percent(interval=None)),
                "memory_mb": _finite(process.memory_info().rss / 1024.0 / 1024.0),
                "system_memory_usage": _finite(psutil.virtual_memory().percent),
            }
        )
    except Exception:
        sample.update(
            {
                key: value
                for key, value in _proc_resource_metrics().items()
                if value is not None
            }
        )
        try:
            import resource

            rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            if sample.get("memory_mb") is None:
                sample["memory_mb"] = rss / (1024.0 if platform.system() != "Darwin" else 1024.0 * 1024.0)
        except Exception:
            pass

    # BM1688 bm-smi may be an interactive curses process. Prefer the non-blocking
    # sysfs telemetry and only use bm-smi as a fallback when NPU sysfs is absent.
    if sample.get("npu_usage") is None:
        bm = _bm_smi_metrics()
        sample["npu_usage"] = bm.get("npu_usage")
        if bm.get("temperature_c") is not None:
            sample["temperature_c"] = bm["temperature_c"]
        if bm.get("power_w") is not None:
            sample["power_w"] = bm["power_w"]
    if sample.get("npu_usage") is None:
        sample["unavailable_reasons"]["npu_usage"] = "device_npu_telemetry_not_exposed"
    if sample.get("power_w") is None:
        sample["unavailable_reasons"]["power_w"] = "device_power_telemetry_not_exposed"
    return sample


class ResourceSampler:
    def __init__(self, interval_seconds: float):
        self.interval_seconds = max(0.2, float(interval_seconds or 1.0))
        self.samples: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None:
            return

        def worker() -> None:
            while not self._stop.is_set():
                self.samples.append(collect_resource_sample())
                self._stop.wait(self.interval_seconds)

        self._thread = threading.Thread(target=worker, name="edge-resource-probe")
        self._thread.daemon = True
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_seconds * 2.0))


def _write_jsonl_gzip(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(str(path), "wt", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")


def _resource_summary(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary = {"sample_count": len(samples), "series_available": False}
    available_fields = []
    unavailable_fields = []
    for key in (
        "cpu_usage",
        "process_cpu_usage",
        "memory_mb",
        "system_memory_usage",
        "npu_usage",
        "temperature_c",
        "power_w",
    ):
        values = [sample.get(key) for sample in samples]
        stats = summarize_series(values)
        if stats.get("sample_count"):
            summary[key] = stats
            available_fields.append(key)
        else:
            unavailable_fields.append(key)
    summary["series_available"] = bool(available_fields)
    summary["available_fields"] = available_fields
    summary["unavailable_fields"] = unavailable_fields
    unavailable_reasons = {}
    for sample in samples:
        reasons = sample.get("unavailable_reasons")
        if isinstance(reasons, dict):
            unavailable_reasons.update(
                (str(key), str(value)) for key, value in reasons.items() if value
            )
    summary["unavailable_reasons"] = {
        key: unavailable_reasons[key]
        for key in unavailable_fields
        if key in unavailable_reasons
    }
    return summary


def run_performance_probe(
    *,
    frame_runner: Callable[[Any], Dict[str, Any]],
    frame_inputs: List[Any],
    output_dir: Path,
    model_path: Optional[Path] = None,
    warmup_count: int = 50,
    repeat_count: int = 1000,
    resource_interval_seconds: float = 1.0,
    stability_seconds: float = 0.0,
) -> Dict[str, Any]:
    if not frame_inputs:
        return {"status": "unavailable", "error": "no benchmark frames"}

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    warmup_count = max(0, int(warmup_count or 0))
    repeat_count = max(1, int(repeat_count or 1))
    stability_seconds = max(0.0, float(stability_seconds or 0.0))
    warmup_completed = 0
    warmup_failed = 0
    for index in range(warmup_count):
        try:
            frame_runner(frame_inputs[index % len(frame_inputs)])
            warmup_completed += 1
        except Exception:
            warmup_failed += 1

    sampler = ResourceSampler(resource_interval_seconds)
    latency_rows: List[Dict[str, Any]] = []
    sampler.start()
    formal_started = time.perf_counter()
    for index in range(repeat_count):
        started = time.perf_counter()
        row: Dict[str, Any] = {"sequence": index, "phase": "formal", "ok": True}
        try:
            metrics = frame_runner(frame_inputs[index % len(frame_inputs)]) or {}
            row.update(metrics)
        except Exception as exc:
            row.update({"ok": False, "error": str(exc)[:500]})
        row["wall_ms"] = round((time.perf_counter() - started) * 1000.0, 4)
        latency_rows.append(row)
    formal_seconds = max(time.perf_counter() - formal_started, 1e-9)

    stability_rows: List[Dict[str, Any]] = []
    stability_started = time.perf_counter()
    if stability_seconds > 0:
        index = 0
        while time.perf_counter() - stability_started < stability_seconds:
            started = time.perf_counter()
            row = {"sequence": index, "phase": "stability", "ok": True}
            try:
                row.update(frame_runner(frame_inputs[index % len(frame_inputs)]) or {})
            except Exception as exc:
                row.update({"ok": False, "error": str(exc)[:500]})
            row["wall_ms"] = round((time.perf_counter() - started) * 1000.0, 4)
            stability_rows.append(row)
            index += 1
    stability_elapsed = time.perf_counter() - stability_started if stability_seconds > 0 else 0.0
    sampler.stop()

    successful = [row for row in latency_rows if row.get("ok") is not False]
    stage_summary = {}
    for key in STAGE_KEYS:
        values = [
            row.get("wall_ms") if key == "total_ms" and row.get(key) is None else row.get(key)
            for row in successful
        ]
        stage_summary[key] = summarize_series(values)
    inference = stage_summary.get("inference_ms") or {}
    runtime_metrics = {
        "schema_version": "bm1688-performance-v1",
        "warmup_count_requested": warmup_count,
        "warmup_count_completed": warmup_completed,
        "warmup_failed": warmup_failed,
        "repeat_count_requested": repeat_count,
        "repeat_count_completed": len(successful),
        "failed_count": len(latency_rows) - len(successful),
        "formal_duration_seconds": round(formal_seconds, 4),
        "throughput_fps": round(len(successful) / formal_seconds, 4),
        "avg_inference_ms": inference.get("avg"),
        "p50_inference_ms": inference.get("p50"),
        "p95_inference_ms": inference.get("p95"),
        "p99_inference_ms": inference.get("p99"),
        "latency_series_available": bool(latency_rows),
        "stage_series_available": any(
            stats.get("sample_count") for stats in stage_summary.values()
        ),
        "stage_metrics": stage_summary,
    }
    resource_usage = _resource_summary(sampler.samples)
    stability_success = sum(1 for row in stability_rows if row.get("ok") is not False)
    stability = {
        "enabled": stability_seconds > 0,
        "requested_duration_seconds": stability_seconds,
        "duration_seconds": round(stability_elapsed, 4),
        "total_frames": len(stability_rows),
        "successful_frames": stability_success,
        "failed_frames": len(stability_rows) - stability_success,
        "failure_rate": (
            round((len(stability_rows) - stability_success) / float(len(stability_rows)), 8)
            if stability_rows
            else None
        ),
        "restart_count": None,
        "series_available": bool(stability_rows),
    }
    environment = collect_environment(model_path=model_path)

    latency_path = output_dir / "latency_samples.jsonl.gz"
    resource_path = output_dir / "resource_samples.jsonl.gz"
    environment_path = output_dir / "environment.json"
    summary_path = output_dir / "performance_summary.json"
    stability_path = output_dir / "stability_summary.json"
    _write_jsonl_gzip(latency_path, latency_rows + stability_rows)
    _write_jsonl_gzip(resource_path, sampler.samples)
    environment_path.write_text(
        json.dumps(environment, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    stability_path.write_text(
        json.dumps(stability, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    artifacts = {
        "latency_samples": latency_path.name,
        "resource_samples": resource_path.name,
        "environment": environment_path.name,
        "stability": stability_path.name,
        "summary": summary_path.name,
    }
    payload = {
        "status": "ok" if successful else "fail",
        "runtime_metrics": runtime_metrics,
        "resource_usage": resource_usage,
        "environment": environment,
        "stability": stability,
        "performance_artifacts": artifacts,
    }
    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload
