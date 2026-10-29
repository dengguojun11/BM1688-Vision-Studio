"""边端 sophon-demo 路径候选。"""
from pathlib import Path

from labelu.internal.common.edge_sophon_demo import (
    is_valid_sophon_demo_root,
    sophon_demo_candidate_paths,
)


def test_sophon_demo_candidate_paths_dedup():
    paths = sophon_demo_candidate_paths(
        "/data/sophon-demo/sample/YOLOv8_plus_det"
    )
    assert paths == [
        "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det"
    ]

    configured = "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det"
    assert sophon_demo_candidate_paths(configured) == [configured]


def test_is_valid_sophon_demo_root(tmp_path):
    demo = tmp_path / "YOLOv8_plus_det"
    (demo / "python").mkdir(parents=True)
    (demo / "python" / "yolov8_bmcv.py").write_text("# stub", encoding="utf-8")
    assert is_valid_sophon_demo_root(str(demo)) is True
    assert is_valid_sophon_demo_root(str(tmp_path / "missing")) is False
