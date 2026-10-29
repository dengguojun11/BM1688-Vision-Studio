import gzip
import importlib.util
import json
from pathlib import Path


def _load_probe():
    path = Path("labelu/scripts/sop_eval/edge_performance_probe.py").resolve()
    spec = importlib.util.spec_from_file_location("edge_performance_probe_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_probe_source_is_importable_by_python_36_runtime():
    path = Path("labelu/scripts/sop_eval/edge_performance_probe.py").resolve()
    source = path.read_text(encoding="utf-8")

    assert "from __future__ import annotations" not in source


def test_summarize_series_reports_percentiles_jitter_and_outliers():
    module = _load_probe()

    summary = module.summarize_series([1, 2, 3, 4, 100])

    assert summary["sample_count"] == 5
    assert summary["p50"] == 3.0
    assert summary["p95"] == 80.8
    assert summary["p99"] == 96.16
    assert summary["stddev"] > 0
    assert summary["outlier_count"] == 1


def test_run_performance_probe_writes_raw_series_and_structured_summary(
    tmp_path, monkeypatch
):
    module = _load_probe()
    monkeypatch.setattr(
        module,
        "collect_resource_sample",
        lambda: {
            "at_monotonic_s": 1.0,
            "cpu_usage": 25.0,
            "process_cpu_usage": 10.0,
            "memory_mb": 128.0,
            "system_memory_usage": 50.0,
            "npu_usage": None,
            "temperature_c": 61.0,
            "power_w": None,
        },
    )
    monkeypatch.setattr(
        module,
        "collect_environment",
        lambda model_path=None: {
            "device_serial": "BM1688-TEST",
            "model_path": str(model_path) if model_path else None,
        },
    )
    calls = []

    def frame_runner(frame):
        calls.append(frame)
        offset = float(len(calls))
        return {
            "decode_ms": 1.0,
            "preprocess_ms": 2.0,
            "inference_ms": 3.0 + offset / 10.0,
            "postprocess_ms": 1.5,
            "total_ms": 7.5 + offset / 10.0,
        }

    payload = module.run_performance_probe(
        frame_runner=frame_runner,
        frame_inputs=["a.jpg", "b.jpg"],
        output_dir=tmp_path,
        model_path=tmp_path / "model.bmodel",
        warmup_count=2,
        repeat_count=5,
        resource_interval_seconds=0.2,
        stability_seconds=0,
    )

    assert payload["status"] == "ok"
    assert payload["runtime_metrics"]["warmup_count_completed"] == 2
    assert payload["runtime_metrics"]["repeat_count_completed"] == 5
    assert payload["runtime_metrics"]["p99_inference_ms"] is not None
    assert payload["runtime_metrics"]["stage_metrics"]["total_ms"]["sample_count"] == 5
    assert payload["resource_usage"]["series_available"] is True
    assert "npu_usage" in payload["resource_usage"]["unavailable_fields"]
    assert payload["environment"]["device_serial"] == "BM1688-TEST"
    assert payload["stability"]["enabled"] is False

    with gzip.open(tmp_path / "latency_samples.jsonl.gz", "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    assert len(rows) == 5
    assert all(row["phase"] == "formal" for row in rows)
    assert (tmp_path / "resource_samples.jsonl.gz").is_file()
    assert (tmp_path / "performance_summary.json").is_file()


def test_proc_resource_metrics_collects_cpu_and_memory_without_psutil(monkeypatch):
    module = _load_probe()
    module._PROC_CPU_STATE.clear()
    snapshots = iter(
        [
            {
                "/proc/stat": "cpu  100 0 100 800 0 0 0 0\n",
                "/proc/self/stat": "1 (python) R 0 0 0 0 0 0 0 0 0 0 10 5 0 0",
            },
            {
                "/proc/stat": "cpu  130 0 120 850 0 0 0 0\n",
                "/proc/self/stat": "1 (python) R 0 0 0 0 0 0 0 0 0 0 18 7 0 0",
            },
        ]
    )
    current = next(snapshots)

    def fake_read(path):
        nonlocal current
        name = str(path).replace("\\", "/")
        if name == "/proc/self/status":
            return "Name:\tpython\nVmRSS:\t131072 kB\n"
        if name == "/proc/meminfo":
            return "MemTotal: 1000000 kB\nMemAvailable: 400000 kB\n"
        return current.get(name)

    monkeypatch.setattr(module, "_read_text", fake_read)
    module._proc_resource_metrics()
    current = next(snapshots)
    metrics = module._proc_resource_metrics()

    assert metrics["cpu_usage"] == 50.0
    assert metrics["process_cpu_usage"] is not None
    assert metrics["memory_mb"] == 128.0
    assert metrics["system_memory_usage"] == 60.0


def test_sysfs_npu_usage_averages_bm1688_cores(monkeypatch):
    module = _load_probe()
    monkeypatch.setattr(
        module,
        "_read_text",
        lambda path: "core 0 usage:20 avusage:10\ncore 1 usage:60 avusage:30\n"
        if str(path).replace("\\", "/").endswith("/203c0000.bmtpu/npu_usage")
        else None,
    )

    assert module._sysfs_npu_usage() == 40.0


def test_resource_summary_preserves_explicit_unavailable_reasons():
    module = _load_probe()

    summary = module._resource_summary(
        [
            {
                "npu_usage": 55.0,
                "power_w": None,
                "unavailable_reasons": {
                    "power_w": "device_power_telemetry_not_exposed"
                },
            }
        ]
    )

    assert summary["npu_usage"]["avg"] == 55.0
    assert summary["unavailable_reasons"] == {
        "power_w": "device_power_telemetry_not_exposed"
    }
