"""边端 SSH 验证相关脚本路径解析（与 venv / LABELU_PROJECT_ROOT 布局无关）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional


def _labelu_package_root() -> Path:
    import labelu

    return Path(labelu.__file__).resolve().parent


def _project_root_candidates() -> list[Path]:
    pkg = _labelu_package_root()
    roots: list[Path] = []
    env_root = (os.environ.get("LABELU_PROJECT_ROOT") or "").strip()
    if env_root:
        roots.append(Path(env_root).resolve())
    roots.append(pkg.parent)
    seen: set[Path] = set()
    out: list[Path] = []
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        out.append(root)
    return out


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if path.is_file():
            return path
    return None


def resolve_edge_board_validate_runner() -> Path:
    """返回 edge_board_validate_runner.py 路径（首选已存在文件）。"""
    pkg = _labelu_package_root()
    candidates = [pkg / "scripts" / "edge_board_validate_runner.py"]
    for root in _project_root_candidates():
        candidates.append(root / "labelu" / "scripts" / "edge_board_validate_runner.py")
    found = _first_existing(candidates)
    return found if found is not None else candidates[0]


def resolve_edge_bmodel_validator() -> Path:
    pkg = _labelu_package_root()
    candidates = [pkg.parent / "scripts" / "edge_bmodel_validator" / "validate.py"]
    for root in _project_root_candidates():
        candidates.append(root / "scripts" / "edge_bmodel_validator" / "validate.py")
    found = _first_existing(candidates)
    return found if found is not None else candidates[0]


def resolve_plate_recognition_bm1688_infer() -> Path:
    pkg = _labelu_package_root()
    candidates = [pkg / "scripts" / "plate_recognition_bm1688_infer.py"]
    for root in _project_root_candidates():
        candidates.append(
            root / "labelu" / "scripts" / "plate_recognition_bm1688_infer.py"
        )
    found = _first_existing(candidates)
    return found if found is not None else candidates[0]


def resolve_plate_detection_bm1688_infer() -> Path:
    pkg = _labelu_package_root()
    candidates = [pkg / "scripts" / "plate_detection_bm1688_infer.py"]
    for root in _project_root_candidates():
        candidates.append(
            root / "labelu" / "scripts" / "plate_detection_bm1688_infer.py"
        )
    found = _first_existing(candidates)
    return found if found is not None else candidates[0]


def resolve_board_agent_script() -> Path:
    """返回 labelu_board_agent.py（位于仓库根 scripts/ops/）。"""
    pkg = _labelu_package_root()
    candidates = [pkg.parent / "scripts" / "ops" / "labelu_board_agent.py"]
    for root in _project_root_candidates():
        candidates.append(root / "scripts" / "ops" / "labelu_board_agent.py")
    found = _first_existing(candidates)
    return found if found is not None else candidates[0]


def resolve_sop_eval_dir() -> Path:
    pkg = _labelu_package_root()
    for root in _project_root_candidates():
        p = root / "labelu" / "scripts" / "sop_eval"
        if p.is_dir():
            return p
    return pkg / "scripts" / "sop_eval"


def resolve_python_test_evaluation() -> Path:
    return resolve_sop_eval_dir() / "pythonTest_evaluation.py"


def resolve_evaluation_utils() -> Path:
    return resolve_sop_eval_dir() / "evaluation_utils.py"
