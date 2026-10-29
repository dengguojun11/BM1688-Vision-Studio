import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest


QUALITY_RULE = {
    "version": "bm1688-yolo11-detection-v1",
    "edge": {"baseline_variant": "fp32", "variant_max_drop": {"fp16": 0.010, "int8": 0.020}},
}


def _load_runner():
    path = Path(__file__).parents[2] / "scripts" / "edge_board_validate_runner.py"
    spec = importlib.util.spec_from_file_location(
        "edge_board_validate_runner_test", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_bmodel_evaluation_requires_all_three_generic_variants(monkeypatch, tmp_path):
    runner = _load_runner()
    work = tmp_path / "work"
    (work / "models" / "BM1688").mkdir(parents=True)
    (work / "models" / "BM1688" / "candidate_int8_1b.bmodel").write_bytes(b"bmodel")

    monkeypatch.setattr(
        runner,
        "_run_sophon_bmodel_eval",
        lambda *args, **kwargs: {"status": "ok", "model_tag": kwargs["model_tag"]},
    )
    monkeypatch.setattr(
        runner,
        "_run_python_test",
        lambda *args, **kwargs: {"status": "fail"},
    )

    result = runner._run_all_bmodel_evaluations(work, {}, demo_root=None)

    assert result["status"] == "fail"
    assert result["required_models"] == ["fp32", "fp16", "int8"]
    assert result["completed_models"] == ["int8"]
    assert result["missing_or_failed_models"] == ["fp32", "fp16"]
    assert result["usability_gate"]["reasons"] == ["required_model_missing_or_failed"]


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        (
            {"performance_enabled": False, "performance_model_tags": []},
            {"fp32": False, "fp16": False, "int8": False},
        ),
        (
            {"performance_enabled": True, "performance_model_tags": ["int8"]},
            {"fp32": False, "fp16": False, "int8": True},
        ),
        ({}, {"fp32": True, "fp16": True, "int8": True}),
    ],
)
def test_bmodel_performance_profile_selects_expected_variants(
    monkeypatch, tmp_path, cfg, expected
):
    runner = _load_runner()
    work = tmp_path / "work"
    model_root = work / "models" / "BM1688"
    model_root.mkdir(parents=True)
    for tag in ("fp32", "fp16", "int8"):
        (model_root / f"candidate_{tag}_1b.bmodel").write_bytes(tag.encode())

    captured = {}

    def _fake_eval(*args, **kwargs):
        tag = kwargs["model_tag"]
        captured[tag] = kwargs["performance_enabled"]
        return {"status": "ok", "model_tag": tag, "metrics": {"map50": 0.9}}

    monkeypatch.setattr(runner, "_run_sophon_bmodel_eval", _fake_eval)

    runner._run_all_bmodel_evaluations(work, cfg, demo_root=None)

    assert captured == expected


def test_generic_variant_gate_rejects_zero_int8_map50():
    runner = _load_runner()
    results = {
        "fp32": {"status": "ok", "metrics": {"map50": 0.96}},
        "fp16": {"status": "ok", "metrics": {"map50": 0.95}},
        "int8": {"status": "ok", "metrics": {"map50": 0.0}},
    }

    gate = runner._generic_variant_usability_gate(
        results, {"labeled_image_count": 50, "quality_rule": QUALITY_RULE}
    )

    assert gate["status"] == "fail"
    assert gate["reasons"] == ["int8_no_usable_detections"]
    assert gate["int8_map50_drop"] == 0.96


def test_generic_variant_gate_rejects_missing_ground_truth():
    runner = _load_runner()
    results = {
        "fp32": {"status": "ok", "metrics": {"map50": 0.96}},
        "fp16": {"status": "ok", "metrics": {"map50": 0.95}},
        "int8": {"status": "ok", "metrics": {"map50": 0.94}},
    }

    gate = runner._generic_variant_usability_gate(
        results,
        {
            "labeled_image_count": 0,
            "quality_rule": QUALITY_RULE,
        },
    )

    assert gate["status"] == "fail"
    assert gate["reasons"] == ["no_labeled_images"]


