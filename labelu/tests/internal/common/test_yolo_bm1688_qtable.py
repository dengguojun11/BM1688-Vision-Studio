from __future__ import annotations

from pathlib import Path

from labelu.internal.common import yolo_bm1688_sophon as sophon


def test_discover_qtable_no_yolov11s_fallback(tmp_path, monkeypatch):
    """yolov11n 不得回退到 yolov11s 模板 qtable。"""
    wrong = tmp_path / "yolov11s_qtable"
    wrong.write_text("shared-template\n", encoding="utf-8")
    monkeypatch.setattr(
        sophon.cfg_io,
        "load_bm1688_compiler_config",
        lambda: {"quantize_table_path": str(wrong)},
    )
    got = sophon.discover_qtable_path("yolov11n", {})
    assert got is None


def test_is_reused_yolov11s_qtable_detects_copy(tmp_path, monkeypatch):
    ref = tmp_path / "bundled_yolov11s_qtable"
    ref.write_text("same-content\n", encoding="utf-8")
    copy = tmp_path / "yolov11n_qtable"
    copy.write_text("same-content\n", encoding="utf-8")
    monkeypatch.setattr(sophon.cfg_io, "default_qtable_path", lambda: ref)
    assert sophon.is_reused_yolov11s_qtable(copy, "yolov11n") is True
    assert sophon.is_reused_yolov11s_qtable(copy, "yolov11s") is False
