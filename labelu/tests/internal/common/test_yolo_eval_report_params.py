from pathlib import Path

from labelu.internal.common.yolo_eval_report_params import (
    extract_iteration_params_from_report,
    extract_recommended_params_json_for_storage,
)

_REPO = Path(__file__).resolve().parents[4]
_JOB2_BODY = _REPO / ".tmp" / "job2_report4_body.txt"


def test_extract_from_job2_yaml_and_prose_body():
    body = _JOB2_BODY.read_text(encoding="utf-8")
    merged = extract_iteration_params_from_report(body=body)
    assert merged.get("lr0") == 0.01
    assert merged.get("lrf") == 0.01
    assert merged.get("warmup_epochs") == 3
    assert merged.get("rect") is False
    assert merged.get("conf") == 0.25
    assert merged.get("iou") == 0.5
    assert merged.get("multi_scale") is True
    assert merged.get("imgsz") == 640


def test_extract_recommended_params_json_for_storage_from_yaml_body():
    body = (
        "建议如下：\n```yaml\nlr0: 0.005\nbatch: 8\n```\n"
        "另可尝试 conf_thres=0.3"
    )
    raw = extract_recommended_params_json_for_storage(body)
    assert raw
    import json

    obj = json.loads(raw)
    inner = obj["recommended_train_params"]
    assert inner["lr0"] == 0.005
    assert inner["batch"] == 8
    assert inner["conf"] == 0.3


def test_json_block_still_preferred():
    body = (
        '```json\n{"recommended_train_params": {"lr0": 0.002, "epochs": 100}}\n```'
    )
    merged = extract_iteration_params_from_report(body=body)
    assert merged["lr0"] == 0.002
    assert merged["epochs"] == 100
