import importlib.util
from pathlib import Path

import numpy as np


def _load_module():
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "plate_recognition_bm1688_infer.py"
    )
    spec = importlib.util.spec_from_file_location("plate_bm1688_infer", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_decode_pose_restores_box_and_four_corners():
    module = _load_module()
    raw = np.zeros((1, 17, 2), dtype=np.float32)
    raw[0, :5, 0] = [160, 160, 100, 40, 0.9]
    corners = [(110, 140), (210, 140), (210, 180), (110, 180)]
    for index, (x, y) in enumerate(corners):
        raw[0, 5 + index * 3 : 8 + index * 3, 0] = [x, y, 0.8]

    decoded = module.decode_pose(
        raw,
        image_shape=(320, 320, 3),
        ratio=1.0,
        pad=(0.0, 0.0),
        confidence=0.25,
        iou_threshold=0.5,
    )

    assert len(decoded) == 1
    assert decoded[0]["box"] == [110.0, 140.0, 210.0, 180.0]
    assert decoded[0]["corners"] == corners


def test_ctc_decode_removes_blank_and_repeated_indices():
    module = _load_module()
    logits = np.zeros((1, 7, 4), dtype=np.float32)
    for position, index in enumerate([0, 1, 1, 0, 2, 2, 3]):
        logits[0, position, index] = 10

    assert module.ctc_decode(logits, ["A", "B", "C"]) == "ABC"


def test_select_ocr_candidate_uses_preferred_length_with_confidence_tolerance():
    module = _load_module()
    candidates = [
        {"text": "72353", "confidence": 0.98, "crop_ratio": 0.5},
        {"text": "723530", "confidence": 0.96, "crop_ratio": 0.45},
    ]

    selected = module.select_ocr_candidate(
        candidates, preferred_length=6, tolerance=0.1
    )

    assert selected["text"] == "723530"


def test_primary_ocr_candidate_requests_fallback_for_uncertain_short_result():
    module = _load_module()
    candidates = [
        {"text": "7239530", "confidence": 0.89, "crop_ratio": 0.45},
        {"text": "72353", "confidence": 0.9753, "crop_ratio": 0.5},
        {"text": "7530", "confidence": 0.82, "crop_ratio": 0.55},
    ]

    selected, needs_fallback = module.select_primary_ocr_candidate(
        candidates,
        preferred_length=6,
        tolerance=0.1,
        anchor_ratio=0.5,
        preserve_confidence=0.99,
    )

    assert selected["text"] == "72353"
    assert needs_fallback is True


def test_primary_ocr_candidate_preserves_high_confidence_non_preferred_length():
    module = _load_module()
    candidates = [
        {"text": "88106H", "confidence": 0.95, "crop_ratio": 0.45},
        {"text": "8816H", "confidence": 0.999, "crop_ratio": 0.5},
        {"text": "8816", "confidence": 0.95, "crop_ratio": 0.55},
    ]

    selected, needs_fallback = module.select_primary_ocr_candidate(
        candidates,
        preferred_length=6,
        tolerance=0.1,
        anchor_ratio=0.5,
        preserve_confidence=0.99,
    )

    assert selected["text"] == "8816H"
    assert needs_fallback is False


def test_ocr_fallback_selects_near_confidence_preferred_length():
    module = _load_module()
    selected = {"text": "72353", "confidence": 0.9753, "crop_ratio": 0.5}
    fallback = [{"text": "723530", "confidence": 0.9747, "crop_ratio": 0.47}]

    result = module.select_ocr_fallback_candidate(
        selected,
        fallback,
        preferred_length=6,
        tolerance=0.1,
    )

    assert result["text"] == "723530"


def test_guarded_consensus_uses_repeated_anchor_group():
    module = _load_module()
    candidates = [
        {"text": "3778V0", "confidence": 0.911, "crop_ratio": 0.45},
        {"text": "3778V0", "confidence": 0.898, "crop_ratio": 0.5},
        {"text": "377VV0", "confidence": 0.982, "crop_ratio": 0.55},
    ]

    selected = module.select_guarded_consensus_candidate(
        candidates[2], candidates, preferred_length=6
    )

    assert selected["text"] == "3778V0"


def test_guarded_consensus_rejects_weak_non_anchor_group():
    module = _load_module()
    candidates = [
        {"text": "2837H8", "confidence": 0.935, "crop_ratio": 0.45},
        {"text": "2837P8", "confidence": 0.966, "crop_ratio": 0.5},
        {"text": "2837H8", "confidence": 0.916, "crop_ratio": 0.55},
    ]

    selected = module.select_guarded_consensus_candidate(
        candidates[1], candidates, preferred_length=6
    )

    assert selected["text"] == "2837P8"


def test_guarded_consensus_repairs_non_preferred_selected_length():
    module = _load_module()
    candidates = [
        {"text": "893H55", "confidence": 0.744, "crop_ratio": 0.45},
        {"text": "839H65", "confidence": 0.848, "crop_ratio": 0.5},
        {"text": "839H5", "confidence": 0.993, "crop_ratio": 0.55},
    ]

    selected = module.select_guarded_consensus_candidate(
        candidates[2], candidates, preferred_length=6
    )

    assert selected["text"] == "839H65"


def test_guarded_consensus_preserves_high_confidence_anchor_length():
    module = _load_module()
    candidates = [
        {"text": "88106H", "confidence": 0.986, "crop_ratio": 0.45},
        {"text": "8816H", "confidence": 0.999, "crop_ratio": 0.5},
        {"text": "8816", "confidence": 0.927, "crop_ratio": 0.55},
    ]

    selected = module.select_guarded_consensus_candidate(
        candidates[1], candidates, preferred_length=6
    )

    assert selected["text"] == "8816H"


def test_enhance_ocr_image_returns_three_equal_channels():
    module = _load_module()
    image = np.zeros((48, 192, 3), dtype=np.uint8)
    image[:, :, 1] = 128

    enhanced = module.enhance_ocr_image(image)

    assert enhanced.shape == image.shape
    assert np.array_equal(enhanced[:, :, 0], enhanced[:, :, 1])
    assert np.array_equal(enhanced[:, :, 1], enhanced[:, :, 2])


def test_double_row_image_keeps_only_lower_number_row():
    module = _load_module()
    plate = np.zeros((40, 80, 3), dtype=np.uint8)
    plate[20:] = 200

    number_row = module.double_row_image(plate)

    assert number_row.shape == (48, 192, 3)
    assert number_row.mean() == 200


def test_order_corners_returns_clockwise_starting_at_top_left():
    module = _load_module()
    points = [(90, 60), (10, 10), (10, 50), (100, 20)]

    ordered = module.order_corners(points)

    assert ordered.tolist() == [
        [10.0, 10.0],
        [100.0, 20.0],
        [90.0, 60.0],
        [10.0, 50.0],
    ]


def test_perspective_crop_preserves_contract_order_for_steep_rotation():
    module = _load_module()
    image = np.zeros((120, 120, 3), dtype=np.uint8)
    corners = [(20, 10), (70, 45), (60, 110), (10, 70)]

    crop = module.perspective_crop(image, corners)

    assert crop.shape[0] > crop.shape[1]


def test_infer_rejects_degenerate_corners_without_aborting(monkeypatch):
    module = _load_module()
    engine = object.__new__(module.PlateRecognitionEngine)
    engine.contract = {
        "networks": {
            "plate_ocr": {"characters": ["A"]},
            "plate_color": {"output": {"classes": ["white"]}},
            "plate_type": {"output": {"classes": ["whitePlate"]}},
        }
    }
    raw = np.zeros((1, 17, 1), dtype=np.float32)
    raw[0, :5, 0] = [50, 50, 20, 10, 0.9]
    for index in range(4):
        raw[0, 5 + index * 3 : 8 + index * 3, 0] = [50, 50, 0.8]
    monkeypatch.setattr(
        engine,
        "_run",
        lambda *_args, **_kwargs: (raw, (1.0, 1.0), (0.0, 0.0), 1.0),
    )

    result = engine.infer(np.zeros((100, 100, 3), dtype=np.uint8), confidence=0.1)

    assert result["detections"] == []
    assert result["rejected_predictions"][0]["rejected_reason"] == (
        "predicted plate corners are not unique"
    )