def test_generic_variant_gate_accepts_usable_int8_within_drop_limit():
    runner = _load_runner()
    results = {
        "fp32": {"status": "ok", "metrics": {"map50": 0.96}},
        "fp16": {"status": "ok", "metrics": {"map50": 0.95}},
        "int8": {"status": "ok", "metrics": {"map50": 0.945}},
    }

    gate = runner._generic_variant_usability_gate(
        results,
        {"labeled_image_count": 50, "quality_rule": QUALITY_RULE},
    )

    assert gate["status"] == "pass"
    assert gate["reasons"] == []
    assert round(gate["int8_map50_drop"], 6) == 0.015


def test_generic_variant_gate_uses_versioned_default_and_checks_fp16():
    runner = _load_runner()
    results = {
        "fp32": {"status": "ok", "metrics": {"map50": 0.96}},
        "fp16": {"status": "ok", "metrics": {"map50": 0.94}},
        "int8": {"status": "ok", "metrics": {"map50": 0.94}},
    }

    gate = runner._generic_variant_usability_gate(
        results, {"labeled_image_count": 50, "quality_rule": QUALITY_RULE}
    )

    assert gate["quality_rule_version"] == "bm1688-yolo11-detection-v1"
    assert gate["baseline_variant"] == "fp32"
    assert round(gate["fp16_map50_drop"], 6) == 0.02
    assert gate["status"] == "fail"
    assert gate["reasons"] == ["fp16_map50_drop_exceeded"]


def test_generic_variant_gate_rejects_legacy_threshold_without_snapshot():
    runner = _load_runner()
    results = {
        "fp32": {"status": "ok", "metrics": {"map50": 0.96}},
        "fp16": {"status": "ok", "metrics": {"map50": 0.95}},
        "int8": {"status": "ok", "metrics": {"map50": 0.95}},
    }

    gate = runner._generic_variant_usability_gate(
        results, {"labeled_image_count": 50, "max_int8_map50_drop": 0.05}
    )

    assert gate["status"] == "fail"
    assert gate["reasons"] == ["legacy_quality_rule_field"]


def test_fixed_eval_binding_rejects_manifest_tamper(tmp_path):
    runner = _load_runner()
    payload = {"schema_version": "fixed_eval.v1", "files": [{"path": "a.jpg", "size": 1, "sha256": "x"}]}
    import hashlib

    payload["manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    cfg = {
        "fixed_eval_signature": payload["manifest_sha256"],
        "require_fixed_eval_binding": True,
        "eval_image_count": 1,
    }
    payload["files"][0]["sha256"] = "tampered"
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    result = runner._fixed_eval_binding(tmp_path, cfg)

    assert result["passed"] is False
    assert "fixed_eval_manifest_tampered" in result["reasons"]


def test_fixed_eval_binding_requires_label_snapshot(tmp_path):
    runner = _load_runner()
    payload = {
        "schema_version": "fixed_eval.v1",
        "image_count": 1,
        "labeled_image_count": 0,
        "label_mappings": [],
        "files": [{"path": "images/a.jpg", "size": 1, "sha256": "x"}],
    }
    payload["manifest_sha256"] = __import__("hashlib").sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    result = runner._fixed_eval_binding(
        tmp_path,
        {
            "fixed_eval_signature": payload["manifest_sha256"],
            "require_fixed_eval_binding": True,
            "eval_image_count": 1,
            "labeled_image_count": 0,
        },
    )
    assert result["passed"] is False
    assert "fixed_eval_labeled_image_count_missing" in result["reasons"]
    assert "fixed_eval_labels_missing" in result["reasons"]


def test_fixed_eval_binding_accepts_selected_sample_pack_legacy_fields(tmp_path):
    runner = _load_runner()
    payload = {
        "image_count": 2,
        "labeled_count": 2,
        "file_mappings": [
            {"packed_name": "0.jpg", "has_annotation": True},
            {"packed_name": "1.jpg", "has_annotation": True},
        ],
    }
    payload["manifest_sha256"] = __import__("hashlib").sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    result = runner._fixed_eval_binding(
        tmp_path,
        {
            "fixed_eval_signature": payload["manifest_sha256"],
            "require_fixed_eval_binding": True,
            "eval_image_count": 2,
            "labeled_image_count": 2,
        },
    )

    assert result["passed"] is True


