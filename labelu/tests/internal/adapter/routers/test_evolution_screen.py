import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.adapter.routers import evolution_screen as evolution_router
from labelu.internal.application.service.evolution_screen import _canonical_gate_status
from labelu.internal.domain.models.auto_training import AutoTrainingPlan
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _task(db: Session, user_id: int = 1, name: str = "道路缺陷") -> Task:
    row = Task(
        name=name,
        media_type="IMAGE",
        status="FINISHED",
        created_by=user_id,
        updated_by=user_id,
        source_type="internal",
    )
    db.add(row)
    db.flush()
    return row


def _job(db: Session, task: Task, **overrides) -> YoloTrainJob:
    values = {
        "task_id": task.id,
        "created_by": task.created_by,
        "model_weights": "yolo11n.pt",
        "epochs": 50,
        "imgsz": 640,
        "category": "yolo_detection",
        "status": "COMPLETED",
        "hyperparams_json": json.dumps({"algorithm_key": "yolo_detection"}),
        "metrics_summary": json.dumps({
            "best_pt_val_metrics": {
                "epoch": 40,
                "map50_95": 0.61,
                "map50": 0.84,
                "precision": 0.82,
                "recall": 0.79,
                "fitness": 0.635,
            }
        }),
    }
    values.update(overrides)
    row = YoloTrainJob(**values)
    db.add(row)
    db.flush()
    return row


def test_overview_projects_real_job_chain_and_ai_decision(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    task = _task(db)
    parent = _job(db, task)
    child = _job(
        db,
        task,
        model_weights=f"trained:{parent.id}",
        status="PROCESSING",
        metrics_summary=json.dumps({"last_epoch_metrics": {"epoch": 17, "map50_95": 0.65}}),
        hyperparams_json=json.dumps({"current_epoch": 17}),
    )
    db.add(YoloEvalReport(
        yolo_train_job_id=parent.id,
        provider_key="volcengine",
        report_kind="next_iteration",
        body="召回率仍有提升空间，建议继续训练。",
        suggested_params=json.dumps({"epochs": 80, "lr0": 0.005}),
    ))
    db.commit()

    response = client.get("/api/v1/evolution/screen/overview", headers=testuser_token_headers)

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["summary"]["lineage_count"] == 1
    assert payload["summary"]["active_training_count"] == 1
    assert payload["summary"]["autonomous_decision_count"] == 1
    assert payload["lineages"][0]["id"] == f"job-chain:{parent.id}"
    assert payload["lineages"][0]["round_count"] == 2
    assert payload["active_jobs"][0]["job_id"] == child.id
    assert payload["active_jobs"][0]["progress"]["percent"] == 34
    assert payload["decisions"][0]["suggested_params"]["epochs"] == 80


def test_lineage_detail_contains_iteration_nodes_and_edges(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    task = _task(db)
    parent = _job(db, task)
    child = _job(db, task, model_weights=f"trained:{parent.id}")
    db.commit()

    response = client.get(
        f"/api/v1/evolution/lineages/job-chain:{parent.id}",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert [node["job_id"] for node in payload["nodes"]] == [parent.id, child.id]
    assert payload["nodes"][0]["quality_gate"]["training"] == "PASS"
    assert payload["edges"] == [{
        "source": f"job:{parent.id}", "target": f"job:{child.id}", "type": "ITERATION"
    }]


def test_plan_without_training_job_is_still_visible(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    db.add(AutoTrainingPlan(
        owner_id=1,
        name="安全帽持续进化",
        series_type="yolo_detection",
        dataset_category_id=8,
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
        status="WAITING_FOR_DATA",
        version=1,
        created_by=1,
        updated_by=1,
    ))
    db.commit()

    response = client.get("/api/v1/evolution/lineages", headers=testuser_token_headers)

    assert response.status_code == 200, response.text
    rows = response.json()["data"]
    assert len(rows) == 1
    assert rows[0]["source_type"] == "PLAN"
    assert rows[0]["status"] == "ATTENTION"
    assert rows[0]["round_count"] == 0

    filtered = client.get(
        "/api/v1/evolution/lineages",
        params={"q": "安全帽"},
        headers=testuser_token_headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["meta_data"]["total"] == 1
    assert filtered.json()["data"][0]["name"] == "安全帽持续进化"


def test_evolution_screen_does_not_leak_other_users_jobs(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    task = _task(db, user_id=999, name="其他用户任务")
    _job(db, task)
    db.commit()

    overview = client.get("/api/v1/evolution/screen/overview", headers=testuser_token_headers)
    detail = client.get(f"/api/v1/evolution/lineages/job-chain:1", headers=testuser_token_headers)

    assert overview.status_code == 200
    assert overview.json()["data"]["summary"]["lineage_count"] == 0
    assert detail.status_code == 404


def test_incremental_metrics_apply_epoch_cursor(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session, monkeypatch
):
    task = _task(db)
    job = _job(db, task)
    db.commit()
    monkeypatch.setattr(
        evolution_router,
        "parse_results_csv_history",
        lambda _path: [{"epoch": 9, "map50": 0.7}, {"epoch": 10, "map50": 0.72}],
    )
    monkeypatch.setattr(evolution_router, "train_job_work_train_dir", lambda _job_id: __import__("pathlib").Path("."))

    response = client.get(
        f"/api/v1/training/jobs/{job.id}/metrics",
        params={"after_epoch": 8, "limit": 1},
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["history"] == [{"epoch": 9, "map50": 0.7}]
    assert payload["next_after_epoch"] == 9
    assert payload["has_more"] is True


def test_quality_gate_statuses_share_one_screen_contract():
    assert _canonical_gate_status("PASS") == "PASS"
    assert _canonical_gate_status("PASSED") == "PASS"
    assert _canonical_gate_status("FAIL") == "FAIL"
    assert _canonical_gate_status("BLOCKED") == "FAIL"
    assert _canonical_gate_status("WAITING") == "PENDING"
