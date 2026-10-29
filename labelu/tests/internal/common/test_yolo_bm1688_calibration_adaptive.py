from labelu.internal.common.yolo_bm1688_calibration import (
    compute_adaptive_calibration_count,
    resolve_calibration_count,
)


def test_adaptive_formula_examples():
    assert compute_adaptive_calibration_count(50) == 50
    assert compute_adaptive_calibration_count(200) == 128
    assert compute_adaptive_calibration_count(7000) == 350
    assert compute_adaptive_calibration_count(50000) == 1000


def test_resolve_fixed_mode_caps_by_train():
    cfg = {"calibration_count_mode": "fixed", "cali_input_num": 500}
    assert resolve_calibration_count(200, cfg) == 200
