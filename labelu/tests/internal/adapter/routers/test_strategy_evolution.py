import hashlib
import hmac
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_sample,
    crud_strategy_task_integration,
    crud_task,
    crud_user,
    crud_yolo_bm1688_export,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.response.yolo_train import YoloTrainJobResponse
from labelu.internal.application.response.strategy_evolution import (
    StrategyEvolutionTaskDetailResponse,
)
from labelu.internal.application.service import strategy_evolution as strategy_service
from labelu.internal.common import aicm_publish_job
from labelu.internal.common.bootstrap_admin import DEFAULT_ADMIN_USERNAME
from labelu.internal.common.service_account import OPEN_SYSTEM_USERNAME
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyPlatformCallbackReceipt,
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User


def _task_payload(external_task_id: str = "strategy-task-3001") -> dict:
    return {
        "schema_version": "v1",
        "external_task_id": external_task_id,
        "task_name": "夜间电动车误报阈值实验",
        "task_type": "threshold_experiment",
        "target": {
            "device_type": "normal_camera",
            "event_type": "electric_bike",
            "model_id": "model-1",
            "scene_type": "night",
            "strategy_id": "strategy-v1",
        },
        "data_window": {
            "start_time": "2026-06-24 00:00:00",
            "end_time": "2026-06-25 00:00:00",
        },
        "experiment": {
            "current_thresholds": {
                "edge_low_threshold": 0.35,
                "edge_high_threshold": 0.8,
                "flink_filter_threshold": 0.6,
            },
            "requested_thresholds": [
                "edge_low_threshold",
                "edge_high_threshold",
                "flink_filter_threshold",
            ],
            "goal": "f1",
        },
    }


def _write_test_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), color="red").save(path)


def _patch_completed_strategy_export(monkeypatch) -> None:
    async def _fake_start_export_for_job(db, task_id, job_id, current_user):
        train_job = crud_yolo_train_job.get(db, job_id)
        with begin_transaction(db):
            export_row = crud_yolo_bm1688_export.create(
                db=db,
                user_id=current_user.id,
                task_id=task_id,
                yolo_train_job_id=job_id,
                imgsz=int(getattr(train_job, "imgsz", 640) or 640),
            )
            crud_yolo_bm1688_export.update_row(db, export_row, status="COMPLETED")
        return SimpleNamespace(
            id=export_row.id,
            status=export_row.status,
            task_id=task_id,
        )

    monkeypatch.setattr(
        strategy_service.yolo_bm1688_export,
        "start_export_for_job",
        _fake_start_export_for_job,
    )


def _ensure_admin(db: Session) -> User:
    with begin_transaction(db):
        admin = crud_user.get_user_by_username(db, username=DEFAULT_ADMIN_USERNAME)
        if admin:
            return admin
        return crud_user.create(
            db=db,
            user=User(
                username=DEFAULT_ADMIN_USERNAME,
                hashed_password=get_password_hash("Admin@123"),
            ),
        )


