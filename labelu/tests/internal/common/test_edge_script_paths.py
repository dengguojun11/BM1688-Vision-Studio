"""边端验证脚本路径解析。"""

from pathlib import Path

from labelu.internal.common.edge_script_paths import (
    resolve_edge_bmodel_validator,
    resolve_edge_board_validate_runner,
    resolve_evaluation_utils,
    resolve_plate_detection_bm1688_infer,
    resolve_plate_recognition_bm1688_infer,
    resolve_python_test_evaluation,
)


def test_resolve_edge_board_validate_runner_under_labelu_scripts():
    runner = resolve_edge_board_validate_runner()
    assert runner.name == "edge_board_validate_runner.py"
    assert runner.is_file()
    assert runner.parent.name == "scripts"
    assert runner.parent.parent.name == "labelu"


def test_resolve_python_test_evaluation():
    main_py = resolve_python_test_evaluation()
    assert main_py.name == "pythonTest_evaluation.py"
    assert main_py.is_file()


def test_resolve_evaluation_utils():
    utils_py = resolve_evaluation_utils()
    assert utils_py.name == "evaluation_utils.py"
    assert utils_py.is_file()


def test_resolve_plate_recognition_edge_validation_scripts():
    validator = resolve_edge_bmodel_validator()
    runner = resolve_plate_recognition_bm1688_infer()
    assert validator.name == "validate.py"
    assert validator.is_file()
    assert validator.parent.name == "edge_bmodel_validator"
    assert runner.name == "plate_recognition_bm1688_infer.py"
    assert runner.is_file()


def test_resolve_plate_detection_edge_validation_runner():
    runner = resolve_plate_detection_bm1688_infer()
    assert runner.name == "plate_detection_bm1688_infer.py"
    assert runner.is_file()


def test_checklist_runner_not_repo_root_scripts(monkeypatch):
    from labelu.internal.common import config
    from labelu.internal.common.edge_ssh_deploy_checklist import (
        iter_deploy_checklist_lines,
    )

    monkeypatch.setattr(config.settings, "YOLO_TRAIN_ENABLED", True)
    lines = iter_deploy_checklist_lines(probe_remote=False)
    runner_line = next(ln for ln in lines if "边端验证 runner 脚本" in ln)
    assert runner_line.startswith("ok|")
    detail = runner_line.split("|", 2)[2]
    assert "/labelu/scripts/edge_board_validate_runner.py" in detail.replace("\\", "/")
    assert detail.replace("\\", "/").endswith(
        "/labelu/scripts/edge_board_validate_runner.py"
    )
