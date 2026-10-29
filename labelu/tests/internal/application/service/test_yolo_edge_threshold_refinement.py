from types import SimpleNamespace

from labelu.internal.application.command.yolo_edge_validation import (
    RefineEdgeThresholdsCommand,
)
from labelu.internal.application.service import (
    yolo_edge_threshold_refinement as svc,
)


def test_build_edge_threshold_refinement_context_contains_edge_result_summary() -> None:
    row = SimpleNamespace(
        id=12,
        export_id=34,
        task_id=56,
        mode="run_and_eval",
        status="COMPLETED",
        threshold_source="ai_recommended",
        image_count=4,
        labeled_image_count=3,
        unlabeled_image_count=1,
        thresholds_json='{"conf_thres":0.31,"nms_thres":0.68,"match_iou_thres":0.5,"source":"ai_recommended"}',
        ai_thresholds_json='{"conf_thres":0.31,"nms_thres":0.68,"match_iou_thres":0.5}',
    )
    request_payload = {"sample_ids": [1, 2], "attachment_ids": [901]}
    result_json = {
        "pack": {"copied_count": 4, "labeled_count": 3, "unlabeled_count": 1},
        "execution": {
            "status": "ok",
            "remote_stdout_tail": "done",
            "remote_stderr_tail": "",
        },
        "execution_summary": {
            "status": "ok",
            "remote_exit_code": 0,
            "files_pulled": 12,
            "has_board_report": True,
            "has_bmcv_infer": True,
            "has_bmodel_eval": True,
        },
        "prediction_summary": {
            "image_count": 4,
            "with_boxes": 3,
            "total_boxes": 8,
            "avg_boxes_per_image": 2.0,
        },
        "eval_summary": {
            "status": "ok",
            "map50": 0.81,
            "map50_95": 0.53,
            "precision": 0.76,
            "recall": 0.72,
            "images": 3,
        },
    }

    user_text, context = svc.build_edge_threshold_refinement_context(
        row=row,
        request_payload=request_payload,
        result_json=result_json,
    )

    assert context["job_id"] == 12
    assert context["base_thresholds"]["conf_thres"] == 0.31
    assert context["execution_summary"]["files_pulled"] == 12
    assert context["prediction_summary"]["total_boxes"] == 8
    assert context["eval_summary"]["map50"] == 0.81
    assert "真实边端验证摘要" in user_text


def test_refine_thresholds_returns_delta_summary(db, monkeypatch) -> None:
    row = SimpleNamespace(
        id=18,
        export_id=101,
        task_id=202,
        mode="run_and_eval",
        status="COMPLETED",
        threshold_source="manual",
        image_count=3,
        labeled_image_count=2,
        unlabeled_image_count=1,
        thresholds_json='{"conf_thres":0.30,"nms_thres":0.70,"match_iou_thres":0.50,"source":"manual"}',
        ai_thresholds_json=None,
        result_json='{"request":{"sample_ids":[1],"attachment_ids":[9]},"execution_summary":{"status":"ok"},"prediction_summary":{"image_count":3,"with_boxes":2,"total_boxes":5},"eval_summary":{"status":"ok","map50":0.80}}',
    )
    task = SimpleNamespace(id=202, name="edge task")

    monkeypatch.setattr(svc, "_load_job_with_access", lambda db, job_id, current_user: (row, task))
    monkeypatch.setattr(svc, "_build_provider_specs", lambda: [("qwen_plus", "http://x", "k", "m")])
    monkeypatch.setattr(svc, "_load_refinement_images", lambda db, task_id, request_payload, max_images: [])
    monkeypatch.setattr(
        svc,
        "request_edge_threshold_recommendation",
        lambda **kwargs: SimpleNamespace(
            recommended_thresholds=svc.EdgeThresholdsResponse(
                conf_thres=0.27,
                nms_thres=0.66,
                match_iou_thres=0.52,
                source="ai_recommended",
                reason="基于实测结果下调 conf 并收紧 nms",
                confidence_level="high",
                provider_key="qwen_plus",
            ),
            fallback_used=False,
        ),
    )

    data = svc.refine_thresholds(
        db,
        job_id=18,
        cmd=RefineEdgeThresholdsCommand(max_images=4, include_visual_evidence=False),
        current_user=SimpleNamespace(id=1),
    )

    assert data.stage == "post_validation_refine"
    assert data.base_thresholds.conf_thres == 0.30
    assert data.refined_thresholds.source == "ai_refined"
    assert data.delta_summary["conf_delta"] == -0.03
    assert data.delta_summary["nms_delta"] == -0.04
    assert data.fallback_used is False
