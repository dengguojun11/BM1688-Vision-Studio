"""真实 BM1688 硬件验证。

测试不在没有硬件工作目录时伪造通过。将测试放到算能盒或已准备好真实
BM1688 工作目录的环境中，并设置 LABELU_BM1688_HIL_WORK_DIR 后执行。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_bm1688_hil_runner_executes_real_bmodel_validation() -> None:
    raw_work_dir = os.environ.get("LABELU_BM1688_HIL_WORK_DIR", "").strip()
    if not raw_work_dir:
        pytest.skip("未设置 LABELU_BM1688_HIL_WORK_DIR，跳过真实 BM1688 硬件测试")

    work_dir = Path(raw_work_dir).expanduser().resolve()
    required_paths = (
        work_dir / "run_config.json",
        work_dir / "models" / "BM1688",
    )
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        pytest.fail("BM1688 硬件测试工作目录缺少: " + ", ".join(missing))

    runner = Path(__file__).resolve().parents[2] / "scripts" / "edge_board_validate_runner.py"
    result = subprocess.run(
        [sys.executable, str(runner), "--work-dir", str(work_dir)],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("LABELU_BM1688_HIL_TIMEOUT_SECONDS", "7200")),
    )
    assert result.returncode == 0, (result.stdout or "") + "\n" + (result.stderr or "")

    summary_path = work_dir / "output" / "summary.json"
    eval_path = work_dir / "output" / "bmodel_sop_eval.json"
    assert summary_path.is_file(), "BM1688 验证未生成 output/summary.json"
    assert eval_path.is_file(), "BM1688 验证未生成 output/bmodel_sop_eval.json"

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    evaluation = json.loads(eval_path.read_text(encoding="utf-8"))
    assert summary.get("passed") is True
    assert summary.get("bmodel_sop_status") == "ok"
    assert evaluation.get("status") == "ok"
