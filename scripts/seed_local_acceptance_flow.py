"""Seed one repeatable, local-only training/evaluation acceptance flow.

This harness creates no model weights and never calls a provider, compiler, or
production callback. It exists to give the local 7GAI UI a real completed
training job and a trusted evaluation result for browser acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path


MARKER = "7GAI-local-acceptance-flow"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=".tmp/db/playwright_e2e.db",
        help="SQLite database path, relative to the repository root",
    )
    parser.add_argument(
        "--media-root",
        default=".tmp/local-acceptance-media",
        help="Local media/evidence directory",
    )
    return parser.parse_args()


def _annotation(index: int) -> dict:
    return {
        "file": {"width": 1280, "height": 720},
        "result": {
            "rectTool": {
                "result": [
                    {
                        "id": f"local-rect-{index}",
                        "label": "vehicle",
                        "x": 10,
                        "y": 20,
                        "width": 100,
                        "height": 80,
                    }
                ]
            }
        },
    }


def main() -> int:
    args = _parse_args()
    database = Path(args.database).resolve()
    media_root = Path(args.media_root).resolve()
    if database.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise SystemExit("refusing non-SQLite local acceptance database")

    os.environ["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    os.environ["APP_ENV"] = "local"
    os.environ["LABELU_AUTO_TRAINING_POOL_ENABLED"] = "true"
    os.environ["LABELU_AUTO_TRAINING_SCHEDULER_ENABLED"] = "true"

    from labelu.internal.application.service.auto_training_evaluator import (
        materialize_evaluation_request,
    )
    from labelu.internal.application.service.auto_training_local_evaluator import (
        run_local_simulated_evaluation,
    )
    from labelu.internal.application.service.auto_training_plan import (
        replace_evaluation_samples,
    )
    from labelu.internal.common.db import SessionLocal, begin_transaction
    from labelu.internal.common.label_contract import annotation_sha256
    from labelu.internal.common import db as db_module
    from labelu.internal.domain.models.annotation_contract import (
        SampleAnnotationScope,
        TaskAnnotationContract,
    )
    from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
    from labelu.internal.domain.models.attachment import TaskAttachment
    from labelu.internal.domain.models.auto_training import (
        AutoTrainingEvaluationRun,
        AutoTrainingPlan,
        AutoTrainingPlanVersion,
        AutoTrainingRound,
    )
    from labelu.internal.domain.models.auto_training_governance import (
        TrainingEvaluationDataset,
    )
    from labelu.internal.domain.models.sample import TaskSample
    from labelu.internal.domain.models.task import Task
    from labelu.internal.domain.models.user import User
    from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
    from labelu.internal.common.config import settings

    if not settings.DATABASE_URL.startswith("sqlite"):
        raise SystemExit("refusing non-SQLite local acceptance database")
    # This harness is the explicit local acceptance writer. The application
    # default stays fail-closed; only this SQLite-only process opens the plan
    # write gate, never a server or production environment.
    settings.AUTO_TRAINING_PLAN_WRITES_ENABLED = True
    settings.MEDIA_ROOT = str(media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    db_module.init_tables()

    db = SessionLocal()
    try:
        with begin_transaction(db):
            existing = db.query(AutoTrainingPlan).filter_by(name=MARKER).first()
            if existing:
                evaluation = (
                    db.query(AutoTrainingEvaluationRun)
                    .join(AutoTrainingRound, AutoTrainingRound.id == AutoTrainingEvaluationRun.round_id)
                    .filter(AutoTrainingRound.plan_id == existing.id)
                    .order_by(AutoTrainingEvaluationRun.id.desc())
                    .first()
                )
                print(json.dumps({"reused": True, "plan_id": existing.id, "evaluation_id": evaluation.id if evaluation else None}))
                return 0

            user = db.query(User).filter_by(id=1).one()
            category = db.query(DatasetCategory).filter_by(id=8).first()
            if category is None:
                category = DatasetCategory(
                    id=8,
                    category_key="garbage_street",
                    name="垃圾街道",
                    status="active",
                    created_by=user.id,
                    updated_by=user.id,
                )
                db.add(category)
                db.flush()

            plan = AutoTrainingPlan(
                # Local acceptance follows the current no-project business scope.
                project_id=None,
                owner_id=user.id,
                name=MARKER,
                series_type="yolo_detection",
                dataset_category_id=category.id,
                label_set_signature="a" * 64,
                class_order_signature="b" * 64,
                status="ROUND_ACTIVE",
                version=1,
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(plan)
            db.flush()
            config = {
                "auto_stop": {"overall_recall_threshold": 0.95},
                "quality_policy_binding": {"signature": "q" * 64},
                "evaluation_binding": {"signature": "e" * 64},
                "training_policy": {"max_technical_failures": 2},
            }
            version = AutoTrainingPlanVersion(
                plan_id=plan.id,
                version_no=1,
                status="APPROVED",
                config_json=json.dumps(config, sort_keys=True),
                config_sha256=hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
                quality_policy_signature="q" * 64,
                evaluation_binding_json=json.dumps(config["evaluation_binding"]),
                missing_fields_json="[]",
                validation_errors_json="[]",
                created_by=user.id,
                approved_by=user.id,
            )
            db.add(version)
            db.flush()
            plan.current_version_id = version.id

            round_row = AutoTrainingRound(
                plan_id=plan.id,
                plan_version_id=version.id,
                round_no=1,
                state="PT_EVALUATING",
                trigger_type="AUTO",
                trigger_snapshot_json=json.dumps({"source": "local_acceptance"}),
                dataset_signature="d" * 64,
                execution_signature="local-acceptance-round-1",
                stage_status_json=json.dumps({"SNAPSHOT": "COMPLETED", "TRAIN": "COMPLETED"}),
                stage_errors_json="{}",
                retry_count_json="{}",
                eligible_sample_count=1,
                train_count=1,
                val_count=0,
                test_count=0,
            )
            db.add(round_row)
            db.flush()
            plan.active_round_id = round_row.id

            task = Task(
                name="7GAI local acceptance task",
                media_type="IMAGE",
                status="INPROGRESS",
                created_by=user.id,
                updated_by=user.id,
                dataset_category_id=category.id,
                source_type="internal",
            )
            db.add(task)
            db.flush()
            contract = TaskAnnotationContract(
                task_id=task.id,
                revision=1,
                annotation_kind="bbox",
                label_ids_json="[1]",
                labels_snapshot_json="[]",
                label_set_signature="a" * 64,
                class_order_signature="b" * 64,
                status="active",
                created_by=user.id,
            )
            db.add(contract)
            db.flush()
            task.current_annotation_contract_id = contract.id

            # Keep the acceptance lineage real: the completed job must point to
            # the task whose contract, image and sample are created below.
            job = YoloTrainJob(
                task_id=task.id,
                project_id=None,
                created_by=user.id,
                status="COMPLETED",
                source_type="auto_training",
                job_type="yolo_detection",
                model_weights="yolo11n.pt",
                weights_path="runs/local-acceptance/best.pt",
                auto_training_plan_id=plan.id,
                auto_training_round_id=round_row.id,
                execution_signature="local-acceptance-job-1",
                metrics_summary=json.dumps({"overall_recall": 0.98}),
            )
            db.add(job)
            db.flush()
            round_row.yolo_train_job_id = job.id

            annotation = _annotation(0)
            relative_path = f"local-acceptance/{task.id}-0.jpg"
            image_path = media_root / relative_path
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"7GAI-local-acceptance-image")
            attachment = TaskAttachment(
                filename=image_path.name,
                path=relative_path,
                task_id=task.id,
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(attachment)
            db.flush()
            sample = TaskSample(
                inner_id=1,
                task_id=task.id,
                file_id=attachment.id,
                created_by=user.id,
                updated_by=user.id,
                state="DONE",
                annotated_count=1,
                data=json.dumps(annotation),
            )
            db.add(sample)
            db.flush()
            db.add(
                SampleAnnotationScope(
                    sample_id=sample.id,
                    contract_id=contract.id,
                    completion_status="complete",
                    annotation_sha256=annotation_sha256(annotation),
                    reviewed_by=user.id,
                )
            )
            db.flush()

            evaluation_rows = []
            for kind in ("VALIDATION", "BENCHMARK", "SEALED_ACCEPTANCE"):
                row = TrainingEvaluationDataset(
                    owner_id=user.id,
                    name=f"{MARKER}-{kind.lower()}",
                    dataset_kind=kind,
                    series_type="yolo_detection",
                    dataset_category_id=category.id,
                    label_set_signature="a" * 64,
                    version_no=1,
                    status="DRAFT",
                    manifest_rel_path=f"draft/{MARKER}-{kind.lower()}.json",
                    manifest_sha256=(kind[0].lower() * 64),
                    sample_count=0,
                    grouping_policy_json="{}",
                    created_by=user.id,
                )
                db.add(row)
                db.flush()
                replace_evaluation_samples(
                    db,
                    user=user,
                    dataset_id=row.id,
                    samples=[
                        {
                            "task_id": task.id,
                            "sample_id": sample.id,
                            "split_role": "test",
                            "identity_group_key": MARKER,
                            "capture_group_key": "local-camera",
                        }
                    ],
                )
                row.status = "APPROVED"
                row.approved_by = user.id
                evaluation_rows.append(row)

            config["evaluation_binding"] = {
                "validation_dataset_id": evaluation_rows[0].id,
                "benchmark_dataset_id": evaluation_rows[1].id,
                "sealed_acceptance_dataset_id": evaluation_rows[2].id,
                "signature": "e" * 64,
            }
            version.config_json = json.dumps(config, sort_keys=True)
            version.evaluation_binding_json = json.dumps(config["evaluation_binding"], sort_keys=True)
            evaluation = materialize_evaluation_request(db, round_id=round_row.id, job_id=job.id)
            plan_id, round_id, job_id, evaluation_id = plan.id, round_row.id, job.id, evaluation.id

        with begin_transaction(db):
            result = run_local_simulated_evaluation(
                db,
                evaluation_run_id=evaluation_id,
                metrics={
                    "overall_metrics": {"overall_recall": 0.98},
                    "per_label_metrics": {"vehicle": {"recall": 0.97}},
                },
            )
        print(json.dumps({"reused": False, "plan_id": plan_id, "round_id": round_id, "job_id": job_id, "evaluation_id": evaluation_id, "decision_code": result["decision_code"], "local_simulation": result["local_simulation"]}))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
