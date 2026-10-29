"""一期算法与芯片执行边界。"""

import pytest

from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.phase_one_contract import (
    ensure_phase_one_bm1688_platforms,
    ensure_phase_one_training_job_type,
)


@pytest.mark.parametrize("job_type", ["yolo_detection", "plate_detection", "plate_recognition"])
def test_phase_one_keeps_yolo_compatible_training_types(job_type):
    assert ensure_phase_one_training_job_type(job_type) == job_type


@pytest.mark.parametrize("job_type", ["classification", "segmentation", "pose", "custom_container"])
def test_phase_one_rejects_second_phase_training_types(job_type):
    with pytest.raises(LabelUException, match="一期暂不支持算法执行类型"):
        ensure_phase_one_training_job_type(job_type)


def test_phase_one_compile_target_is_bm1688_only():
    assert ensure_phase_one_bm1688_platforms("bm1688") == ("bm1688",)
    assert ensure_phase_one_bm1688_platforms(["BM1688"]) == ("bm1688",)
    with pytest.raises(LabelUException, match="一期编译目标仅支持 BM1688"):
        ensure_phase_one_bm1688_platforms("bm1688,bm1684x")
