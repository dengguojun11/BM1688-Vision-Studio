from labelu.internal.application.service.yolo_bm1688_export import (
    _current_verdict_label,
    _edge_int8_eval_label,
)


def test_edge_int8_eval_label_reads_manual_edge_validation_result():
    row = type("Row", (), {"status": "COMPLETED", "quality_report": {"sop_eval": {"pt": {"metrics": {"map50": 0.6721}}}}})()
    board_validation = type(
        "BV",
        (),
        {
            "status": "COMPLETED",
            "report": {
                "result": {
                    "eval_summary": {
                        "status": "ok",
                        "map50": 0.6795,
                        "map50_95": 0.6795,
                        "precision": 0.7361,
                        "recall": 0.6578,
                        "images": 957,
                    },
                    "analysis": {
                        "summary": "bmodel vs pt mAP50 差 0.0074 (1.10%)，文档阈值 ≤ 5%",
                        "sources": {
                            "bmodel_report": "/tmp/results_bmodel/evaluation_report.md",
                        },
                    },
                }
            },
        },
    )()

    assert _edge_int8_eval_label(row, board_validation) == "通过"
    assert _current_verdict_label(row, "达标", board_validation) == "可进入发布评估"
