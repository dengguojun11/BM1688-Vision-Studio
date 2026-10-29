from labelu.internal.common.bm1688_compile_contract import (
    build_bm1688_profile_snapshot,
    build_onnx_export_contract,
    evaluate_bm1688_compile_contract,
    profile_snapshot_digest,
)


def _write_full_artifacts(root, stem="yolov11n"):
    platform = root / "models" / "BM1688"
    platform.mkdir(parents=True)
    for precision in ("fp32", "fp16", "int8"):
        (platform / f"{stem}_{precision}_1b.bmodel").write_bytes(precision.encode())


def _base_kwargs(root, cali, *, mode="full"):
    onnx = root / "models" / "onnx" / "yolo11n.onnx"
    onnx.parent.mkdir(parents=True, exist_ok=True)
    onnx.write_bytes(b"onnx")
    return dict(
        model_stem="yolov11n",
        mode=mode,
        onnx_path=onnx,
        calibration_dir=cali,
        compiler_config={
            "compiler_version": "tpu-mlir-1.0",
            **build_onnx_export_contract(sophon_export=True, imgsz=640),
        },
        artifact_manifest={"execution_signature": "sig"},
        fixed_eval_evidence={"dataset_signature": "sealed-eval"},
    )


def test_sophon_export_contract_is_static_opset17():
    contract = build_onnx_export_contract(sophon_export=True, imgsz=640)
    assert contract == {
        "onnx_input_imgsz": 640,
        "onnx_opset": 17,
        "onnx_dynamic": False,
        "onnx_batch": 1,
        "onnx_simplify": True,
        "onnx_export_backend": "sophon",
    }


def test_full_compile_contract_requires_three_precisions_and_evidence(tmp_path):
    root = tmp_path / "export"
    cali = root / "calibration"
    cali.mkdir(parents=True)
    (cali / "sample.jpg").write_bytes(b"image")
    _write_full_artifacts(root)

    result = evaluate_bm1688_compile_contract(
        root,
        platforms=("bm1688",),
        **_base_kwargs(root, cali),
    )

    assert result["passed"] is True
    assert result["checks"]["platforms"]["bm1688"]["missing_precisions"] == []


def test_missing_int8_or_calibration_fails_closed(tmp_path):
    root = tmp_path / "export"
    cali = root / "calibration"
    cali.mkdir(parents=True)
    (cali / "sample.jpg").write_bytes(b"image")
    _write_full_artifacts(root)
    (root / "models" / "BM1688" / "yolov11n_int8_1b.bmodel").unlink()

    result = evaluate_bm1688_compile_contract(
        root,
        platforms=("bm1688",),
        **_base_kwargs(root, cali),
    )
    assert result["passed"] is False
    assert any(reason.startswith("bmodel_missing:") for reason in result["reasons"])

    for item in cali.iterdir():
        item.unlink()
    result = evaluate_bm1688_compile_contract(
        root,
        platforms=("bm1688",),
        **_base_kwargs(root, cali),
    )
    assert result["passed"] is False
    assert "calibration_set_missing" in result["reasons"]


def test_missing_fixed_eval_and_toolchain_version_is_not_release_ready(tmp_path):
    root = tmp_path / "export"
    cali = root / "calibration"
    cali.mkdir(parents=True)
    (cali / "sample.jpg").write_bytes(b"image")
    _write_full_artifacts(root)
    kwargs = _base_kwargs(root, cali)
    kwargs["compiler_config"] = {
        **build_onnx_export_contract(sophon_export=True, imgsz=640),
    }
    kwargs["fixed_eval_evidence"] = None

    result = evaluate_bm1688_compile_contract(
        root,
        platforms=("bm1688",),
        **kwargs,
    )
    assert result["passed"] is False
    assert "compiler_version_missing" in result["reasons"]
    assert "fixed_eval_missing" in result["reasons"]


def test_bm1688_profile_snapshot_is_versioned_and_stable(tmp_path):
    cali = tmp_path / "calibration"
    cali.mkdir()
    (cali / "a.jpg").write_bytes(b"image")
    qtable = tmp_path / "yolov11s_qtable"
    qtable.write_bytes(b"qtable")
    profile = build_bm1688_profile_snapshot(
        model_family="yolov11",
        model_stem="yolov11s",
        imgsz=640,
        compiler_config={
            "compiler_version": "tpu-mlir-test",
            **build_onnx_export_contract(sophon_export=True, imgsz=640),
        },
        class_names=["plate"],
        calibration_dir=cali,
        qtable_path=qtable,
    )

    assert profile["schema_version"] == "bm1688.profile.v1"
    assert profile["profile_id"] == "bm1688-yolov11-detection-v1"
    assert profile["model_stem"] == "yolov11s"
    assert profile["input"]["size"] == [640, 640]
    assert profile["output"]["layout"]
    assert profile["toolchain"]["tpu_mlir_version"] == "tpu-mlir-test"
    assert profile["calibration"]["sample_count"] == 1
    assert profile["calibration"]["qtable"]["sha256"]
    assert profile["profile_digest"] == profile_snapshot_digest(profile)


def test_compile_contract_blocks_profile_missing_or_mismatch(tmp_path):
    root = tmp_path / "export"
    cali = root / "calibration"
    cali.mkdir(parents=True)
    (cali / "sample.jpg").write_bytes(b"image")
    _write_full_artifacts(root)
    profile = build_bm1688_profile_snapshot(
        model_stem="yolov11n",
        compiler_config={
            "compiler_version": "tpu-mlir-test",
            **build_onnx_export_contract(sophon_export=True, imgsz=640),
        },
        calibration_dir=cali,
    )
    kwargs = _base_kwargs(root, cali)
    kwargs["compiler_config"] = {
        **kwargs["compiler_config"],
        "bm1688_profile": profile,
    }
    kwargs["bm1688_profile"] = profile
    kwargs["artifact_manifest"] = {
        "execution_signature": "sig",
        "bm1688_profile": profile,
    }
    assert evaluate_bm1688_compile_contract(root, platforms=("bm1688",), **kwargs)["checks"]["profile"]["passed"]

    kwargs["artifact_manifest"] = {"execution_signature": "sig"}
    result = evaluate_bm1688_compile_contract(root, platforms=("bm1688",), **kwargs)
    assert "profile:profile_missing_in_manifest" in result["reasons"]

    tampered = dict(profile)
    tampered["input"] = {**profile["input"], "size": [320, 320]}
    kwargs["artifact_manifest"] = {"execution_signature": "sig", "bm1688_profile": tampered}
    result = evaluate_bm1688_compile_contract(root, platforms=("bm1688",), **kwargs)
    assert "profile:profile_snapshot_mismatch" in result["reasons"]
