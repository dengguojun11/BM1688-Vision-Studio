"""损坏/空图片上传应拒绝为 400，而非 OpenCV 500。"""
from __future__ import annotations

from pathlib import Path

from labelu.internal.common.image_pipeline.features import validate_image_readable


def test_validate_accepts_valid_png_without_cv2(tmp_path, monkeypatch):
    p = Path("labelu/tests/data/test.png")
    assert p.is_file()

    import builtins

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "cv2":
            raise ModuleNotFoundError("No module named 'cv2'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert validate_image_readable(p) is True


def test_validate_rejects_empty_file(tmp_path):
    p = tmp_path / "empty.jpg"
    p.write_bytes(b"")
    assert validate_image_readable(p) is False


def test_validate_rejects_garbage_bytes(tmp_path):
    p = tmp_path / "bad.jpg"
    p.write_bytes(b"not-a-real-image")
    assert validate_image_readable(p) is False
