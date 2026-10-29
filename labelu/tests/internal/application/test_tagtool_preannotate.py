import json
import sys
from pathlib import Path

import pytest
from labelu.internal.application.service import tagtool_preannotate as svc
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.sample import SampleState, TaskSample


def _task_config():
    return {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "场景",
                            "value": "sceneType",
                            "type": "enum",
                            "options": [
                                {"key": "城市主干道", "value": "urbanArterial"},
                                {"key": "商业街", "value": "commercialStreet"},
                            ],
                        },
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        },
                    ]
                },
            }
        ]
    }


def test_validate_label_map_requires_all_expected_heads():
    errors = svc.validate_label_map({"sceneType": ["urbanArterial"]})

    assert "missing:lighting" in errors
    assert "missing:weather" in errors


def test_build_payload_filters_to_configured_matching_tag_dimensions():
    predictions = {
        "sceneType": {"label": "urbanArterial", "confidence": 0.91},
        "lighting": {"label": "normalDaylight", "confidence": 0.82},
        "weather": {"label": "sunny", "confidence": 0.73},
    }

    payload = svc.build_tagtool_preannotation_payload(
        sample_name="demo.jpg",
        task_cfg_merged=_task_config(),
        predictions=predictions,
        model_kind="imported",
        model_id=7,
        weights_name="best.pt",
    )

    items = payload["annotations"]["tagTool"]["result"]
    assert [next(iter(x["value"])) for x in items] == ["sceneType", "weather"]
    assert items[0]["type"] == "tag"
    assert payload["meta"]["source_type"] == svc.TAGTOOL_PRE_META
    emitted = {}
    for item in items:
        emitted.update(item["value"])
    assert "lighting" not in emitted


def test_build_payload_rejects_when_no_prediction_matches_task_config():
    with pytest.raises(ValueError, match="no tagTool predictions"):
        svc.build_tagtool_preannotation_payload(
            sample_name="demo.jpg",
            task_cfg_merged=_task_config(),
            predictions={"lighting": {"label": "normalDaylight", "confidence": 0.82}},
            model_kind="imported",
            model_id=7,
            weights_name="best.pt",
        )


def test_dependency_check_reports_missing_torchvision(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "torchvision":
            raise ImportError("No module named 'torchvision'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    sys.modules.pop("torchvision", None)

    with pytest.raises(LabelUException) as exc:
        svc._ensure_torchvision_available()

    assert exc.value.status_code == 501
    assert "torch 和 torchvision" in exc.value.msg


def test_safe_path_check_does_not_accept_sibling_prefix_paths():
    root = Path(r"C:\media").resolve()
    sibling = Path(r"C:\media-other\model.pt").resolve()

    assert svc._path_is_relative_to(root / "model.pt", root)
    assert not svc._path_is_relative_to(sibling, root)


def test_explicit_sample_ids_drop_skipped_samples_preserving_order():
    cmd = type("Cmd", (), {"sample_ids": [3, 1, 3], "sample_id": 2})()
    rows = [
        TaskSample(id=1, state=SampleState.NEW.value),
        TaskSample(id=2, state=SampleState.SKIPPED.value),
        TaskSample(id=3, state=SampleState.DONE.value),
    ]

    targets = svc.filter_explicit_sample_targets(cmd, rows)

    assert [s.id for s in targets] == [3, 1]


def test_tagtool_preannotation_detection_uses_meta_source():
    raw = json.dumps({"meta": {"source_type": svc.TAGTOOL_PRE_META}})

    assert svc.is_tagtool_preannotation_data(raw)
    assert not svc.is_tagtool_preannotation_data(json.dumps({"meta": {"source_type": "ai_generated"}}))