def test_fixed_eval_binding_accepts_canonical_image_label_mappings(tmp_path):
    runner = _load_runner()
    image = tmp_path / "images" / "0000.jpg"
    label = tmp_path / "labels" / "0000.txt"
    image.parent.mkdir()
    label.parent.mkdir()
    image.write_bytes(b"image")
    label.write_text("0 0.5 0.5 0.4 0.2\n", encoding="utf-8")
    payload = {
        "schema_version": "fixed_eval.v1",
        "image_count": 1,
        "labeled_image_count": 1,
        "label_mappings": [{"image": "images/0000.jpg", "path": "labels/0000.txt"}],
        "files": [
            {"path": "images/0000.jpg", "size": image.stat().st_size, "sha256": __import__("hashlib").sha256(image.read_bytes()).hexdigest()},
            {"path": "labels/0000.txt", "size": label.stat().st_size, "sha256": __import__("hashlib").sha256(label.read_bytes()).hexdigest()},
        ],
    }
    payload["manifest_sha256"] = __import__("hashlib").sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")

    result = runner._fixed_eval_binding(
        tmp_path,
        {
            "fixed_eval_signature": payload["manifest_sha256"],
            "require_fixed_eval_binding": True,
            "eval_image_count": 1,
            "labeled_image_count": 1,
        },
    )

    assert result["passed"] is True


def test_generic_variant_gate_rejects_invalid_precision_rule(tmp_path):
    runner = _load_runner()
    results = {
        "fp32": {"status": "ok", "metrics": {"map50": 0.96}},
        "fp16": {"status": "ok", "metrics": {"map50": 0.95}},
        "int8": {"status": "ok", "metrics": {"map50": 0.95}},
    }
    rule = {"version": "x", "edge": {"baseline_variant": "fp32", "variant_max_drop": {"fp16": 0.01, "int8": "bad"}}}

    gate = runner._generic_variant_usability_gate(
        results, {"labeled_image_count": 50, "quality_rule": rule}
    )

    assert gate["status"] == "fail"
    assert gate["reasons"] == ["quality_rule_int8_limit_invalid"]


