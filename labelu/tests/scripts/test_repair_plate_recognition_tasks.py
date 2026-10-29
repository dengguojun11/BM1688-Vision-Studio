from __future__ import annotations

import json

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_sample, crud_task, crud_user
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.plate_recognition_profile import task_config_matches_profile
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from scripts.repair_plate_recognition_tasks import repair_plate_recognition_tasks


def _historical_sample_data() -> str:
    return json.dumps(
        {
            "result": json.dumps(
                {
                    "rectTool": {
                        "result": [
                            {
                                "label": "doebleEP",
                                "attributes": {
                                    "plateNumber": "A12345",
                                    "color": ["green"],
                                    " plateType": [" greenPlate"],
                                },
                            },
                            {"label": "doubleEP"},
                        ]
                    },
                    "polygonTool": {"result": [{"label": " platePoint"}]},
                },
                ensure_ascii=False,
            )
        },
        ensure_ascii=False,
    )


def test_repair_plate_tasks_supports_preview_then_apply(db: Session) -> None:
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    original_config = json.dumps(
        {
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [{"key": "双层车牌", "value": "doebleEP"}]
                    },
                }
            ]
        },
        ensure_ascii=False,
    )
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="historical plate task",
                config=original_config,
                media_type="IMAGE",
                training_ready=True,
                annotation_audit_json='{"training_ready":true}',
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        samples = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=1,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                    data=_historical_sample_data(),
                    annotated_count=3,
                    state="DONE",
                )
            ],
        )

    with begin_transaction(db):
        preview = repair_plate_recognition_tasks(
            db, task_ids=[task.id], apply_changes=False
        )
    assert preview[0]["config_changed"] is True
    assert preview[0]["sample_changed"] == 1
    assert preview[0]["rect_labels"] == 1
    assert preview[0]["polygon_labels"] == 1
    assert preview[0]["attribute_keys"] == 1
    assert preview[0]["attribute_values"] == 1

    db.expire_all()
    unchanged_task = crud_task.get(db=db, task_id=task.id)
    unchanged_sample = crud_sample.get(db=db, sample_id=samples[0].id)
    assert unchanged_task.config == original_config
    assert unchanged_sample.data == _historical_sample_data()

    with begin_transaction(db):
        applied = repair_plate_recognition_tasks(
            db, task_ids=[task.id], apply_changes=True
        )
    assert applied == preview

    db.expire_all()
    repaired_task = crud_task.get(db=db, task_id=task.id)
    repaired_sample = crud_sample.get(db=db, sample_id=samples[0].id)
    assert task_config_matches_profile(repaired_task.config)
    assert json.loads(repaired_task.config)["defaultRectPresetId"] == "plateRecognition"
    assert repaired_task.training_ready is None
    assert repaired_task.annotation_audit_json is None

    result = json.loads(json.loads(repaired_sample.data)["result"])
    rects = result["rectTool"]["result"]
    assert rects[0]["label"] == "doubleEP"
    assert rects[0]["attributes"]["plateType"] == ["greenPlate"]
    assert "attributes" not in rects[1]
    assert result["polygonTool"]["result"][0]["label"] == "platePoint"


def test_repair_plate_tasks_reports_missing_and_invalid_config(db: Session) -> None:
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="invalid plate task",
                config="not-json",
                media_type="IMAGE",
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        non_plate_task = crud_task.create(
            db=db,
            task=Task(
                name="ordinary task",
                config='{"tools":[{"tool":"rectTool","config":{"attributes":[{"value":"car"}]}}]}',
                media_type="IMAGE",
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )

    with begin_transaction(db):
        result = repair_plate_recognition_tasks(
            db, task_ids=[task.id, non_plate_task.id, 999999], apply_changes=True
        )

    assert result == [
        {"task_id": task.id, "found": True, "error": "invalid_task_config"},
        {
            "task_id": non_plate_task.id,
            "found": True,
            "error": "plate_marker_not_found",
        },
        {"task_id": 999999, "found": False},
    ]
