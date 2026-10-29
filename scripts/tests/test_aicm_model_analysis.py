from __future__ import annotations

import json

import pytest

from labelu.internal.application.service.aicm_model_analysis import analyze_models
from scripts.ops.analyze_aicm_model_catalog import export_analysis, extract_models


def model(identity, labels, created_at=1700000000, **extra):
    return {
        "id": identity,
        "name": f"模型{identity}",
        "created_at": created_at,
        "label_class_set_obj_info": [
            {"key": label, "name": f"中文{label}", "accuracy": 0.9} for label in labels
        ],
        **extra,
    }


def test_groups_ignore_order_accuracy_and_chinese_name_but_keep_original_order():
    rows = [model(10, ["a", "b"], 1700000030), model(2, ["b", "a"], 1700000010)]
    rows[1]["label_class_set_obj_info"][0].update(name="别名", accuracy=0.1)
    before = json.dumps(rows, ensure_ascii=False)
    analysis = analyze_models(rows)
    assert json.dumps(rows, ensure_ascii=False) == before
    assert analysis["summary"]["identical_label_group_count"] == 1
    assert analysis["summary"]["order_mismatch_group_count"] == 1
    assert analysis["label_groups"][0]["model_ids"] == [2, 10]
    assert [entry["version_label"] for entry in analysis["models"]] == ["v2", "v1"]
    assert analysis["models"][0]["label_group_size"] == 2
    assert analysis["models"][0]["label_group_version"] == "v2"
    assert analysis["models"][0]["labels"] == ["a", "b"]


def test_shared_pairs_count_once_even_when_more_than_one_label_is_shared():
    analysis = analyze_models(
        [
            model(1, ["a", "b"]),
            model(2, ["b", "a"]),
            model(3, ["b", "c"]),
            model(4, ["d"]),
            model(5, []),
        ]
    )
    assert analysis["summary"]["shared_label_model_count"] == 3
    assert analysis["summary"]["related_pair_count"] == 3
    assert analysis["summary"]["identical_pair_count"] == 1
    assert analysis["summary"]["partial_overlap_pair_count"] == 2
    assert analysis["summary"]["models_in_identical_groups"] == 2
    assert analysis["summary"]["extra_versions"] == 1
    assert analysis["summary"]["unlabeled_model_count"] == 1
    assert analysis["models"][0]["related_model_count"] == 2
    assert analysis["models"][0]["partial_overlap_model_count"] == 1
    assert analysis["models"][-1]["version"] is None
    assert analysis["models"][-1]["label_group_size"] == 0


def test_publication_time_falls_back_to_creation_and_numeric_ids_break_ties():
    analysis = analyze_models(
        [
            model("10", ["a"], 1700000000, published_at=0),
            model("2", ["a"], "2023-11-14T22:13:20Z", published_at="invalid"),
            model(3, ["a"], 1, published_at=1700000001000),
            model(1, ["a"], None, published_at=-2),
        ]
    )
    assert analysis["label_groups"][0]["model_ids"] == ["2", "10", 3, 1]
    assert analysis["models"][0]["version_time_source"] == "created_at"
    assert analysis["models"][2]["version_time_source"] == "published_at"
    assert analysis["summary"]["undated_model_count"] == 1
    assert analysis["models"][0]["version_time"] == "2023-11-15T06:13:20+08:00"
    assert analysis["summary"]["published_time_count"] == 1
    assert analysis["summary"]["created_time_fallback_count"] == 2


def test_source_publication_and_download_flags_do_not_invent_publication_time():
    analysis = analyze_models(
        [
            model(
                1,
                ["a"],
                published_at=0,
                in_use=True,
                zip_download_url="http://example.invalid/file.zip",
            ),
            model(2, ["a"], published_at=float("nan"), in_use=False),
            model(3, ["a"], published_at=float("inf"), in_use="true"),
        ]
    )
    assert analysis["summary"]["published_time_count"] == 0
    assert analysis["summary"]["created_time_fallback_count"] == 3
    assert analysis["summary"]["zip_download_url_count"] == 1
    assert analysis["summary"]["in_use_count"] == 2


