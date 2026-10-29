from pathlib import Path

from labelu.internal.common.pipeline_process_log import (
    ProcessLogWriter,
    aicm_publish_log_path,
    append_log_line,
    bm1688_compile_log_path,
    bm1688_quantize_log_path,
    yolo_train_log_path,
)


def _patch_media_root(tmp_path, monkeypatch):
    from labelu.internal.common import config as cfg_mod

    media = tmp_path / "media"
    media.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg_mod.settings, "MEDIA_ROOT", str(media))
    monkeypatch.setattr(cfg_mod.settings, "BASE_DATA_DIR", str(tmp_path / "appdata"))
    return media


def test_yolo_train_log_path_under_job_dir(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    p = yolo_train_log_path(42)
    assert p == media / "yolo_train" / "job-42" / "process.log"


def test_bm1688_log_paths_under_export_dir(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    compile_p = bm1688_compile_log_path(7)
    quant_p = bm1688_quantize_log_path(7)
    assert compile_p == media / "yolo_bm1688_exports" / "7" / "compile.log"
    assert quant_p == media / "yolo_bm1688_exports" / "7" / "quantize.log"


def test_aicm_publish_log_path_under_job_dir(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as pub_mod

    jobs_root = tmp_path / "aicm_publish_jobs"
    monkeypatch.setattr(pub_mod, "jobs_dir", lambda: jobs_root)
    p = aicm_publish_log_path("abc123")
    assert p == jobs_root / "abc123" / "process.log"


def test_process_log_writer_appends_timestamped_lines(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    path = bm1688_compile_log_path(7)
    with ProcessLogWriter(path, header="test header") as w:
        w.write("line one")
        w.write_block("block", "alpha\nbeta")
    text = path.read_text(encoding="utf-8")
    assert "test header" in text
    assert "line one" in text
    assert "--- block ---" in text
    assert "alpha" in text
    assert "process log closed" in text
    assert path.parent == media / "yolo_bm1688_exports" / "7"


def test_append_log_line(tmp_path, monkeypatch):
    media = _patch_media_root(tmp_path, monkeypatch)
    path = yolo_train_log_path(9)
    append_log_line(path, "heartbeat")
    assert "heartbeat" in path.read_text(encoding="utf-8")
    assert path.parent == media / "yolo_train" / "job-9"


def test_process_log_writer_skips_loguru_error_recursion(tmp_path, monkeypatch):
    _patch_media_root(tmp_path, monkeypatch)
    path = yolo_train_log_path(1)
    with ProcessLogWriter(path, logger_prefix="job-1", header="hdr") as w:
        w.write("--- Logging error in Loguru Handler #2 ---")
        w.write("normal line")
    text = path.read_text(encoding="utf-8")
    assert "normal line" in text
    assert "Logging error" not in text


def test_should_skip_long_mirror_lines():
    from labelu.internal.common.pipeline_process_log import _should_skip_log_mirror

    assert _should_skip_log_mirror("x" * 3000) is True
    assert _should_skip_log_mirror("epoch 1/100") is False
