import json
from pathlib import Path

from labelu.scripts import edge_board_validate_runner as runner


def test_runner_uses_new_threshold_fields() -> None:
    cfg = {
        "conf_thres": 0.31,
        "nms_thres": 0.66,
        "match_iou_thres": 0.47,
    }
    assert runner._cfg_conf_thres(cfg) == 0.31
    assert runner._cfg_nms_thres(cfg) == 0.66
    assert runner._cfg_match_iou_thres(cfg) == 0.47


def test_runner_falls_back_to_legacy_iou_field() -> None:
    cfg = {"conf_thres": 0.29, "iou_thres": 0.58}
    assert runner._cfg_nms_thres(cfg) == 0.58
    assert runner._cfg_match_iou_thres(cfg) == 0.58


def test_runner_prefers_nested_threshold_contract_over_legacy_root_fields() -> None:
    cfg = {
        "conf_thres": 0.29,
        "nms_thres": 0.58,
        "threshold_unit": "legacy",
        "threshold_contract": {
            "threshold_unit": "ratio_0_1",
            "source": "ai_recommended",
            "conf_thres": 0.42,
            "nms_thres": 0.63,
            "match_iou_thres": 0.51,
        },
    }

    assert runner._cfg_conf_thres(cfg) == 0.42
    assert runner._cfg_nms_thres(cfg) == 0.63
    assert runner._cfg_match_iou_thres(cfg) == 0.51
    assert runner._cfg_threshold_unit(cfg) == "ratio_0_1"
    assert runner._cfg_threshold_source(cfg) == "ai_recommended"


def test_json_env_class_names_ascii_safe_for_py36_subprocess() -> None:
    names = ["餐厨垃圾", "其他垃圾"]
    payload = runner._json_env_class_names(names)
    assert payload.isascii()
    assert json.loads(payload) == names


def test_subprocess_env_py36_compatible_drops_non_ascii_inherited_vars() -> None:
    env = {"PATH": "/usr/bin", "EVAL_CLASS_NAMES": '["\\u9910\\u5385\\u5783\\u573e"]', "LC_ALL": "zh_CN.UTF-8"}
    safe = runner._subprocess_env_py36_compatible(env)
    if runner.sys.version_info >= (3, 7):
        assert safe == env
    else:
        assert "PATH" in safe
        assert "EVAL_CLASS_NAMES" in safe
        assert "LC_ALL" not in safe


def test_runner_run_only_skips_bmodel_eval(tmp_path, monkeypatch) -> None:
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "run_config.json").write_text(
        json.dumps({"validation_mode": "run_only"}, ensure_ascii=False),
        encoding="utf-8",
    )

    called = {"sophon": 0, "python_test": 0}

    monkeypatch.setattr(runner, "_resolve_sophon_demo_root", lambda: (None, "none"))
    monkeypatch.setattr(
        runner,
        "_skipped_board_report",
        lambda reason, tried: {
            "passed": False,
            "skipped": True,
            "summary": "skipped",
            "reason": reason,
            "tried_paths": tried,
        },
    )

    def _fake_sophon(*args, **kwargs):
        called["sophon"] += 1
        return {"status": "ok"}

    def _fake_python_test(*args, **kwargs):
        called["python_test"] += 1
        return {"status": "ok"}

    monkeypatch.setattr(runner, "_run_sophon_bmodel_eval", _fake_sophon)
    monkeypatch.setattr(runner, "_run_python_test", _fake_python_test)
    monkeypatch.setattr(
        runner.argparse.ArgumentParser,
        "parse_args",
        lambda self: type("Args", (), {"work_dir": str(work)})(),
    )

    code = runner.main()
    assert code == 0
    assert called["sophon"] == 0
    assert called["python_test"] == 0
    result = json.loads((work / "output" / "bmodel_sop_eval.json").read_text(encoding="utf-8"))
    assert result["status"] == "skip"