@pytest.mark.parametrize(
    "evolution",
    [
        [
            {"id": 4, "name": "基础模型"},
            {"id": 605, "download_url": "http://example.invalid/quant.zip"},
        ],
        {"download_url": "https://example.invalid/quant.ZIP?token=placeholder"},
        {"list": [{"zip_download_url": "https://example.invalid/download/605"}]},
    ],
)
def test_compiled_zip_downloads_are_found_in_evolution_list_and_object(evolution):
    analysis = analyze_models([model(605, ["a"], evolution_set_obj_info=evolution)])
    assert analysis["models"][0]["has_zip_download_url"] is True
    assert analysis["summary"]["zip_download_url_count"] == 1


def test_other_artifacts_and_missing_evolution_are_not_compiled_zip_downloads():
    analysis = analyze_models(
        [
            model(
                1,
                ["a"],
                evolution_set_obj_info=[
                    {"download_url": "https://example.invalid/model.pt"}
                ],
            ),
            model(2, ["a"], evolution_set_obj_info=None),
            model(3, ["a"], evolution_set_obj_info=[{}, "ignored"]),
        ]
    )
    assert analysis["summary"]["zip_download_url_count"] == 0


def test_empty_labels_are_not_family_and_duplicate_keys_do_not_change_set():
    analysis = analyze_models(
        [
            model(1, []),
            model(2, []),
            model(3, ["a", "a"]),
            model(4, ["a"]),
            model(5, ["A"]),
        ]
    )
    assert analysis["summary"]["unlabeled_model_count"] == 2
    assert analysis["summary"]["label_group_count"] == 2
    assert analysis["summary"]["identical_label_group_count"] == 1
    assert analysis["summary"]["related_pair_count"] == 1
    assert analysis["models"][2]["labels"] == ["a"]


def test_name_fallback_and_duplicate_source_ids_are_visible():
    analysis = analyze_models(
        [
            {"id": 1, "name": "一", "label_class_set_obj_info": [{"name": "person"}]},
            {"id": 1, "label_class_set_obj_info": [{"key": "person", "name": "人"}]},
        ]
    )
    assert analysis["summary"]["duplicate_model_id_count"] == 1
    assert analysis["summary"]["identical_pair_count"] == 1
    assert {warning["code"] for warning in analysis["warnings"]} >= {
        "label_key_missing",
        "duplicate_model_ids",
        "model_time_missing",
    }


def test_report_contains_all_models_and_guards_spreadsheet_formula(tmp_path):
    rows = [model(index, ["a", f"label{index}"]) for index in range(123)]
    rows[-1]["name"] = "=危险公式"
    analysis = export_analysis(rows, tmp_path)
    report = (tmp_path / "AICM模型全量分析报告.md").read_text(encoding="utf-8")
    assert "模型122" not in report
    assert "危险公式" in report
    assert analysis["summary"]["total_models"] == 123
    assert "'=" in (tmp_path / "全部模型.csv").read_text(encoding="utf-8-sig")
    saved = json.loads((tmp_path / "模型标签分析.json").read_text(encoding="utf-8"))
    assert len(saved["models"]) == 123
    assert len(saved["label_groups"]) == 123
    assert saved["summary"]["related_pair_count"] == 123 * 122 // 2


def test_snapshot_report_records_evidence_without_claiming_it_refetched_remote(
    tmp_path,
):
    export_analysis(
        [model(1, ["a"])],
        tmp_path,
        source_info={
            "mode": "input",
            "input_path": "snapshot.json",
            "snapshot_sha256": "known-hash",
        },
    )
    report = (tmp_path / "AICM模型全量分析报告.md").read_text(encoding="utf-8")
    assert "本次生成报告未重新请求远端" in report
    assert "known-hash" in report
    assert "本脚本不联网" not in report


@pytest.mark.parametrize(
    "wrapper",
    [
        lambda rows: rows,
        lambda rows: {"data": {"list": rows}},
        lambda rows: {"results": rows},
        lambda rows: {"data": rows},
    ],
)
def test_snapshot_api_envelopes(wrapper):
    rows = [model(1, ["a"])]
    assert extract_models(wrapper(rows)) == rows


def test_invalid_snapshot_is_not_silently_an_empty_remote_catalogue():
    with pytest.raises(ValueError, match="没有找到模型列表"):
        extract_models({"data": {"total": 500}})
    with pytest.raises(ValueError, match="必须是对象"):
        extract_models(["bad"])