def test_plate_recognition_evaluation_runs_combined_fp16_pipeline(
    monkeypatch, tmp_path
):
    runner = _load_runner()
    work = tmp_path / "work"
    model_dir = work / "models" / "BM1688"
    image_dir = work / "eval_data" / "images" / "test"
    validator = work / "scripts" / "plate_validation" / "validate.py"
    model_dir.mkdir(parents=True)
    image_dir.mkdir(parents=True)
    validator.parent.mkdir(parents=True)
    (model_dir / "plate_recognition_fp16_1b.bmodel").write_bytes(b"bmodel")
    (model_dir / "plate_recognition_contract.json").write_text(
        '{"format":"labelu_plate_recognition_bm1688_v1"}', encoding="utf-8"
    )
    (image_dir / "plate.jpg").write_bytes(b"image")
    validator.write_text("# validator", encoding="utf-8")
    captured = {}

    def _fake_run(cmd, cwd, env, timeout_sec):
        captured["cmd"] = cmd
        output_dir = Path(cmd[cmd.index("--output") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "summary.json").write_text(
            '{"status":"completed","runtime_ok":true,"model_type":"plate_recognition","total_detections":2}',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="completed", stderr="")

    monkeypatch.setattr(runner, "_subprocess_run_compat", _fake_run)

    result = runner._run_all_bmodel_evaluations(
        work,
        {"conf_thres": 0.25, "nms_thres": 0.7, "match_iou_thres": 0.5},
        demo_root=None,
    )

    assert result["status"] == "ok"
    assert result["required_models"] == ["fp16"]
    assert result["completed_models"] == ["fp16"]
    assert result["metrics"]["total_detections"] == 2
    assert "--model" in captured["cmd"]


def test_plate_detection_evaluation_runs_single_fp16_pipeline(monkeypatch, tmp_path):
    runner = _load_runner()
    work = tmp_path / "work"
    model_dir = work / "models" / "BM1688"
    image_dir = work / "eval_data" / "images" / "test"
    validator = work / "scripts" / "plate_validation" / "validate.py"
    model_dir.mkdir(parents=True)
    image_dir.mkdir(parents=True)
    validator.parent.mkdir(parents=True)
    (model_dir / "plate_detection_fp16_1b.bmodel").write_bytes(b"bmodel")
    (model_dir / "plate_detection_contract.json").write_text(
        '{"format":"labelu_plate_detection_bm1688_v1"}', encoding="utf-8"
    )
    (image_dir / "plate.jpg").write_bytes(b"image")
    validator.write_text("# validator", encoding="utf-8")
    captured = {}

    def _fake_run(cmd, cwd, env, timeout_sec):
        captured["cmd"] = cmd
        output_dir = Path(cmd[cmd.index("--output") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "summary.json").write_text(
            '{"status":"completed","runtime_ok":true,"model_type":"plate_detection","total_detections":3}',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="completed", stderr="")

    monkeypatch.setattr(runner, "_subprocess_run_compat", _fake_run)

    result = runner._run_all_bmodel_evaluations(
        work,
        {"conf_thres": 0.35, "nms_thres": 0.7, "match_iou_thres": 0.5},
        demo_root=None,
    )

    assert result["status"] == "ok"
    assert result["required_models"] == ["fp16"]
    assert result["completed_models"] == ["fp16"]
    assert result["metrics"]["model_type"] == "plate_detection"
    assert result["model_filename"] == "plate_detection_fp16_1b.bmodel"
    assert Path(captured["cmd"][captured["cmd"].index("--model") + 1]) == model_dir


def test_plate_detection_precision_evaluation_reads_real_runner_results(tmp_path):
    runner = _load_runner()
    work = tmp_path / "work"
    image_dir = work / "eval_data" / "images" / "test"
    xml_dir = work / "eval_data" / "labelsxml" / "test"
    out_dir = work / "output" / "results_bmodel"
    model_dir = work / "models" / "BM1688"
    for path in (image_dir, xml_dir, out_dir, model_dir):
        path.mkdir(parents=True, exist_ok=True)

    image_path = image_dir / "000000_plate.jpg"
    assert cv2.imwrite(str(image_path), np.zeros((100, 100, 3), dtype=np.uint8))
    (xml_dir / "000000_plate.xml").write_text(
        "<annotation><object><name>doubleEP</name><bndbox>"
        "<xmin>30</xmin><ymin>30</ymin><xmax>70</xmax><ymax>70</ymax>"
        "</bndbox></object></annotation>",
        encoding="utf-8",
    )
    contract_path = model_dir / "plate_detection_contract.json"
    contract_path.write_text(
        json.dumps({"network": {"class_names": ["doubleEP"]}}),
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(
        json.dumps({"inference": {"elapsed_seconds": 0.02}}), encoding="utf-8"
    )
    (out_dir / "results.json").write_text(
        json.dumps(
            {
                "format": "labelu_plate_detection_bm1688_result_v1",
                "images": [
                    {
                        "image": str(image_path),
                        "detections": [
                            {
                                "box": [30, 30, 70, 70],
                                "score": 0.99,
                                "label": "doubleEP",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    metrics = runner._plate_detection_precision_evaluation(
        work, image_dir, out_dir, contract_path, {"match_iou_thres": 0.5}
    )

    assert metrics["precision_eval_status"] == "computed"
    assert metrics["images"] == 1
    assert metrics["map50"] == 1.0
    assert metrics["map50_95"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["keypoint_metrics_status"] == "not_computed"
    assert (out_dir / "eval_summary.json").is_file()
    assert (out_dir / "evaluation_report.md").is_file()


def test_plate_specialized_runtime_is_authoritative_over_generic_demo(tmp_path):
    runner = _load_runner()
    work = tmp_path / "work"
    model_dir = work / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    (model_dir / "plate_detection_contract.json").write_text("{}", encoding="utf-8")

    outcome = runner._validation_outcome(
        work,
        mode="run_and_eval",
        report={"passed": False, "skipped": False},
        bmodel_eval={"status": "ok"},
        dependency_setup={"status": "ok"},
    )

    assert outcome["passed"] is True
    assert outcome["board_passed"] is True
    assert outcome["generic_board_passed"] is False
    assert outcome["validation_profile"] == "plate_detection"
    assert outcome["checks"]["generic_sophon_demo"]["status"] == "not_applicable"
    assert outcome["checks"]["specialized_runtime"]["authoritative"] is True


def test_generic_yolo_still_requires_generic_demo_success(tmp_path):
    runner = _load_runner()
    work = tmp_path / "work"
    (work / "models" / "BM1688").mkdir(parents=True)

    outcome = runner._validation_outcome(
        work,
        mode="run_and_eval",
        report={"passed": False, "skipped": False},
        bmodel_eval={"status": "ok"},
        dependency_setup={"status": "ok"},
    )

    assert outcome["passed"] is False
    assert outcome["validation_profile"] == "generic_yolo"
    assert outcome["checks"]["generic_sophon_demo"]["status"] == "fail"