def _prepare_threshold_fixture(
    db: Session,
    media_root: Path,
    admin_user_id: int,
):
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="expert feedback electric bike",
                description="expert feedback",
                tips="",
                config=json.dumps(
                    {
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [
                                        {
                                            "key": "electric_bike",
                                            "value": "electric_bike",
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                created_by=admin_user_id,
                updated_by=admin_user_id,
                media_type="IMAGE",
            ),
        )
        train_job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=admin_user_id,
            model_weights="yolo11n.pt",
            epochs=10,
            imgsz=640,
            category="default",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=train_job,
            status="COMPLETED",
            weights_path="weights/best.pt",
        )
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=admin_user_id,
            task_id=task.id,
            yolo_train_job_id=train_job.id,
            imgsz=640,
        )
        crud_yolo_bm1688_export.update_row(db, export_row, status="COMPLETED")
        att_done = crud_attachment.create(
            db,
            TaskAttachment(
                filename="threshold_done.png",
                url="/upload/threshold_done.png",
                path="upload/threshold_done.png",
                task_id=task.id,
                created_by=admin_user_id,
                updated_by=admin_user_id,
            ),
        )
        att_new = crud_attachment.create(
            db,
            TaskAttachment(
                filename="threshold_new.png",
                url="/upload/threshold_new.png",
                path="upload/threshold_new.png",
                task_id=task.id,
                created_by=admin_user_id,
                updated_by=admin_user_id,
            ),
        )
        att_outside = crud_attachment.create(
            db,
            TaskAttachment(
                filename="threshold_outside.png",
                url="/upload/threshold_outside.png",
                path="upload/threshold_outside.png",
                task_id=task.id,
                created_by=admin_user_id,
                updated_by=admin_user_id,
            ),
        )
        samples = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=att_done.id,
                    created_by=admin_user_id,
                    updated_by=admin_user_id,
                    state=SampleState.DONE.value,
                    annotated_count=1,
                    data=json.dumps(
                        {
                            "result": json.dumps(
                                {
                                    "rectTool": {
                                        "toolName": "rectTool",
                                        "result": [
                                            {
                                                "label": "electric_bike",
                                                "x": 10,
                                                "y": 10,
                                                "width": 20,
                                                "height": 20,
                                            }
                                        ],
                                    }
                                },
                                ensure_ascii=False,
                            )
                        },
                        ensure_ascii=False,
                    ),
                ),
                TaskSample(
                    task_id=task.id,
                    file_id=att_new.id,
                    created_by=admin_user_id,
                    updated_by=admin_user_id,
                    state=SampleState.NEW.value,
                    annotated_count=0,
                    data=json.dumps({"result": "{}"}, ensure_ascii=False),
                ),
                TaskSample(
                    task_id=task.id,
                    file_id=att_outside.id,
                    created_by=admin_user_id,
                    updated_by=admin_user_id,
                    state=SampleState.DONE.value,
                    annotated_count=1,
                    data=json.dumps(
                        {
                            "result": json.dumps(
                                {
                                    "rectTool": {
                                        "toolName": "rectTool",
                                        "result": [
                                            {
                                                "label": "electric_bike",
                                                "x": 11,
                                                "y": 11,
                                                "width": 21,
                                                "height": 21,
                                            }
                                        ],
                                    }
                                },
                                ensure_ascii=False,
                            )
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
        threshold_package_id = f"experiment-dataset-threshold-window-{int(task.id)}"
        metadata_json = {
            "schema_version": "v1",
            "package_id": threshold_package_id,
            "dataset_type": "reviewed_false_positive_daily",
            "dataset_date": "2026-06-24",
            "window_start": "2026-06-24 00:00:00",
            "window_end": "2026-06-27 00:00:00",
            "samples": [
                {
                    "event_time": "2026-06-24 12:30:00",
                    "event_type": "electric_bike",
                    "device_type": "normal_camera",
                    "model_id": "537",
                    "scene_type": "night",
                    "strategy_id": "strategy-v1",
                    "image": {
                        "file_name": "threshold_done.png",
                        "object_key": "upload/threshold_done.png",
                    },
                },
                {
                    "event_time": "2026-06-24 13:30:00",
                    "event_type": "electric_bike",
                    "device_type": "normal_camera",
                    "model_id": "537",
                    "scene_type": "night",
                    "strategy_id": "strategy-v1",
                    "image": {
                        "file_name": "threshold_new.png",
                        "object_key": "upload/threshold_new.png",
                    },
                },
                {
                    "event_time": "2026-06-26 12:30:00",
                    "event_type": "electric_bike",
                    "device_type": "normal_camera",
                    "model_id": "537",
                    "scene_type": "night",
                    "strategy_id": "strategy-v1",
                    "image": {
                        "file_name": "threshold_outside.png",
                        "object_key": "upload/threshold_outside.png",
                    },
                },
            ],
        }
        import_row = ExperimentDatasetImport(
            package_id=threshold_package_id,
            dataset_type="reviewed_false_positive_daily",
            dataset_date="2026-06-24",
            event_type="electric_bike",
            model_id="",
            target_signature="threshold-window",
            source_payload_json=json.dumps(
                {
                    "schema_version": "v1",
                    "package_id": threshold_package_id,
                    "dataset_type": "reviewed_false_positive_daily",
                    "dataset_date": "2026-06-24",
                    "window_start": "2026-06-24 00:00:00",
                    "window_end": "2026-06-27 00:00:00",
                    "target": {
                        "device_type": "",
                        "event_type": "electric_bike",
                        "model_id": "",
                        "scene_type": "",
                        "strategy_id": "",
                    },
                },
                ensure_ascii=False,
            ),
            metadata_json=json.dumps(metadata_json, ensure_ascii=False),
            status="COMPLETED",
            formal_task_ids_json=json.dumps([task.id]),
            declared_sample_count=3,
            formal_sample_count=3,
            draft_sample_count=0,
            abnormal_sample_count=0,
            created_by=admin_user_id,
            updated_by=admin_user_id,
        )
        db.add(import_row)
        task_ref = SimpleNamespace(id=task.id)
        export_id = int(export_row.id)
        done_sample_id = int(samples[0].id)
        new_sample_id = int(samples[1].id)
        outside_sample_id = int(samples[2].id)
    _write_test_image(media_root / "upload" / "threshold_done.png")
    _write_test_image(media_root / "upload" / "threshold_new.png")
    _write_test_image(media_root / "upload" / "threshold_outside.png")
    return task_ref, export_id, done_sample_id, new_sample_id, outside_sample_id


def _prepare_model_capability_fixture(
    db: Session,
    media_root: Path,
    admin_user_id: int,
):
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="expert feedback model capability",
                description="expert feedback capability",
                tips="",
                config=json.dumps(
                    {
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [
                                        {
                                            "key": "electric_bike",
                                            "value": "electric_bike",
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                created_by=admin_user_id,
                updated_by=admin_user_id,
                media_type="IMAGE",
                status="CONFIGURED",
            ),
        )
        base_train_job = crud_yolo_train_job.create(
            db=db,
            task_id=task.id,
            user_id=admin_user_id,
            model_weights="yolo11n.pt",
            epochs=10,
            imgsz=640,
            category="base-model",
        )
        crud_yolo_train_job.update_status(
            db=db,
            job=base_train_job,
            status="COMPLETED",
            weights_path="weights/base_best.pt",
        )
        export_row = crud_yolo_bm1688_export.create(
            db=db,
            user_id=admin_user_id,
            task_id=task.id,
            yolo_train_job_id=base_train_job.id,
            imgsz=640,
        )
        crud_yolo_bm1688_export.update_row(db, export_row, status="COMPLETED")
        attachments = [
            crud_attachment.create(
                db,
                TaskAttachment(
                    filename=name,
                    url=f"/upload/{name}",
                    path=f"upload/{name}",
                    task_id=task.id,
                    created_by=admin_user_id,
                    updated_by=admin_user_id,
                ),
            )
            for name in (
                "capability_in_window_1.png",
                "capability_in_window_2.png",
                "capability_outside.png",
            )
        ]
        samples = crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=task.id,
                    file_id=attachments[0].id,
                    created_by=admin_user_id,
                    updated_by=admin_user_id,
                    state=SampleState.DONE.value,
                    annotated_count=1,
                    data=json.dumps(
                        {
                            "result": json.dumps(
                                {
                                    "rectTool": {
                                        "toolName": "rectTool",
                                        "result": [
                                            {
                                                "label": "electric_bike",
                                                "x": 10,
                                                "y": 10,
                                                "width": 20,
                                                "height": 20,
                                            }
                                        ],
                                    }
                                },
                                ensure_ascii=False,
                            )
                        },
                        ensure_ascii=False,
                    ),
                ),
                TaskSample(
                    task_id=task.id,
                    file_id=attachments[1].id,
                    created_by=admin_user_id,
                    updated_by=admin_user_id,
                    state=SampleState.DONE.value,
                    annotated_count=1,
                    data=json.dumps(
                        {
                            "result": json.dumps(
                                {
                                    "rectTool": {
                                        "toolName": "rectTool",
                                        "result": [
                                            {
                                                "label": "electric_bike",
                                                "x": 12,
                                                "y": 12,
                                                "width": 22,
                                                "height": 22,
                                            }
                                        ],
                                    }
                                },
                                ensure_ascii=False,
                            )
                        },
                        ensure_ascii=False,
                    ),
                ),
                TaskSample(
                    task_id=task.id,
                    file_id=attachments[2].id,
                    created_by=admin_user_id,
                    updated_by=admin_user_id,
                    state=SampleState.DONE.value,
                    annotated_count=1,
                    data=json.dumps(
                        {
                            "result": json.dumps(
                                {
                                    "rectTool": {
                                        "toolName": "rectTool",
                                        "result": [
                                            {
                                                "label": "electric_bike",
                                                "x": 14,
                                                "y": 14,
                                                "width": 24,
                                                "height": 24,
                                            }
                                        ],
                                    }
                                },
                                ensure_ascii=False,
                            )
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
        )
        capability_package_id = f"experiment-dataset-model-capability-{int(task.id)}"
        metadata_json = {
            "schema_version": "v1",
            "package_id": capability_package_id,
            "dataset_type": "reviewed_false_positive_daily",
            "dataset_date": "2026-06-24",
            "window_start": "2026-06-24 00:00:00",
            "window_end": "2026-06-25 00:00:00",
            "samples": [
                {
                    "event_time": "2026-06-24 09:00:00",
                    "event_type": "electric_bike",
                    "device_type": "normal_camera",
                    "model_id": "537",
                    "scene_type": "night",
                    "strategy_id": "strategy-v1",
                    "image": {
                        "file_name": "capability_in_window_1.png",
                        "object_key": "upload/capability_in_window_1.png",
                    },
                },
                {
                    "event_time": "2026-06-24 11:00:00",
                    "event_type": "electric_bike",
                    "device_type": "normal_camera",
                    "model_id": "537",
                    "scene_type": "night",
                    "strategy_id": "strategy-v1",
                    "image": {
                        "file_name": "capability_in_window_2.png",
                        "object_key": "upload/capability_in_window_2.png",
                    },
                },
                {
                    "event_time": "2026-06-25 11:00:00",
                    "event_type": "electric_bike",
                    "device_type": "normal_camera",
                    "model_id": "537",
                    "scene_type": "night",
                    "strategy_id": "strategy-v1",
                    "image": {
                        "file_name": "capability_outside.png",
                        "object_key": "upload/capability_outside.png",
                    },
                },
            ],
        }
        db.add(
            ExperimentDatasetImport(
                package_id=capability_package_id,
                dataset_type="reviewed_false_positive_daily",
                dataset_date="2026-06-24",
                event_type="electric_bike",
                model_id="",
                target_signature="model-capability-window",
                source_payload_json=json.dumps(
                    {
                        "schema_version": "v1",
                        "package_id": capability_package_id,
                        "dataset_type": "reviewed_false_positive_daily",
                        "dataset_date": "2026-06-24",
                        "window_start": "2026-06-24 00:00:00",
                        "window_end": "2026-06-25 00:00:00",
                        "target": {
                            "device_type": "",
                            "event_type": "electric_bike",
                            "model_id": "",
                            "scene_type": "",
                            "strategy_id": "",
                        },
                    },
                    ensure_ascii=False,
                ),
                metadata_json=json.dumps(metadata_json, ensure_ascii=False),
                status="COMPLETED",
                formal_task_ids_json=json.dumps([task.id]),
                declared_sample_count=3,
                formal_sample_count=3,
                draft_sample_count=0,
                abnormal_sample_count=0,
                created_by=admin_user_id,
                updated_by=admin_user_id,
            )
        )
        task_ref = SimpleNamespace(id=task.id)
        export_id = int(export_row.id)
        selected_sample_ids = [int(samples[0].id), int(samples[1].id)]
    _write_test_image(media_root / "upload" / "capability_in_window_1.png")
    _write_test_image(media_root / "upload" / "capability_in_window_2.png")
    _write_test_image(media_root / "upload" / "capability_outside.png")
    return task_ref, export_id, selected_sample_ids


class TestStrategyEvolutionRouter:
    def test_edge_job_summary_handles_missing_eval_summary(self) -> None:
        summary = strategy_service._summarize_edge_job_payload(
            {
                "job_id": 26,
                "status": "FAILED",
                "result": {"eval_summary": None},
            }
        )

        assert summary is not None
        assert summary["status"] == "FAILED"
        assert summary.get("edge_accuracy_metrics") is None
        assert summary.get("class_metrics") is None
        assert summary.get("business_threshold_analysis") is None

    def test_model_training_cache_drops_deleted_job_reference(
        self,
        db: Session,
        monkeypatch,
    ) -> None:
        admin = _ensure_admin(db)
        with begin_transaction(db):
            row = StrategyTaskIntegration(
                external_task_id="strategy-task-stale-model-cache",
                schema_version="v1",
                task_name="stale model cache",
                task_type="model_capability",
                dataset_s3_uri="",
                dataset_image_prefix="",
                metadata_key="",
                bucket="",
                prefix="",
                labeling_required=False,
                labeling_labels_json="[]",
                target_json="{}",
                data_window_json="{}",
                experiment_json="{}",
                platform_result_json=json.dumps(
                    {
                        "model_training_status": "training_job_created",
                        "model_training_jobs": [{"job_id": 987654, "status": "COMPLETED"}],
                    }
                ),
                derived_train_job_id=987654,
                created_by=admin.id,
                updated_by=admin.id,
            )
            db.add(row)
            db.flush()

        def _deleted_job(*args, **kwargs):
            raise LabelUException(
                code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
                status_code=404,
            )

        monkeypatch.setattr(strategy_service.yolo_train, "get_train_job_by_id", _deleted_job)

        result, message, changed = strategy_service._sync_existing_model_training_result(
            db,
            row=row,
        )

        assert changed is True
        assert message == "stale model training job reference cleared"
        assert result["model_training_status"] == "stale_reference_cleared"
        assert "model_training_jobs" not in result

    def test_threshold_contract_rejects_percent_and_out_of_range_values(
        self,
        client: TestClient,
    ) -> None:
        invalid_experiments = [
            {"current_thresholds": {"edge_low_threshold": 35}},
            {"current_thresholds": {"edge_low_threshold": "35%"}},
            {
                "threshold_unit": "percent",
                "current_thresholds": {"edge_low_threshold": 0.35},
            },
            {
                "threshold_unit": "ratio_0_1",
                "unit": "percent",
                "current_thresholds": {"edge_low_threshold": 0.35},
            },
            {"edge_validation_thresholds": {"nms_thres": 1.01}},
            {"edge_validation_thresholds": {"match_iou_thres": -0.01}},
            {
                "edge_validation_thresholds": {
                    "unit": "percent",
                    "conf_thres": 0.35,
                }
            },
        ]

        for index, experiment in enumerate(invalid_experiments):
            payload = _task_payload(f"strategy-invalid-threshold-{index}")
            payload["experiment"] = experiment
            response = client.post(
                "/internal/v1/strategy-evolution/tasks",
                json=payload,
            )
            assert response.status_code == 422, response.text
            assert response.json()["err_code"] == 30002

    def test_threshold_contract_accepts_full_ratio_domain(
        self,
        client: TestClient,
    ) -> None:
        payload = _task_payload("strategy-full-ratio-domain")
        payload["experiment"]["edge_validation_thresholds"] = {
            "conf_thres": 0.8,
            "nms_thres": 0.85,
            "match_iou_thres": 0.9,
        }

        response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )

        assert response.status_code == 202, response.text

    def test_platform_callback_rejects_percent_threshold_contract(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            "/api/v1/strategy-evolution/platform-callbacks",
            json={
                "schema_version": "v1",
                "callback_event_id": "callback-invalid-threshold",
                "external_task_id": "missing-task-is-not-reached",
                "task_type": "threshold_experiment",
                "status": "completed",
                "result": {"threshold_unit": "percent"},
            },
        )
        assert response.status_code == 422, response.text
        assert response.json()["status"] == 422

    def test_platform_callback_rejects_nested_threshold_contracts(
        self,
        client: TestClient,
    ) -> None:
        invalid_results = [
            (
                "threshold_experiment",
                {
                    "business_thresholds": {
                        "unit": "percent",
                        "current": {"edge_low_threshold": 0.35},
                    }
                },
            ),
            (
                "model_capability",
                {
                    "validation_thresholds": {
                        "unit": "ratio_0_1",
                        "effective": {"conf_thres": 35},
                    }
                },
            ),
            (
                "model_capability",
                {"threshold_unit": "percent"},
            ),
            (
                "threshold_experiment",
                {
                    "threshold_unit": "ratio_0_1",
                    "unit": "percent",
                    "recommended_thresholds": {"edge_low_threshold": 0.35},
                },
            ),
            (
                "threshold_experiment",
                {
                    "business_thresholds": {
                        "unit": "ratio_0_1",
                        "current": {"edge_low_threshold_typo": 0.35},
                    }
                },
            ),
            (
                "threshold_experiment",
                {
                    "business_thresholds": {
                        "threshold_unit": "ratio_0_1",
                        "unit": "percent",
                        "current": {"edge_low_threshold": 0.35},
                    }
                },
            ),
            (
                "model_capability",
                {
                    "validation_thresholds": {
                        "unit": "ratio_0_1",
                        "effective": {"conf_threshold": 0.35},
                    }
                },
            ),
        ]
        for index, (task_type, result) in enumerate(invalid_results):
            response = client.post(
                "/api/v1/strategy-evolution/platform-callbacks",
                json={
                    "schema_version": "v1",
                    "callback_event_id": f"callback-invalid-nested-{index}",
                    "external_task_id": "missing-task-is-not-reached",
                    "task_type": task_type,
                    "status": "completed",
                    "result": result,
                },
            )
            assert response.status_code == 422, response.text
            assert response.json()["status"] == 422

    def test_threshold_contract_accepts_explicit_null_values(
        self,
        client: TestClient,
    ) -> None:
        payload = _task_payload("strategy-null-thresholds")
        payload["experiment"]["current_thresholds"]["edge_low_threshold"] = None

        response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )

        assert response.status_code == 202, response.text
        callback = client.post(
            "/api/v1/strategy-evolution/platform-callbacks",
            json={
                "schema_version": "v1",
                "callback_event_id": "callback-null-thresholds",
                "external_task_id": "strategy-null-thresholds",
                "task_type": "threshold_experiment",
                "status": "completed",
                "result": {
                    "threshold_unit": "ratio_0_1",
                    "recommended_thresholds": None,
                },
            },
        )
        assert callback.status_code == 200, callback.text

    def test_threshold_asset_persistence_distinguishes_omitted_from_explicit_clear(
        self,
        db: Session,
    ) -> None:
        with begin_transaction(db):
            export_row = crud_yolo_bm1688_export.create(
                db,
                user_id=91001,
                task_id=91001,
                yolo_train_job_id=91001,
                imgsz=640,
            )
            crud_yolo_bm1688_export.update_row(
                db,
                export_row,
                published_model_id=537,
                business_thresholds_json=json.dumps({"effective": {"edge_low_threshold": 0.4}}),
                validation_thresholds_json=json.dumps({"effective": {"conf_thres": 0.4}}),
                threshold_binding_json=json.dumps({"published_model_id": 537}),
                quality_gate_json=json.dumps({"passed": True}),
            )
            export_id = int(export_row.id)

        strategy_service._persist_export_threshold_assets(db, export_id=export_id)
        db.expire_all()
        preserved = crud_yolo_bm1688_export.get(db, export_id)
        assert preserved is not None
        assert preserved.published_model_id == 537
        assert preserved.business_thresholds_json is not None
        assert preserved.validation_thresholds_json is not None
        assert preserved.threshold_binding_json is not None
        assert preserved.quality_gate_json is not None

        strategy_service._persist_export_threshold_assets(
            db,
            export_id=export_id,
            business_thresholds=None,
            validation_thresholds={},
            threshold_binding=None,
            quality_gate={},
        )
        db.expire_all()
        cleared = crud_yolo_bm1688_export.get(db, export_id)
        assert cleared is not None
        assert cleared.published_model_id == 537
        assert cleared.business_thresholds_json is None
        assert cleared.validation_thresholds_json is None
        assert cleared.threshold_binding_json is None
        assert cleared.quality_gate_json is None

        assert (
            strategy_service._threshold_asset_section_payload(
                current={"edge_low_threshold": None},
                metadata={"goal": "f1"},
            )
            is None
        )

    def test_platform_callback_requires_hmac_or_explicit_unsigned_compatibility(
        self,
        client: TestClient,
        monkeypatch,
    ) -> None:
        payload = {
            "schema_version": "v1",
            "callback_event_id": "callback-unsigned-config",
            "external_task_id": "missing-task",
            "task_type": "threshold_experiment",
            "status": "running",
        }
        monkeypatch.setattr(
            settings,
            "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
            "",
        )
        monkeypatch.setattr(
            settings,
            "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS",
            False,
        )

        rejected = client.post(
            "/api/v1/strategy-evolution/platform-callbacks",
            json=payload,
        )
        assert rejected.status_code == 503, rejected.text

        monkeypatch.setattr(
            settings,
            "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS",
            True,
        )
        compatibility_request = client.post(
            "/api/v1/strategy-evolution/platform-callbacks",
            json=payload,
        )
        assert compatibility_request.status_code == 404, compatibility_request.text

    def test_platform_callback_recovers_from_concurrent_receipt_races(
        self,
        client: TestClient,
        db: Session,
        monkeypatch,
    ) -> None:
        unique_constraints = inspect(db.get_bind()).get_unique_constraints(
            StrategyPlatformCallbackReceipt.__tablename__
        )
        assert any(
            set(item.get("column_names") or [])
            == {"strategy_task_integration_id", "callback_event_id"}
            for item in unique_constraints
        )

        race_errors = (
            (
                "unique",
                IntegrityError(
                    "insert strategy callback receipt",
                    {},
                    RuntimeError("duplicate callback receipt"),
                ),
            ),
            (
                "sqlite-lock",
                OperationalError(
                    "insert strategy callback receipt",
                    {},
                    RuntimeError("database is locked"),
                ),
            ),
        )
        for race_name, race_error in race_errors:
            external_task_id = f"strategy-callback-{race_name}-race"
            created = client.post(
                "/internal/v1/strategy-evolution/tasks",
                json=_task_payload(external_task_id),
            )
            assert created.status_code == 202, created.text

            state = {"lookups": 0, "creates": 0}

            def fake_get_receipt(**_kwargs):
                state["lookups"] += 1
                if state["lookups"] == 1:
                    return None
                return SimpleNamespace(platform_status="running")

            def fake_create_receipt(**_kwargs):
                state["creates"] += 1
                raise race_error

            monkeypatch.setattr(
                strategy_service.crud_strategy_task_integration,
                "get_platform_callback_receipt",
                fake_get_receipt,
            )
            monkeypatch.setattr(
                strategy_service.crud_strategy_task_integration,
                "create_platform_callback_receipt",
                fake_create_receipt,
            )

            response = client.post(
                "/api/v1/strategy-evolution/platform-callbacks",
                json={
                    "schema_version": "v1",
                    "callback_event_id": f"callback-{race_name}-race",
                    "external_task_id": external_task_id,
                    "task_type": "threshold_experiment",
                    "status": "running",
                },
            )
            assert response.status_code == 200, response.text
            assert state == {"lookups": 2, "creates": 1}

    def test_platform_callback_hmac_is_required_and_replay_is_idempotent(
        self,
        client: TestClient,
        db: Session,
        monkeypatch,
    ) -> None:
        secret = "inbound-callback-secret"
        now = 1_721_732_800
        monkeypatch.setattr(
            settings,
            "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
            secret,
        )
        monkeypatch.setattr(
            settings,
            "STRATEGY_EVOLUTION_CALLBACK_HMAC_MAX_SKEW_SECONDS",
            300,
        )
        monkeypatch.setattr(strategy_service.time, "time", lambda: now)
        create_payload = _task_payload("strategy-signed-callback")
        created = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=create_payload,
        )
        assert created.status_code == 202, created.text

        callback_payload = {
            "schema_version": "v1",
            "callback_event_id": "callback-signed-1",
            "external_task_id": "strategy-signed-callback",
            "task_type": "threshold_experiment",
            "status": "running",
            "progress": {"stage": "edge_validation"},
        }
        unsigned = client.post(
            "/api/v1/strategy-evolution/platform-callbacks",
            json=callback_payload,
        )
        assert unsigned.status_code == 401, unsigned.text

        def signed_request(payload: dict, timestamp: int = now):
            body = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            event_id = payload["callback_event_id"]
            signature = hmac.new(
                secret.encode("utf-8"),
                b".".join(
                    (
                        str(timestamp).encode("utf-8"),
                        event_id.encode("utf-8"),
                        body,
                    )
                ),
                hashlib.sha256,
            ).hexdigest()
            return client.post(
                "/api/v1/strategy-evolution/platform-callbacks",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-LabelU-Timestamp": str(timestamp),
                    "X-LabelU-Event-Id": event_id,
                    "X-LabelU-Signature": f"sha256={signature}",
                },
            )

        stale = signed_request(callback_payload, timestamp=now - 301)
        assert stale.status_code == 401, stale.text
        future = signed_request(callback_payload, timestamp=now + 301)
        assert future.status_code == 401, future.text

        accepted = signed_request(callback_payload)
        assert accepted.status_code == 200, accepted.text

        completed_payload = {
            **callback_payload,
            "callback_event_id": "callback-signed-2",
            "status": "completed",
            "result": {"metrics": {"f1": 0.88}},
        }
        completed = signed_request(completed_payload)
        assert completed.status_code == 200, completed.text

        replay_payload = {
            **callback_payload,
            "status": "failed",
            "result": {"metrics": {"f1": 0.01}},
        }
        replay = signed_request(replay_payload)
        assert replay.status_code == 200, replay.text
        db.expire_all()
        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-signed-callback"
            )
            .first()
        )
        assert row is not None
        assert row.platform_status == "succeeded"
        assert json.loads(row.platform_result_json)["metrics"]["f1"] == 0.88
        assert (
            db.query(StrategyPlatformCallbackReceipt)
            .filter(
                StrategyPlatformCallbackReceipt.strategy_task_integration_id
                == row.id
            )
            .count()
            == 2
        )

        regression_payload = {
            **callback_payload,
            "callback_event_id": "callback-signed-3",
            "status": "running",
            "progress": {"stage": "stale_retry"},
        }
        regression = signed_request(regression_payload)
        assert regression.status_code == 409, regression.text
        db.expire_all()
        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-signed-callback"
            )
            .first()
        )
        assert row is not None
        assert row.platform_status == "succeeded"
        assert json.loads(row.platform_result_json)["metrics"]["f1"] == 0.88

    def test_platform_callback_rejects_running_after_failure_or_cancellation(
        self,
        client: TestClient,
        db: Session,
    ) -> None:
        for index, terminal_status in enumerate(("failed", "cancelled")):
            external_task_id = f"strategy-terminal-{terminal_status}"
            created = client.post(
                "/internal/v1/strategy-evolution/tasks",
                json=_task_payload(external_task_id),
            )
            assert created.status_code == 202, created.text

            terminal = client.post(
                "/api/v1/strategy-evolution/platform-callbacks",
                json={
                    "schema_version": "v1",
                    "callback_event_id": f"callback-terminal-{index}",
                    "external_task_id": external_task_id,
                    "task_type": "threshold_experiment",
                    "status": terminal_status,
                },
            )
            assert terminal.status_code == 200, terminal.text

            regression = client.post(
                "/api/v1/strategy-evolution/platform-callbacks",
                json={
                    "schema_version": "v1",
                    "callback_event_id": f"callback-regression-{index}",
                    "external_task_id": external_task_id,
                    "task_type": "threshold_experiment",
                    "status": "running",
                },
            )
            assert regression.status_code == 409, regression.text

            db.expire_all()
            row = (
                db.query(StrategyTaskIntegration)
                .filter(StrategyTaskIntegration.external_task_id == external_task_id)
                .first()
            )
            assert row is not None
            assert row.platform_status == terminal_status

    def test_outbound_callback_hmac_request_contract(self, monkeypatch) -> None:
        monkeypatch.setattr(
            settings,
            "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
            "callback-secret",
        )
        monkeypatch.setattr(strategy_service.time, "time", lambda: 1_721_732_800)
        payload = {
            "schema_version": "v1",
            "callback_event_id": "strategy-auto-8",
            "external_task_id": "third-party-task-8",
            "task_type": "model_capability",
            "status": "succeeded",
        }

        request_kwargs = strategy_service._outbound_callback_request_kwargs(payload)
        body = request_kwargs["data"]
        signed_content = b".".join(
            (b"1721732800", b"strategy-auto-8", body)
        )
        expected_signature = hmac.new(
            b"callback-secret",
            signed_content,
            hashlib.sha256,
        ).hexdigest()

        assert request_kwargs["timeout"] == 30
        assert json.loads(body.decode("utf-8")) == payload
        assert request_kwargs["headers"]["X-LabelU-Timestamp"] == "1721732800"
        assert request_kwargs["headers"]["X-LabelU-Event-Id"] == "strategy-auto-8"
        assert request_kwargs["headers"]["X-LabelU-Signature"] == (
            f"sha256={expected_signature}"
        )

    def test_nms_threshold_does_not_override_business_filter_threshold(self) -> None:
        result = strategy_service._derive_business_thresholds_from_validation(
            {
                "edge_low_threshold": 0.35,
                "edge_high_threshold": 0.8,
                "flink_filter_threshold": 0.6,
            },
            {
                "conf_thres": 0.45,
                "nms_thres": 0.72,
                "match_iou_thres": 0.5,
            },
        )

        assert result == {
            "edge_low_threshold": 0.45,
            "edge_high_threshold": 0.8,
            "flink_filter_threshold": 0.6,
        }

    def test_internal_task_accepts_without_auth_and_callback_updates_status(
        self,
        client: TestClient,
        db: Session,
    ) -> None:
        response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=_task_payload(),
        )

        assert response.status_code == 202
        accepted_body = response.json()
        assert accepted_body["status"] == "accepted"
        assert accepted_body["message"] == "已发布模型映射尚未就绪"
        assert "platform_status" not in accepted_body
        assert "result" not in accepted_body

        callback = client.post(
            "/api/v1/strategy-evolution/platform-callbacks",
            json={
                "schema_version": "v1",
                "callback_event_id": "callback-20260707-0002",
                "external_task_id": "strategy-task-3001",
                "task_type": "threshold_experiment",
                "status": "completed",
                "finished_at": "2026-07-07 18:30:00",
                "result": {
                    "recommended_thresholds": {
                        "edge_low_threshold": 0.45,
                        "edge_high_threshold": 0.8,
                        "flink_filter_threshold": 0.65,
                    },
                    "metrics": {"f1": 0.884},
                },
            },
        )

        assert callback.status_code == 200
        callback_body = callback.json()
        assert callback_body["status"] == 0
        assert callback_body["msg"] == ""
        assert callback_body["body"]["callback_id"] == "callback-20260707-0002"
        assert callback_body["body"]["task_id"] == "strategy-task-3001"
        assert callback_body["body"]["status"] == "succeeded"
        assert callback_body["body"]["external_platform"] == "experiment_platform"

        db.expire_all()
        row = (
            db.query(StrategyTaskIntegration)
            .filter(StrategyTaskIntegration.external_task_id == "strategy-task-3001")
            .first()
        )
        assert row is not None
        assert row.platform_status == "succeeded"
        assert row.platform_callback_event_id == "callback-20260707-0002"
        assert row.platform_finished_at == "2026-07-07 18:30:00"
        assert json.loads(row.platform_result_json)["metrics"]["f1"] == 0.884
        assert json.loads(row.platform_result_json)["threshold_unit"] == "ratio_0_1"
        assert json.loads(row.data_window_json)["start_time"] == "2026-06-24 00:00:00"
        assert json.loads(row.experiment_json)["threshold_unit"] == "ratio_0_1"

        detail = client.get("/internal/v1/strategy-evolution/tasks/strategy-task-3001")
        assert detail.status_code == 200
        detail_body = detail.json()["data"]
        assert detail_body["platform_status"] == "succeeded"
        assert detail_body["result"]["metrics"]["f1"] == 0.884
        assert detail_body["target"]["event_type"] == "electric_bike"

    def test_list_strategy_evolution_tasks_returns_process_summary(
        self,
        client: TestClient,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            strategy_service.aicm_publish_job,
            "list_jobs",
            lambda page, size: ([], 0),
        )
        waiting_payload = _task_payload("strategy-task-list-waiting")
        implemented_payload = _task_payload("strategy-task-list-model")
        implemented_payload["task_type"] = "model_capability"
        implemented_payload["task_name"] = "model capability task"
        implemented_payload["experiment"] = {
            "capability_issue_type": "high_score_false_positive",
            "symptoms": ["night_scene_false_positive"],
        }

        waiting_resp = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=waiting_payload,
        )
        implemented_resp = client.post(
            "/internal/v1/strategy-evolution/model-trainings",
            json=implemented_payload,
        )

        assert waiting_resp.status_code == 202
        assert implemented_resp.status_code == 202

        response = client.get(
            "/internal/v1/strategy-evolution/tasks",
            params={"page": 0, "size": 20},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["total"] >= 2
        assert body["meta_data"]["page"] == 0
        assert body["meta_data"]["size"] == 20
        rows = {
            item["external_task_id"]: item
            for item in body["data"]
            if item["external_task_id"]
            in {"strategy-task-list-waiting", "strategy-task-list-model"}
        }
        assert rows["strategy-task-list-waiting"]["platform_status"] == "waiting_for_model"
        assert rows["strategy-task-list-waiting"]["edge_job_count"] == 0
        assert rows["strategy-task-list-waiting"]["last_message"] == "待发布模型映射"
        assert rows["strategy-task-list-model"]["platform_status"] == "waiting_for_model"
        assert rows["strategy-task-list-model"]["model_job_count"] == 0
        assert rows["strategy-task-list-model"]["last_message"] == "待发布模型映射"

        filtered = client.get(
            "/internal/v1/strategy-evolution/tasks",
            params={"task_type": "model_capability", "keyword": "model capability"},
        )
        assert filtered.status_code == 200
        filtered_rows = filtered.json()["data"]
        assert len(filtered_rows) == 1
        assert filtered_rows[0]["external_task_id"] == "strategy-task-list-model"

        filtered_by_q = client.get(
            "/internal/v1/strategy-evolution/tasks",
            params={"task_type": "model_capability", "q": "model capability"},
        )
        assert filtered_by_q.status_code == 200
        filtered_q_rows = filtered_by_q.json()["data"]
        assert len(filtered_q_rows) == 1
        assert filtered_q_rows[0]["external_task_id"] == "strategy-task-list-model"

    def test_strategy_task_can_bind_exact_expert_feedback_package(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
    ) -> None:
        task_ref, _, _, _, _ = _prepare_threshold_fixture(
            db,
            tmp_path,
            _ensure_admin(db).id,
        )
        package_id = f"experiment-dataset-threshold-window-{int(task_ref.id)}"
        payload = _task_payload("strategy-task-source-package")
        payload["source_package_id"] = package_id
        payload["target"]["event_type"] = "electric_bike"
        payload["target"]["model_id"] = "537"

        response = client.post(
            "/internal/v1/strategy-evolution/threshold-experiments",
            json=payload,
        )

        assert response.status_code == 202, response.text
        db.expire_all()
        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-source-package"
            )
            .first()
        )
        assert row is not None
        assert row.source_package_id == package_id
        assert row.source_type == "third_party"
        assert row.source_system == "strategy_evolution"
        assert row.owner.username == OPEN_SYSTEM_USERNAME
        assert row.owner.is_service_account is True
        assert row.owner.interactive_login_enabled is False

        package = (
            db.query(ExperimentDatasetImport)
            .filter(ExperimentDatasetImport.package_id == package_id)
            .first()
        )
        assert package is not None
        assert package.source_external_task_id == "strategy-task-source-package"
        assert package.source_integration_id == row.id
        assert package.created_by == row.created_by

        linked_task = crud_task.get(db=db, task_id=int(task_ref.id))
        assert linked_task.source_type == "third_party"
        assert linked_task.source_external_package_id == package_id
        assert linked_task.created_by == row.created_by

        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-source-package"
        )
        assert detail.status_code == 200
        assert detail.json()["data"]["source_package_id"] == package_id
        assert detail.json()["data"]["created_by_username"] == OPEN_SYSTEM_USERNAME

    def test_strategy_tasks_persist_each_request_callback_url(
        self,
        client: TestClient,
        db: Session,
    ) -> None:
        threshold_callback_url = (
            "http://10.10.11.28:21087/api/v1/strategy-evolution/platform-callbacks"
        )
        model_callback_url = (
            "http://10.10.11.28:21088/api/v1/strategy-evolution/platform-callbacks"
        )
        threshold_payload = _task_payload("strategy-task-threshold-callback-url")
        threshold_payload["callback_url"] = threshold_callback_url
        model_payload = _task_payload("strategy-task-model-callback-url")
        model_payload.update(
            {
                "task_name": "专家回流-骑行未戴头盔-模型优化",
                "task_type": "model_capability",
                "callback_url": model_callback_url,
                "experiment": {
                    "capability_issue_type": "high_score_false_positive",
                    "symptoms": ["riding_without_helmet_false_positive"],
                },
            }
        )

        threshold_response = client.post(
            "/internal/v1/strategy-evolution/threshold-experiments",
            json=threshold_payload,
        )
        model_response = client.post(
            "/internal/v1/strategy-evolution/model-trainings",
            json=model_payload,
        )

        assert threshold_response.status_code == 202, threshold_response.text
        assert model_response.status_code == 202, model_response.text
        db.expire_all()
        rows = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id.in_(
                    (
                        "strategy-task-threshold-callback-url",
                        "strategy-task-model-callback-url",
                    )
                )
            )
            .all()
        )
        callback_urls = {
            row.external_task_id: row.outbound_callback_url for row in rows
        }
        assert callback_urls == {
            "strategy-task-threshold-callback-url": threshold_callback_url,
            "strategy-task-model-callback-url": model_callback_url,
        }

    def test_strategy_task_type_callback_configuration_rejects_wrong_port(
        self,
        client: TestClient,
        db: Session,
        monkeypatch,
    ) -> None:
        threshold_callback_url = (
            "http://10.10.11.28:21087/api/v1/strategy-evolution/platform-callbacks"
        )
        model_callback_url = (
            "http://10.10.11.28:21088/api/v1/strategy-evolution/platform-callbacks"
        )
        monkeypatch.setattr(
            settings,
            "STRATEGY_EVOLUTION_THRESHOLD_CALLBACK_URL",
            threshold_callback_url,
        )
        monkeypatch.setattr(
            settings,
            "STRATEGY_EVOLUTION_MODEL_CALLBACK_URL",
            model_callback_url,
        )
        payload = _task_payload("strategy-task-threshold-wrong-callback-port")
        payload["callback_url"] = model_callback_url

        rejected = client.post(
            "/internal/v1/strategy-evolution/threshold-experiments",
            json=payload,
        )

        assert rejected.status_code == 400
        assert "threshold_experiment 的 callback_url 必须使用已配置地址" in (
            rejected.json()["message"]
        )

        payload["external_task_id"] = "strategy-task-threshold-configured-callback"
        payload.pop("callback_url")
        accepted = client.post(
            "/internal/v1/strategy-evolution/threshold-experiments",
            json=payload,
        )

        assert accepted.status_code == 202, accepted.text
        row = crud_strategy_task_integration.get_by_external_task_id(
            db=db,
            external_task_id="strategy-task-threshold-configured-callback",
        )
        assert row is not None
        assert row.outbound_callback_url == threshold_callback_url

    def test_strategy_task_rejects_non_http_callback_url(
        self,
        client: TestClient,
    ) -> None:
        payload = _task_payload("strategy-task-invalid-callback-url")
        payload["callback_url"] = "ftp://10.10.11.28/callback"

        response = client.post(
            "/internal/v1/strategy-evolution/threshold-experiments",
            json=payload,
        )

        assert response.status_code == 422

    def test_strategy_task_rejects_unknown_source_package(
        self,
        client: TestClient,
    ) -> None:
        payload = _task_payload("strategy-task-source-package-missing")
        payload["source_package_id"] = "missing-expert-feedback-package"

        response = client.post(
            "/internal/v1/strategy-evolution/threshold-experiments",
            json=payload,
        )

        assert response.status_code == 404
        assert "未找到专家回流数据包" in response.json()["message"]

    def test_list_strategy_evolution_tasks_does_not_crash_without_derived_export_id(
        self,
        client: TestClient,
        db: Session,
    ) -> None:
        admin = _ensure_admin(db)
        with begin_transaction(db):
            db.add(
                StrategyTaskIntegration(
                    external_task_id="strategy-task-list-missing-export",
                    labelu_task_id=None,
                    datasource_id=None,
                    schema_version="v1",
                    task_name="missing export threshold task",
                    task_type="threshold_experiment",
                    dataset_s3_uri="",
                    dataset_image_prefix="",
                    metadata_key="",
                    bucket="",
                    prefix="",
                    labeling_required=False,
                    labeling_media_type=None,
                    labeling_annotation_type=None,
                    labeling_labels_json="[]",
                    target_json=json.dumps(
                        {
                            "device_type": "normal_camera",
                            "event_type": "electric_bike",
                            "model_id": "model-1",
                            "scene_type": "night",
                            "strategy_id": "strategy-v1",
                        },
                        ensure_ascii=False,
                    ),
                    data_window_json=json.dumps(
                        {
                            "start_time": "2026-06-24 00:00:00",
                            "end_time": "2026-06-25 00:00:00",
                        },
                        ensure_ascii=False,
                    ),
                    experiment_json=json.dumps(
                        {"goal": "f1"},
                        ensure_ascii=False,
                    ),
                    import_status="PENDING",
                    platform_status="edge_validation_created",
                    platform_result_json=json.dumps(
                        {
                            "edge_experiment_status": "edge_validation_created",
                            "edge_experiment_jobs": [],
                            "edge_validation_jobs": [],
                        },
                        ensure_ascii=False,
                    ),
                    automation_source="strategy_evolution",
                    automation_stage="edge_validation",
                    automation_status="running",
                    outbound_callback_status="pending",
                    created_by=admin.id,
                    updated_by=admin.id,
                )
            )

        response = client.get(
            "/internal/v1/strategy-evolution/tasks",
            params={"page": 0, "size": 20},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["page"] == 0
        assert body["meta_data"]["size"] == 20
        rows = {
            item["external_task_id"]: item
            for item in body["data"]
            if item["external_task_id"] == "strategy-task-list-missing-export"
        }
        assert rows["strategy-task-list-missing-export"]["platform_status"] == "export_created"
        assert (
            rows["strategy-task-list-missing-export"]["last_message"]
            == "已创建 BM1688 导出任务"
        )

    def test_threshold_experiment_creates_edge_validation_from_finished_expert_samples(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
        _patch_completed_strategy_export(monkeypatch)
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "start_job",
            lambda db, job_id, current_user: SimpleNamespace(status="PROCESSING"),
        )

        admin = _ensure_admin(db)
        (
            task,
            export_id,
            done_sample_id,
            new_sample_id,
            outside_sample_id,
        ) = _prepare_threshold_fixture(db, tmp_path, admin.id)
        aicm_publish_job.save_job(
            "publish-model-1",
            {
                "job_id": "publish-model-1",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 530,
                },
                "log_lines": [
                    "最新模型集 id=537，调用 edit_name → 非机动车违停_bm1688_v1"
                ],
                "created_at": "2026-07-13T10:00:00",
                "updated_at": "2026-07-13T10:00:00",
            },
        )
        payload = _task_payload("strategy-task-threshold-run")
        payload["target"]["model_id"] = "537"

        response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["status"] == "accepted"
        assert body["message"] == "已创建 BM1688 导出任务"
        assert "platform_status" not in body
        assert "result" not in body

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-threshold-run"
        )
        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-run"
        )
        assert detail.status_code == 200
        detail_result = detail.json()["data"]["result"]
        assert detail.json()["data"]["platform_status"] == "edge_validation_created"
        assert detail_result["model_resolution"]["resolved_by"] == "aicm_publish_job"
        assert detail_result["model_resolution"]["model_set_id"] == 537
        assert detail_result["source_export_id"] == export_id
        assert int(detail_result["derived_export_id"]) > 0
        assert detail_result["selected_sample_count"] == 1
        assert detail_result["sample_selection_mode"] == "metadata_sample_event_time"
        assert detail_result["selected_sample_ids_by_task"] == {
            str(task.id): [done_sample_id]
        }
        assert (
            detail_result["selected_samples"][0]["event_time"]
            == "2026-06-24 12:30:00"
        )
        assert detail_result["matched_task_ids"] == [task.id]
        assert len(detail_result["edge_validation_jobs"]) == 1
        edge_job = detail_result["edge_validation_jobs"][0]
        assert edge_job["export_id"] == detail_result["derived_export_id"]
        assert edge_job["task_id"] == task.id
        assert edge_job["image_count"] == 1
        assert edge_job["start_status"] == "started"
        assert detail_result["edge_validation_jobs"][0]["job_id"] == edge_job["job_id"]

        db.expire_all()
        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-threshold-run"
            )
            .first()
        )
        assert row is not None
        persisted_result = json.loads(row.platform_result_json)
        assert persisted_result["selected_sample_count"] == 1
        assert persisted_result["selected_sample_ids_by_task"] == {
            str(task.id): [done_sample_id]
        }
        assert (
            new_sample_id
            not in persisted_result["selected_sample_ids_by_task"][str(task.id)]
        )
        assert (
            outside_sample_id
            not in persisted_result["selected_sample_ids_by_task"][str(task.id)]
        )

    def test_threshold_experiment_resolves_from_persisted_published_model_id(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
        _patch_completed_strategy_export(monkeypatch)
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "start_job",
            lambda db, job_id, current_user: SimpleNamespace(status="PROCESSING"),
        )

        admin = _ensure_admin(db)
        task, export_id, done_sample_id, *_ = _prepare_threshold_fixture(
            db, tmp_path, admin.id
        )
        export_row = crud_yolo_bm1688_export.get(db, export_id)
        assert export_row is not None
        with begin_transaction(db):
            crud_yolo_bm1688_export.update_row(
                db, export_row, published_model_id=537
            )

        payload = _task_payload("strategy-task-threshold-db-model")
        payload["target"]["model_id"] = "537"

        response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )

        assert response.status_code == 202, response.text
        assert response.json()["message"] == "已创建 BM1688 导出任务"

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-threshold-db-model"
        )
        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-db-model"
        )
        assert detail.status_code == 200
        detail_result = detail.json()["data"]["result"]
        assert detail_result["model_resolution"]["resolved_by"] == (
            "export_row_published_model_id"
        )
        assert detail_result["model_resolution"]["published_model_id"] == 537
        assert detail_result["source_export_id"] == export_id
        assert detail_result["selected_sample_ids_by_task"] == {
            str(task.id): [done_sample_id]
        }

    def test_resolve_export_id_backfills_persisted_published_model_id_from_publish_job(
        self,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))

        admin = _ensure_admin(db)
        _, export_id, *_ = _prepare_threshold_fixture(db, tmp_path, admin.id)
        export_row = crud_yolo_bm1688_export.get(db, export_id)
        assert export_row is not None
        assert getattr(export_row, "published_model_id", None) is None

        aicm_publish_job.save_job(
            "publish-backfill-model-id",
            {
                "job_id": "publish-backfill-model-id",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 530,
                },
                "log_lines": [
                    "最新模型集 id=537，调用 edit_name → 非机动车违停_bm1688_v1"
                ],
                "created_at": "2026-07-17T12:00:00",
                "updated_at": "2026-07-17T12:00:00",
            },
        )

        resolved_export_id, resolution = strategy_service._resolve_export_id_from_model_id(
            db, "537"
        )

        assert resolved_export_id == export_id
        assert resolution["resolved_by"] == "aicm_publish_job"
        db.refresh(export_row)
        assert export_row.published_model_id == 537

    def test_threshold_experiment_reuses_existing_edge_validation_job_and_resumes_pending(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
        _patch_completed_strategy_export(monkeypatch)
        start_calls: list[int] = []

        def _fake_start_job(db, job_id, current_user):
            start_calls.append(int(job_id))
            row = crud_yolo_edge_validation_job.get(db, int(job_id))
            with begin_transaction(db):
                crud_yolo_edge_validation_job.update(
                    db,
                    row,
                    status="PROCESSING",
                    started_at=datetime(2026, 7, 13, 17, 45, 18),
                )
            return SimpleNamespace(status="PROCESSING")

        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "start_job",
            _fake_start_job,
        )

        admin = _ensure_admin(db)
        task, export_id, *_ = _prepare_threshold_fixture(db, tmp_path, admin.id)
        aicm_publish_job.save_job(
            "publish-model-resume",
            {
                "job_id": "publish-model-resume",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 530,
                },
                "log_lines": ["最新模型集 id=537"],
                "created_at": "2026-07-13T10:00:00",
                "updated_at": "2026-07-13T10:00:00",
            },
        )
        payload = _task_payload("strategy-task-threshold-resume")
        payload["target"]["model_id"] = "537"

        first_response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )
        assert first_response.status_code == 202, first_response.text
        assert first_response.json()["message"] == "已创建 BM1688 导出任务"

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-threshold-resume"
        )
        first_detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-resume"
        )
        assert first_detail.status_code == 200, first_detail.text

        db.expire_all()
        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-threshold-resume"
            )
            .first()
        )
        assert row is not None
        first_result = json.loads(row.platform_result_json)
        assert first_result["derived_export_id"] > 0
        job_id = int(first_result["edge_validation_jobs"][0]["job_id"])
        assert start_calls == [job_id]

        edge_job = crud_yolo_edge_validation_job.get(db, job_id)
        with begin_transaction(db):
            crud_yolo_edge_validation_job.update(
                db,
                edge_job,
                status="PENDING",
            )

        second_response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )
        assert second_response.status_code == 202, second_response.text
        assert second_response.json()["message"] == "已有边端验证任务已恢复执行"
        assert start_calls == [job_id, job_id]

        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-resume"
        )
        assert detail.status_code == 200, detail.text
        edge_jobs = detail.json()["data"]["result"]["edge_validation_jobs"]
        assert len(edge_jobs) == 1
        assert edge_jobs[0]["job_id"] == job_id
        assert edge_jobs[0]["status"] == "PROCESSING"
        assert edge_jobs[0]["start_status"] == "started"
        assert detail.json()["data"]["result"]["selected_sample_ids_by_task"] == {
            str(task.id): edge_jobs[0]["sample_ids"]
        }

    def test_threshold_experiment_detail_refreshes_completed_edge_result(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
        _patch_completed_strategy_export(monkeypatch)
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "start_job",
            lambda db, job_id, current_user: SimpleNamespace(status="PROCESSING"),
        )

        admin = _ensure_admin(db)
        _, export_id, done_sample_id, *_ = _prepare_threshold_fixture(
            db, tmp_path, admin.id
        )
        aicm_publish_job.save_job(
            "publish-model-detail-refresh",
            {
                "job_id": "publish-model-detail-refresh",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 530,
                },
                "log_lines": ["最新模型集 id=537"],
                "created_at": "2026-07-13T10:00:00",
                "updated_at": "2026-07-13T10:00:00",
            },
        )
        payload = _task_payload("strategy-task-threshold-detail-refresh")
        payload["target"]["model_id"] = "537"

        create_response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )
        assert create_response.status_code == 202, create_response.text

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-threshold-detail-refresh"
        )
        first_detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-detail-refresh"
        )
        assert first_detail.status_code == 200, first_detail.text

        db.expire_all()
        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-threshold-detail-refresh"
            )
            .first()
        )
        assert row is not None
        stored_result = json.loads(row.platform_result_json)
        job_id = int(stored_result["edge_validation_jobs"][0]["job_id"])

        edge_job = crud_yolo_edge_validation_job.get(db, job_id)
        with begin_transaction(db):
            crud_yolo_edge_validation_job.update(
                db,
                edge_job,
                status="COMPLETED",
                started_at=datetime(2026, 7, 13, 17, 45, 18),
                completed_at=datetime(2026, 7, 13, 17, 46, 44),
                report_summary="边端验证执行完成",
                progress_json=json.dumps(
                    {
                        "stage": "finalize",
                        "message": "边端验证已完成，结果已回填",
                        "detail": {
                            "final_status": "COMPLETED",
                            "remote_exit_code": 0,
                        },
                    },
                    ensure_ascii=False,
                ),
                result_json=json.dumps(
                    {
                        "request": {"sample_ids": [done_sample_id]},
                        "execution": {
                            "status": "ok",
                            "remote_exit_code": 0,
                        },
                        "eval_summary": {
                            "status": "ok",
                            "map50": 0.81,
                            "business_threshold_analysis": {
                                "status": "ok",
                                "iou_threshold": 0.5,
                                "confidence_step": 0.01,
                                "recommended_thresholds": {
                                    "edge_low_threshold": 0.45,
                                    "edge_high_threshold": 0.8,
                                    "flink_filter_threshold": 0.65,
                                },
                                "per_class": [
                                    {
                                        "class_id": 0,
                                        "class_name": "electric_bike",
                                        "status": "ok",
                                    }
                                ],
                            },
                        },
                    },
                    ensure_ascii=False,
                ),
            )

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-threshold-detail-refresh"
        )
        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-detail-refresh"
        )
        assert detail.status_code == 200, detail.text
        detail_result = detail.json()["data"]["result"]
        edge_job_payload = detail_result["edge_validation_jobs"][0]
        assert edge_job_payload["job_id"] == job_id
        assert edge_job_payload["status"] == "COMPLETED"
        assert edge_job_payload["report_summary"] == "边端验证执行完成"
        assert edge_job_payload["result"]["execution"]["status"] == "ok"
        assert edge_job_payload["result"]["eval_summary"]["map50"] == 0.81
        assert isinstance(edge_job_payload["started_at"], str)
        assert isinstance(edge_job_payload["completed_at"], str)

        db.expire_all()
        refreshed = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-threshold-detail-refresh"
            )
            .first()
        )
        assert refreshed is not None
        persisted_result = json.loads(refreshed.platform_result_json)
        persisted_job = persisted_result["edge_validation_jobs"][0]
        assert persisted_job["status"] == "COMPLETED"
        assert persisted_job["result"]["execution"]["status"] == "ok"

        list_response = client.get(
            "/internal/v1/strategy-evolution/tasks",
            params={"page": 0, "size": 20},
        )
        assert list_response.status_code == 200, list_response.text
        rows = {
            item["external_task_id"]: item for item in list_response.json()["data"]
        }
        row_payload = rows["strategy-task-threshold-detail-refresh"]
        assert row_payload["latest_edge_job_status"] == "COMPLETED"
        assert row_payload["last_message"] == "边端验证执行完成"

    def test_threshold_experiment_builds_recommended_thresholds_from_refine_result(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
        _patch_completed_strategy_export(monkeypatch)
        callback_calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "start_job",
            lambda db, job_id, current_user: SimpleNamespace(status="PROCESSING"),
        )
        monkeypatch.setattr(
            strategy_service,
            "_send_outbound_callback",
            lambda db, row, result_payload=None, error_payload=None: (
                callback_calls.append(
                    {
                        "task_id": row.external_task_id,
                        "result": result_payload,
                        "error": error_payload,
                    }
                )
                or ("succeeded", None)
            ),
        )

        admin = _ensure_admin(db)
        _, export_id, done_sample_id, *_ = _prepare_threshold_fixture(
            db, tmp_path, admin.id
        )
        aicm_publish_job.save_job(
            "publish-model-threshold-refine",
            {
                "job_id": "publish-model-threshold-refine",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 530,
                },
                "log_lines": ["最新模型集 id=537"],
                "created_at": "2026-07-13T10:00:00",
                "updated_at": "2026-07-13T10:00:00",
            },
        )
        payload = _task_payload("strategy-task-threshold-recommend")
        payload["target"]["model_id"] = "537"

        create_response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )
        assert create_response.status_code == 202, create_response.text

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-threshold-recommend"
        )
        seed_detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-recommend"
        )
        assert seed_detail.status_code == 200, seed_detail.text

        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-threshold-recommend"
            )
            .first()
        )
        assert row is not None
        stored_result = json.loads(row.platform_result_json)
        job_id = int(stored_result["edge_validation_jobs"][0]["job_id"])

        edge_job = crud_yolo_edge_validation_job.get(db, job_id)
        with begin_transaction(db):
            crud_yolo_edge_validation_job.update(
                db,
                edge_job,
                status="COMPLETED",
                started_at=datetime(2026, 7, 13, 17, 45, 18),
                completed_at=datetime(2026, 7, 13, 17, 46, 44),
                report_summary="边端验证执行完成",
                result_json=json.dumps(
                    {
                        "execution_summary": {"status": "ok"},
                        "eval_summary": {
                            "status": "ok",
                            "map50": 0.81,
                            "business_threshold_analysis": {
                                "status": "ok",
                                "iou_threshold": 0.5,
                                "confidence_step": 0.01,
                                "recommended_thresholds": {
                                    "edge_low_threshold": 0.45,
                                    "edge_high_threshold": 0.8,
                                    "flink_filter_threshold": 0.65,
                                },
                            },
                        },
                        "request": {"sample_ids": [done_sample_id]},
                    },
                    ensure_ascii=False,
                ),
            )

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-threshold-recommend"
        )
        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-recommend"
        )
        assert detail.status_code == 200, detail.text
        data = detail.json()["data"]
        assert data["platform_status"] == "succeeded"
        recommendation = data["result"]["threshold_recommendation"]
        assert recommendation["recommendation_source"] == "edge_validation_formal_analysis"
        assert recommendation["recommended_thresholds"] == {
            "edge_low_threshold": 0.45,
            "edge_high_threshold": 0.8,
            "flink_filter_threshold": 0.65,
        }
        assert recommendation["internal_recommended_thresholds"]["conf_thres"] == 0.45
        assert recommendation["internal_recommended_thresholds"]["nms_thres"] is None
        assert recommendation["mapping_detail"]["edge_low_threshold"] == "formal_analysis.conf_threshold_gray_recall"
        assert recommendation["mapping_detail"]["flink_filter_threshold"] == "formal_analysis.conf_threshold_alert_f1"
        assert len(callback_calls) == 1
        callback_result = callback_calls[0]["result"]
        assert callback_calls[0]["task_id"] == "strategy-task-threshold-recommend"
        assert callback_calls[0]["error"] is None
        assert callback_result["business_thresholds"]["current"] == {
            "edge_low_threshold": 0.35,
            "edge_high_threshold": 0.8,
            "flink_filter_threshold": 0.6,
        }
        assert callback_result["business_thresholds"]["unit"] == "ratio_0_1"
        assert callback_result["business_thresholds"]["recommended"] == {
            "edge_low_threshold": 0.45,
            "edge_high_threshold": 0.8,
            "flink_filter_threshold": 0.65,
        }
        assert (
            callback_result["validation_thresholds"]["recommended"]["conf_thres"]
            == 0.45
        )
        assert callback_result["validation_thresholds"]["unit"] == "ratio_0_1"
        assert callback_result["threshold_contract"]["value_scale"] == "ratio_0_1"
        assert callback_result["threshold_contract"]["percent_values_allowed"] is False
        assert callback_result["threshold_contract"]["ranges"]["validation_thresholds"][
            "conf_thres"
        ] == {"minimum": 0.0, "maximum": 1.0}
        assert callback_result["threshold_contract"]["operating_ranges"]["conf_thres"] == {
            "minimum": 0.15,
            "maximum": 0.6,
        }
        assert callback_result["dataset_summary"]["sample_count"] == 1
        assert callback_result["candidate_edge_metrics"]["job_summary"]["avg_score"] == 0.81
        assert callback_result["candidate_edge_metrics"]["edge_accuracy_metrics"]["map50"] == 0.81

    def test_threshold_experiment_falls_back_to_legacy_done_task_for_same_package(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
        _patch_completed_strategy_export(monkeypatch)
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "start_job",
            lambda db, job_id, current_user: SimpleNamespace(status="PROCESSING"),
        )

        admin = _ensure_admin(db)
        task, export_id, done_sample_id, *_ = _prepare_threshold_fixture(
            db, tmp_path, admin.id
        )

        with begin_transaction(db):
            import_row = (
                db.query(ExperimentDatasetImport)
                .filter(
                    ExperimentDatasetImport.package_id
                    == f"experiment-dataset-threshold-window-{int(task.id)}"
                )
                .first()
            )
            assert import_row is not None
            persisted_task = crud_task.get(db, int(task.id))
            assert persisted_task is not None
            persisted_task.description = "\n".join(
                [
                    "source=experiment_dataset_formal",
                    f"package_id={import_row.package_id}",
                    "dataset_date=2026-06-24",
                    "event_type=electric_bike",
                    "target_signature=threshold-window",
                ]
            )
            db.add(persisted_task)
            duplicate_task = crud_task.create(
                db=db,
                task=Task(
                    name=persisted_task.name,
                    description=persisted_task.description,
                    tips="",
                    config=persisted_task.config,
                    media_type="IMAGE",
                    status="CONFIGURED",
                    created_by=admin.id,
                    updated_by=admin.id,
                ),
            )
            duplicate_attachment = crud_attachment.create(
                db,
                TaskAttachment(
                    filename="threshold_done.png",
                    url="/upload/threshold_done.png",
                    path="upload/threshold_done.png",
                    task_id=duplicate_task.id,
                    created_by=admin.id,
                    updated_by=admin.id,
                ),
            )
            crud_sample.batch(
                db=db,
                samples=[
                    TaskSample(
                        task_id=duplicate_task.id,
                        file_id=duplicate_attachment.id,
                        created_by=admin.id,
                        updated_by=admin.id,
                        state=SampleState.INCOMPLETE.value,
                        annotated_count=0,
                        data=json.dumps({"result": "{}"}, ensure_ascii=False),
                    )
                ],
            )
            import_row.formal_task_ids_json = json.dumps([duplicate_task.id])
            db.add(import_row)

        aicm_publish_job.save_job(
            "publish-model-fallback",
            {
                "job_id": "publish-model-fallback",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 530,
                },
                "log_lines": ["最新模型集 id=537"],
                "created_at": "2026-07-13T10:00:00",
                "updated_at": "2026-07-13T10:00:00",
            },
        )
        payload = _task_payload("strategy-task-threshold-fallback")
        payload["target"]["model_id"] = "537"

        response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )

        assert response.status_code == 202, response.text
        assert response.json()["message"] == "已创建 BM1688 导出任务"

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-threshold-fallback"
        )
        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-threshold-fallback"
        )
        assert detail.status_code == 200, detail.text
        result = detail.json()["data"]["result"]
        assert result["selected_sample_ids_by_task"] == {str(task.id): [done_sample_id]}
        assert task.id in result["matched_task_ids"]
        assert duplicate_task.id in result["matched_task_ids"]

    def test_detail_includes_image_compare_urls_for_edge_preview(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))
        _patch_completed_strategy_export(monkeypatch)
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "start_job",
            lambda db, job_id, current_user: SimpleNamespace(status="PROCESSING"),
        )

        admin = _ensure_admin(db)
        task, export_id, done_sample_id, *_ = _prepare_threshold_fixture(
            db, tmp_path, admin.id
        )
        aicm_publish_job.save_job(
            "publish-model-compare",
            {
                "job_id": "publish-model-compare",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 537,
                },
            },
        )
        payload = _task_payload("strategy-task-detail-compare")
        payload["target"]["model_id"] = "537"

        create_response = client.post(
            "/internal/v1/strategy-evolution/tasks",
            json=payload,
        )
        assert create_response.status_code == 202, create_response.text

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-detail-compare"
        )
        detail_seed = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-detail-compare"
        )
        assert detail_seed.status_code == 200, detail_seed.text

        row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-detail-compare"
            )
            .first()
        )
        assert row is not None
        seed_result = json.loads(row.platform_result_json)
        derived_export_id = int(seed_result["derived_export_id"])

        edge_job = crud_yolo_edge_validation_job.get_latest_for_export(
            db, export_id=derived_export_id
        )
        assert edge_job is not None

        job_root = (
            tmp_path
            / "yolo_bm1688_exports"
            / str(derived_export_id)
            / "manual_edge_validation"
            / f"job-{int(edge_job.id)}"
        )
        preview_path = (
            job_root / "server_eval" / "bmcv_preview" / "0000_threshold_done.png"
        )
        _write_test_image(preview_path)
        source_path = tmp_path / "upload" / "threshold_done.png"

        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "get_job",
            lambda db, job_id, current_user: SimpleNamespace(
                id=edge_job.id,
                export_id=derived_export_id,
                task_id=task.id,
                status="COMPLETED",
                mode="run_and_eval",
                image_count=1,
                labeled_image_count=1,
                unlabeled_image_count=0,
                threshold_source="strategy_evolution",
                workflow_stage="edge_validation",
                edge_result_status="pass",
                report_summary="done",
                progress={},
                thresholds={},
                ai_thresholds=None,
                result={
                    "pack": {
                        "job_root": str(job_root),
                        "file_mappings": [
                            {
                                "sample_id": done_sample_id,
                                "attachment_id": 1,
                                "source_kind": "sample",
                                "packed_name": "0000_threshold_done.png",
                                "original_name": "threshold_done.png",
                                "source_path": str(source_path),
                                "has_annotation": True,
                            }
                        ],
                    },
                    "prediction_summary": {
                        "image_count": 1,
                        "with_boxes": 1,
                        "total_boxes": 2,
                    },
                },
                error_message=None,
                started_at=None,
                completed_at=None,
                sample_ids=[done_sample_id],
                attachment_ids=[],
            ),
        )

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-detail-compare"
        )
        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-detail-compare"
        )
        assert detail.status_code == 200, detail.text
        detail_body = detail.json()["data"]
        edge_jobs = detail_body["result"]["edge_validation_jobs"]
        assert len(edge_jobs) == 1
        image_compare = edge_jobs[0]["result"]["image_compare"]
        assert image_compare["matched_count"] == 1
        assert image_compare["total_count"] == 1
        item = image_compare["items"][0]
        assert item["sample_id"] == done_sample_id
        assert item["has_result"] is True
        assert item["original_url"].endswith(
            "/api/v1/tasks/annotation-display/upload/threshold_done.png?max_side=1680"
        )
        assert item["result_url"].endswith(
            "/api/v1/tasks/annotation-display/yolo_bm1688_exports/"
            + str(derived_export_id)
            + "/manual_edge_validation/job-"
            + str(int(edge_job.id))
            + "/server_eval/bmcv_preview/0000_threshold_done.png?max_side=1680"
        )

    def test_split_endpoint_rejects_wrong_task_type_without_auth(
        self,
        client: TestClient,
    ) -> None:
        payload = _task_payload("strategy-task-3002")
        payload["task_type"] = "model_capability"

        response = client.post(
            "/internal/v1/strategy-evolution/threshold-experiments",
            json=payload,
        )

        assert response.status_code == 400
        assert response.json()["status"] == "rejected"

    def test_model_training_endpoint_creates_training_job_from_filtered_samples(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))

        admin = _ensure_admin(db)
        source_task_ref, export_id, selected_sample_ids = (
            _prepare_model_capability_fixture(
                db,
                tmp_path,
                admin.id,
            )
        )
        with begin_transaction(db):
            source_task = crud_task.get(db, int(source_task_ref.id))
            assert source_task is not None
            category = DatasetCategory(
                category_key="model_capability_category",
                name="模型能力分类",
                created_by=admin.id,
                updated_by=admin.id,
            )
            db.add(category)
            db.flush()
            category_id = int(category.id)
            source_task.dataset_category_id = category_id
            db.add(source_task)
        source_export_row = crud_yolo_bm1688_export.get(db, export_id)
        assert source_export_row is not None
        with begin_transaction(db):
            crud_yolo_bm1688_export.update_row(
                db,
                source_export_row,
                business_thresholds_json=json.dumps(
                    {
                        "effective": {
                            "edge_low_threshold": 0.35,
                            "edge_high_threshold": 0.8,
                            "flink_filter_threshold": 0.6,
                        }
                    },
                    ensure_ascii=False,
                ),
            )
        source_train_job_id = int(source_export_row.yolo_train_job_id)

        async def _fake_create_train_job(db, task_id, cmd, current_user):
            with begin_transaction(db):
                job = crud_yolo_train_job.create(
                    db=db,
                    task_id=task_id,
                    user_id=current_user.id,
                    model_weights=(
                        f"trained:{int(cmd.weights_ref_id)}"
                        if getattr(cmd, "weights_ref_id", None)
                        else (cmd.model_weights or "yolo11n.pt")
                    ),
                    epochs=cmd.epochs,
                    imgsz=cmd.imgsz,
                    category=cmd.category,
                )
            return YoloTrainJobResponse(
                id=job.id,
                task_id=task_id,
                status=job.status,
                category=job.category,
                model_weights=job.model_weights,
                epochs=job.epochs,
                imgsz=job.imgsz,
                merge_task_ids=[],
                merge_task_sample_counts=None,
                created_at=job.created_at,
                updated_at=job.updated_at,
                task_rect_classes=["electric_bike"],
            )

        def _fake_get_train_job_by_id(db, job_id, current_user):
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            return YoloTrainJobResponse(
                id=job.id,
                task_id=job.task_id,
                status=job.status,
                category=job.category,
                model_weights=job.model_weights,
                epochs=job.epochs,
                imgsz=job.imgsz,
                merge_task_ids=[],
                merge_task_sample_counts=None,
                created_at=job.created_at,
                updated_at=job.updated_at,
                task_rect_classes=["electric_bike"],
            )

        monkeypatch.setattr(
            strategy_service.yolo_train,
            "create_train_job",
            _fake_create_train_job,
        )
        monkeypatch.setattr(
            strategy_service.yolo_train,
            "get_train_job_by_id",
            _fake_get_train_job_by_id,
        )

        aicm_publish_job.save_job(
            "publish-model-capability",
            {
                "job_id": "publish-model-capability",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 530,
                },
                "log_lines": ["最新模型集 id=537"],
                "created_at": "2026-07-13T10:00:00",
                "updated_at": "2026-07-13T10:00:00",
            },
        )

        payload = _task_payload("strategy-task-model-capability")
        payload["task_type"] = "model_capability"
        payload["task_name"] = "night capability optimize"
        payload["target"]["model_id"] = "537"
        payload["experiment"] = {
            "capability_issue_type": "high_score_false_positive",
            "symptoms": ["night_scene_false_positive"],
            "labeling_required": True,
        }

        response = client.post(
            "/internal/v1/strategy-evolution/model-trainings",
            json=payload,
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "accepted"
        assert body["message"] == "已创建模型训练任务"
        assert "platform_status" not in body
        assert "result" not in body

        detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-model-capability"
        )
        assert detail.status_code == 200
        detail_body = detail.json()["data"]
        assert detail_body["task_type"] == "model_capability"
        assert detail_body["platform_status"] == "model_training_created"
        result = detail_body["result"]
        assert result["issue_type"] == "high_score_false_positive"
        assert result["symptoms"] == ["night_scene_false_positive"]
        assert result["labeling_required"] is True
        assert result["selected_sample_count"] == 2
        assert result["accepted_sample_count"] == 2
        assert result["selected_sample_ids_by_task"] == {
            str(result["matched_task_ids"][0]): selected_sample_ids
        }
        assert result["derived_task_id"] > 0
        assert len(result["model_training_jobs"]) == 1
        assert result["model_training_jobs"][0]["status"] == "PENDING"
        assert result["model_training_jobs"][0]["task_id"] == result["derived_task_id"]

        db.expire_all()
        strategy_row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-model-capability"
            )
            .first()
        )
        assert strategy_row is not None
        assert int(strategy_row.labelu_task_id or 0) == int(result["derived_task_id"])
        derived_task = crud_task.get(db, int(result["derived_task_id"]))
        assert derived_task is not None
        assert derived_task.status == "CONFIGURED"
        assert derived_task.dataset_category_id == category_id
        derived_samples = crud_sample.list_by_task(db, int(result["derived_task_id"]))
        assert len(derived_samples) == 2

    def test_model_training_auto_advances_to_publish_and_callback(
        self,
        client: TestClient,
        db: Session,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
        monkeypatch.setattr(settings, "BASE_DATA_DIR", str(tmp_path))

        admin = _ensure_admin(db)
        _, export_id, selected_sample_ids = _prepare_model_capability_fixture(
            db,
            tmp_path,
            admin.id,
        )
        source_export_row = crud_yolo_bm1688_export.get(db, export_id)
        assert source_export_row is not None
        with begin_transaction(db):
            crud_yolo_bm1688_export.update_row(
                db,
                source_export_row,
                business_thresholds_json=json.dumps(
                    {
                        "effective": {
                            "edge_low_threshold": 0.35,
                            "edge_high_threshold": 0.8,
                            "flink_filter_threshold": 0.6,
                        }
                    },
                    ensure_ascii=False,
                ),
            )
        source_train_job_id = int(source_export_row.yolo_train_job_id)

        callback_calls: list[dict[str, object]] = []

        def _train_detail(job):
            metrics = (
                {"map50": 0.82}
                if str(job.model_weights or "").startswith("trained:")
                else {"map50": 0.80}
            )
            return YoloTrainJobResponse(
                id=job.id,
                task_id=job.task_id,
                status=job.status,
                category=job.category,
                model_weights=job.model_weights,
                epochs=job.epochs,
                imgsz=job.imgsz,
                merge_task_ids=[],
                merge_task_sample_counts=None,
                created_at=job.created_at,
                updated_at=job.updated_at,
                task_rect_classes=["electric_bike"],
                weights_path=getattr(job, "weights_path", None),
                training_val_metrics=metrics,
                train_sample_count=2,
                val_sample_count=1,
                test_sample_count=1,
                total_sample_count=4,
                current_epoch=50 if str(job.status).upper() == "COMPLETED" else 1,
                progress_snapshot="done"
                if str(job.status).upper() == "COMPLETED"
                else "running",
                train_phase="fit",
                train_phase_label="训练中",
                train_phase_detail="自动推进测试",
                runtime_status="finished"
                if str(job.status).upper() == "COMPLETED"
                else "running",
                error_message=None,
            )

        async def _fake_create_train_job(db, task_id, cmd, current_user):
            with begin_transaction(db):
                job = crud_yolo_train_job.create(
                    db=db,
                    task_id=task_id,
                    user_id=current_user.id,
                    model_weights=f"trained:{int(cmd.weights_ref_id)}",
                    epochs=cmd.epochs,
                    imgsz=cmd.imgsz,
                    category=cmd.category,
                )
            return _train_detail(job)

        def _fake_get_train_job_by_id(db, job_id, current_user):
            job = crud_yolo_train_job.get(db=db, job_id=job_id)
            return _train_detail(job)

        async def _fake_start_export_for_job(db, task_id, job_id, current_user):
            with begin_transaction(db):
                export_row = crud_yolo_bm1688_export.create(
                    db=db,
                    user_id=current_user.id,
                    task_id=task_id,
                    yolo_train_job_id=job_id,
                    imgsz=640,
                )
            return SimpleNamespace(
                id=export_row.id,
                status=export_row.status,
                task_id=task_id,
            )

        def _edge_detail(row):
            result_payload = json.loads(row.result_json) if row.result_json else {}
            progress_payload = json.loads(row.progress_json) if row.progress_json else {}
            thresholds_payload = (
                json.loads(row.thresholds_json) if row.thresholds_json else {}
            )
            ai_thresholds_payload = (
                json.loads(row.ai_thresholds_json) if row.ai_thresholds_json else None
            )
            sample_ids = json.loads(row.sample_ids_json) if row.sample_ids_json else []
            return SimpleNamespace(
                id=row.id,
                export_id=row.export_id,
                task_id=row.task_id,
                status=row.status,
                mode=row.mode,
                image_count=row.image_count,
                labeled_image_count=row.labeled_image_count,
                unlabeled_image_count=row.unlabeled_image_count,
                threshold_source=row.threshold_source,
                workflow_stage="edge_validation",
                edge_result_status=(
                    result_payload.get("edge_result_status")
                    if isinstance(result_payload, dict)
                    else None
                ),
                report_summary=row.report_summary,
                progress=progress_payload,
                thresholds=thresholds_payload,
                ai_thresholds=ai_thresholds_payload,
                result=result_payload,
                error_message=row.error_message,
                started_at=row.started_at,
                completed_at=row.completed_at,
                sample_ids=sample_ids,
                attachment_ids=[],
            )

        def _fake_create_edge_job(db, cmd, current_user):
            with begin_transaction(db):
                row = crud_yolo_edge_validation_job.create(
                    db=db,
                    user_id=current_user.id,
                    task_id=cmd.task_id,
                    export_id=cmd.export_id,
                    mode=cmd.mode,
                    threshold_source="strategy_evolution",
                    sample_ids_json=json.dumps(cmd.sample_ids or []),
                    thresholds_json=json.dumps(
                        {
                            "conf_thres": 0.45,
                            "nms_thres": 0.65,
                            "match_iou_thres": 0.5,
                            "source": "strategy_evolution",
                        }
                    ),
                    ai_thresholds_json=None,
                    image_count=len(cmd.sample_ids or []),
                    labeled_image_count=len(cmd.sample_ids or []),
                    unlabeled_image_count=0,
                )
            return _edge_detail(row)

        def _fake_start_edge_job(db, job_id, current_user):
            row = crud_yolo_edge_validation_job.get(db, int(job_id))
            with begin_transaction(db):
                crud_yolo_edge_validation_job.update(
                    db,
                    row,
                    status="PROCESSING",
                    started_at=datetime(2026, 7, 16, 23, 50, 0),
                )
            return SimpleNamespace(status="PROCESSING")

        def _fake_get_edge_job(db, job_id, current_user):
            row = crud_yolo_edge_validation_job.get(db, int(job_id))
            return _edge_detail(row)

        async def _fake_start_publish_job(
            db, current_user, export_id, publish_mode="automatic"
        ):
            assert publish_mode == "automatic"
            job_id = f"publish-auto-{int(export_id)}"
            aicm_publish_job.save_job(
                job_id,
                {
                    "job_id": job_id,
                    "export_id": export_id,
                    "status": "PROCESSING",
                    "result": {"ok": False},
                    "created_at": "2026-07-16T23:55:00",
                    "updated_at": "2026-07-16T23:55:00",
                },
            )
            return SimpleNamespace(job_id=job_id, status="PROCESSING", result={})

        monkeypatch.setattr(
            strategy_service.yolo_train,
            "create_train_job",
            _fake_create_train_job,
        )
        monkeypatch.setattr(
            strategy_service.yolo_train,
            "get_train_job_by_id",
            _fake_get_train_job_by_id,
        )
        monkeypatch.setattr(
            strategy_service.yolo_bm1688_export,
            "start_export_for_job",
            _fake_start_export_for_job,
        )
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "create_job",
            _fake_create_edge_job,
        )
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "start_job",
            _fake_start_edge_job,
        )
        monkeypatch.setattr(
            strategy_service.yolo_edge_validation,
            "get_job",
            _fake_get_edge_job,
        )
        monkeypatch.setattr(
            strategy_service.yolo_aicm_publish,
            "start_publish_job",
            _fake_start_publish_job,
        )
        monkeypatch.setattr(
            strategy_service,
            "_training_weights_command_fields",
            lambda db, model_id: (
                {
                    "weights_source": "trained",
                    "weights_ref_id": source_train_job_id,
                },
                {
                    "model_id": model_id,
                    "resolved_by": "test",
                    "export_id": export_id,
                    "source_train_job_id": source_train_job_id,
                },
            ),
        )
        monkeypatch.setattr(
            strategy_service,
            "_send_outbound_callback",
            lambda db, row, result_payload=None, error_payload=None: (
                callback_calls.append(
                    {
                        "task_id": row.external_task_id,
                        "result": result_payload,
                        "error": error_payload,
                    }
                )
                or strategy_service.crud_strategy_task_integration.record_outbound_callback_result(
                    db=db,
                    row=row,
                    callback_status="succeeded",
                    callback_attempts=int(row.outbound_callback_attempts or 0) + 1,
                    callback_last_error=None,
                )
                or ("succeeded", None)
            ),
        )

        aicm_publish_job.save_job(
            "publish-model-capability-auto",
            {
                "job_id": "publish-model-capability-auto",
                "export_id": export_id,
                "user_id": admin.id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": export_id,
                    "model_set_id": 530,
                },
                "log_lines": ["最新模型集 id=537"],
                "created_at": "2026-07-13T10:00:00",
                "updated_at": "2026-07-13T10:00:00",
            },
        )

        payload = _task_payload("strategy-task-model-capability-auto")
        payload["task_type"] = "model_capability"
        payload["task_name"] = "night capability optimize auto"
        payload["target"]["model_id"] = "537"
        payload["target"]["event_type"] = "electric_bike"
        payload["target"]["scene_type"] = "night"
        payload["target"]["strategy_id"] = "strategy-v1"
        payload["experiment"] = {
            "capability_issue_type": "high_score_false_positive",
            "symptoms": ["night_scene_false_positive"],
            "labeling_required": True,
        }

        create_response = client.post(
            "/internal/v1/strategy-evolution/model-trainings",
            json=payload,
        )
        assert create_response.status_code == 202, create_response.text
        assert create_response.json()["message"] == "已创建模型训练任务"

        first_detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-model-capability-auto"
        )
        assert first_detail.status_code == 200, first_detail.text
        first_result = first_detail.json()["data"]["result"]
        assert first_detail.json()["data"]["platform_status"] == "model_training_created"
        assert first_result["selected_sample_count"] == 2
        assert first_result["selected_sample_ids_by_task"] == {
            str(first_result["matched_task_ids"][0]): selected_sample_ids
        }
        candidate_train_job_id = int(first_result["derived_train_job_id"])

        candidate_train_job = crud_yolo_train_job.get(db, candidate_train_job_id)
        with begin_transaction(db):
            crud_yolo_train_job.update_status(
                db=db,
                job=candidate_train_job,
                status="COMPLETED",
                weights_path="weights/candidate_best.pt",
            )

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-model-capability-auto"
        )
        second_detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-model-capability-auto"
        )
        assert second_detail.status_code == 200, second_detail.text
        second_body = second_detail.json()["data"]
        assert second_body["platform_status"] == "export_created"
        derived_export_id = int(second_body["result"]["derived_export_id"])

        candidate_export = crud_yolo_bm1688_export.get(db, derived_export_id)
        with begin_transaction(db):
            crud_yolo_bm1688_export.update_row(
                db,
                candidate_export,
                status="COMPLETED",
            )

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-model-capability-auto"
        )
        third_detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-model-capability-auto"
        )
        assert third_detail.status_code == 200, third_detail.text
        third_body = third_detail.json()["data"]
        assert third_body["platform_status"] == "edge_validation_created"
        source_jobs = third_body["result"]["source_model_edge_validation_jobs"]
        candidate_jobs = third_body["result"]["edge_validation_jobs"]
        assert len(source_jobs) == 1
        assert len(candidate_jobs) == 1
        refreshed_strategy_row = crud_strategy_task_integration.get_by_external_task_id(
            db=db,
            external_task_id="strategy-task-model-capability-auto",
        )
        assert refreshed_strategy_row.derived_edge_validation_job_id == int(
            candidate_jobs[0]["job_id"]
        )

        for job_payload, map50 in (
            (source_jobs[0], 0.81),
            (candidate_jobs[0], 0.85),
        ):
            row = crud_yolo_edge_validation_job.get(db, int(job_payload["job_id"]))
            with begin_transaction(db):
                crud_yolo_edge_validation_job.update(
                    db,
                    row,
                    status="COMPLETED",
                    completed_at=datetime(2026, 7, 16, 23, 57, 0),
                    report_summary="edge validation done",
                    result_json=json.dumps(
                        {
                            "edge_result_status": "pass",
                            "eval_summary": {
                                "map50": map50,
                                "class_metrics": [
                                    {
                                        "id": 0,
                                        "name": "electric_bike",
                                        "f1": 0.85 if map50 > 0.82 else 0.81,
                                    }
                                ],
                                "business_threshold_analysis": {
                                    "status": "ok",
                                    "iou_threshold": 0.5,
                                    "confidence_step": 0.01,
                                    "recommended_thresholds": {
                                        "edge_low_threshold": 0.44,
                                        "edge_high_threshold": 0.82,
                                        "flink_filter_threshold": 0.62,
                                    },
                                    "per_class": [
                                        {
                                            "class_id": 0,
                                            "class_name": "electric_bike",
                                            "status": "ok",
                                        }
                                    ],
                                },
                            },
                        },
                        ensure_ascii=False,
                    ),
                )

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-model-capability-auto"
        )
        fourth_detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-model-capability-auto"
        )
        assert fourth_detail.status_code == 200, fourth_detail.text
        fourth_body = fourth_detail.json()["data"]
        assert fourth_body["platform_status"] == "publish_created"
        publish_job = fourth_body["result"]["publish_job"]
        publish_job_id = publish_job["job_id"]

        aicm_publish_job.save_job(
            publish_job_id,
            {
                "job_id": publish_job_id,
                "export_id": derived_export_id,
                "status": "COMPLETED",
                "result": {
                    "ok": True,
                    "export_id": derived_export_id,
                    "model_set_id": 530,
                    "precision": "int8",
                },
                "log_lines": ["最新模型集 id=537"],
                "created_at": "2026-07-16T23:58:00",
                "updated_at": "2026-07-16T23:58:00",
            },
        )

        assert strategy_service.auto_advance_strategy_task(
            "strategy-task-model-capability-auto"
        )
        final_detail = client.get(
            "/internal/v1/strategy-evolution/tasks/strategy-task-model-capability-auto"
        )
        assert final_detail.status_code == 200, final_detail.text
        final_body = final_detail.json()["data"]
        assert final_body["platform_status"] == "succeeded"
        assert final_body["result"]["comparison"]["passed"] is True
        assert final_body["result"]["publish_job"]["status"] == "COMPLETED"
        assert len(callback_calls) == 1
        assert callback_calls[0]["task_id"] == "strategy-task-model-capability-auto"
        assert callback_calls[0]["error"] is None
        callback_result = callback_calls[0]["result"]
        assert callback_result["target"]["modelId"] == "537"
        assert callback_result["dataset_summary"]["sample_count"] == 2
        assert callback_result["source_model"]["train_job_id"] == source_train_job_id
        assert callback_result["candidate_model"]["train_job_id"] == candidate_train_job_id
        assert callback_result["candidate_model"]["published_model_id"] == 537
        assert callback_result["candidate_model"]["model_set_id"] == 537
        assert callback_result["candidate_model"]["publication_status"] == "published"
        assert callback_result["candidate_model"]["precision"] == "int8"
        assert callback_result["candidate_model"]["artifact"]["availability"] == "unavailable"
        assert callback_result["candidate_model"]["artifact"]["missing_reason"] == (
            "artifact_manifest_not_available"
        )
        assert callback_result["optimization"]["status"] == "applied"
        assert callback_result["threshold_contract"]["value_scale"] == "ratio_0_1"
        assert callback_result["threshold_contract"]["percent_values_allowed"] is False
        assert (
            callback_result["candidate_model"]["training_metrics"]["training_val_metrics"]["map50"]
            == 0.82
        )
        assert (
            callback_result["candidate_edge_metrics"]["edge_accuracy_metrics"]["map50"]
            == 0.85
        )
        assert callback_result["comparison"]["passed"] is True
        assert callback_result["comparison"]["quality_gate"]["passed"] is True
        assert callback_result["comparison"]["quality_gate"]["failed_checks"] == []
        assert callback_result["comparison"]["delta_score"] == 0.04
        assert callback_result["business_thresholds"]["current"] == {
            "edge_low_threshold": 0.35,
            "edge_high_threshold": 0.8,
            "flink_filter_threshold": 0.6,
        }
        assert callback_result["business_thresholds"]["recommended"] == {
            "edge_low_threshold": 0.44,
            "edge_high_threshold": 0.82,
            "flink_filter_threshold": 0.62,
        }
        assert callback_result["business_thresholds"]["unit"] == "ratio_0_1"
        assert callback_result["validation_thresholds"]["unit"] == "ratio_0_1"
        assert callback_result["threshold_binding"]["published_model_id"] == 537
        assert callback_result["threshold_binding"]["threshold_unit"] == "ratio_0_1"
        assert callback_result["publish"]["allowed"] is True
        assert callback_result["publish"]["status"] == "COMPLETED"

        db.expire_all()
        strategy_row = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id
                == "strategy-task-model-capability-auto"
            )
            .first()
        )
        assert strategy_row is not None
        assert strategy_row.platform_status == "succeeded"
        assert strategy_row.outbound_callback_status == "succeeded"
        persisted_export = crud_yolo_bm1688_export.get(
            db, int(strategy_row.derived_export_id)
        )
        assert persisted_export is not None
        assert persisted_export.published_model_id == 537
        persisted_business = json.loads(persisted_export.business_thresholds_json)
        persisted_validation = json.loads(persisted_export.validation_thresholds_json)
        persisted_binding = json.loads(persisted_export.threshold_binding_json)
        assert persisted_business["effective"]["edge_low_threshold"] == 0.44
        assert persisted_business["effective"]["flink_filter_threshold"] == 0.62
        assert persisted_business["unit"] == "ratio_0_1"
        assert persisted_validation["effective"]["conf_thres"] == 0.45
        assert persisted_validation["effective"]["nms_thres"] == 0.65
        assert persisted_validation["unit"] == "ratio_0_1"
        assert persisted_binding["published_model_id"] == 537
        assert persisted_binding["threshold_unit"] == "ratio_0_1"

    def test_callback_returns_document_shape_for_missing_task(
        self,
        client: TestClient,
    ) -> None:
        response = client.post(
            "/api/v1/strategy-evolution/platform-callbacks",
            json={
                "schema_version": "v1",
                "callback_event_id": "callback-missing",
                "external_task_id": "missing-task",
                "task_type": "threshold_experiment",
                "status": "running",
            },
        )

        assert response.status_code == 404
        body = response.json()
        assert body["body"] is None
        assert body["status"] == 404
        assert "missing-task" in body["msg"]

    def test_callback_retry_route_accepts_callback_url_override(
        self,
        client: TestClient,
        monkeypatch,
    ) -> None:
        called: dict[str, str | None] = {}

        def _retry(*, db, external_task_id, callback_url=None):
            called["external_task_id"] = external_task_id
            called["callback_url"] = callback_url
            return StrategyEvolutionTaskDetailResponse(
                external_task_id=external_task_id,
                task_name="callback retry",
                task_type="model_capability",
                target={},
                data_window={},
                platform_status="comparison_failed",
                outbound_callback_status="succeeded",
                outbound_callback_attempts=2,
            )

        monkeypatch.setattr(strategy_service, "retry_outbound_callback", _retry)
        response = client.post(
            "/internal/v1/strategy-evolution/tasks/strategy-task-retry/callback-retry",
            json={
                "callback_url": (
                    "http://10.10.11.28:21088/api/v1/strategy-evolution/platform-callbacks"
                )
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["data"]["outbound_callback_status"] == "succeeded"
        assert called == {
            "external_task_id": "strategy-task-retry",
            "callback_url": (
                "http://10.10.11.28:21088/api/v1/strategy-evolution/platform-callbacks"
            ),
        }

    def test_public_strategy_task_api_supports_create_list_detail_and_conflict(
        self,
        client: TestClient,
        db: Session,
    ) -> None:
        payload = _task_payload("public-strategy-task-1")

        created = client.post("/api/v1/strategy-tasks", json=payload)
        assert created.status_code == 202, created.text
        assert created.json()["status"] == "accepted"

        repeated = client.post("/api/v1/strategy-tasks", json=payload)
        assert repeated.status_code == 202, repeated.text

        changed = dict(payload)
        changed["task_name"] = "同编号但请求内容变化"
        conflict = client.post("/api/v1/strategy-tasks", json=changed)
        assert conflict.status_code == 409, conflict.text
        assert "请求内容与首次提交不一致" in conflict.json()["message"]

        listed = client.get(
            "/api/v1/strategy-tasks",
            params={
                "page": 0,
                "size": 20,
                "keyword": "public-strategy-task-1",
                "external_task_id": "public-strategy-task-1",
                "modelId": "model-1",
            },
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["meta_data"]["total"] == 1
        assert listed.json()["data"][0]["external_task_id"] == "public-strategy-task-1"

        listed_by_q = client.get(
            "/api/v1/strategy-tasks",
            params={
                "page": 0,
                "size": 20,
                "q": "public-strategy-task-1",
                "external_task_id": "public-strategy-task-1",
                "modelId": "model-1",
            },
        )
        assert listed_by_q.status_code == 200, listed_by_q.text
        assert listed_by_q.json()["meta_data"]["total"] == 1

        detail = client.get("/api/v1/strategy-tasks/public-strategy-task-1")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["external_task_id"] == "public-strategy-task-1"
        assert detail.json()["data"]["task_name"] == payload["task_name"]

        rows = (
            db.query(StrategyTaskIntegration)
            .filter(StrategyTaskIntegration.external_task_id == "public-strategy-task-1")
            .all()
        )
        assert len(rows) == 1
