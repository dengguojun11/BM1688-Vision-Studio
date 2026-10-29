from __future__ import annotations

import json

import pytest
from sqlalchemy.exc import IntegrityError

from labelu.internal.adapter.persistence import (
    crud_annotation_taxonomy,
    crud_attachment,
    crud_sample,
    crud_task,
    crud_training_task,
    crud_user,
    crud_yolo_train_job,
)
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.annotation_contract import SampleAnnotationScope
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    DatasetCategory,
)
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_consistent_pool_registry import (
    YoloConsistentPoolRegistry,
)


def _rect_tool_config(slug: str) -> str:
    return json.dumps(
        {
            "defaultRectPresetId": slug,
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [
                            {"key": "路灯", "value": "street_light"},
                        ]
                    },
                }
            ],
        },
        ensure_ascii=False,
    )


def _ensure_admin_user(db) -> int:
    admin = crud_user.get_user_by_username(db, username="admin@123.com")
    if admin and admin.id:
        return int(admin.id)
    admin = crud_user.create(
        db=db,
        user=User(
            username="admin@123.com",
            hashed_password=get_password_hash("Admin@123"),
        ),
    )
    return int(admin.id)


def test_find_or_create_pool_task_normalizes_owner_to_admin(tmp_path, db, monkeypatch):
    from labelu.internal.application.service import (
        yolo_train_consistent_pool as service,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))

    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    assert current_user is not None

    with begin_transaction(db):
        admin_id = _ensure_admin_user(db)
        db.add(
            AnnotationLabel(
                label_key="路灯",
                label_value="street_light",
                normalized_label_value="street_light",
                annotation_kind="bbox",
                color="#aabbcc",
                attributes_json="[]",
                status="active",
                created_by=admin_id,
                updated_by=admin_id,
            )
        )
        crud_annotation_taxonomy.label_preset_create(
            db,
            slug="pytest_road",
            name="道路设施",
            description=None,
            sort_order=10,
            items=[
                {
                    "label_key": "路灯",
                    "label_value": "street_light",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        )
        template_task = crud_task.create(
            db,
            Task(
                name="template-task",
                description="",
                tips="",
                config=_rect_tool_config("pytest_road"),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        existing_pool = crud_task.create(
            db,
            Task(
                name="????-训练筛选一致",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "defaultRectPresetId": "pytest_road",
                        "consistent_pool": {
                            "kind": "train_filtered_consistent",
                            "category": "????",
                            "rect_preset_slug": "pytest_road",
                            "label_set_signature": "pytest-road-signature",
                            "class_names": ["street_light"],
                        },
                    },
                    ensure_ascii=False,
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        existing_pool.collaborators.append(current_user)
        db.add(existing_pool)

    pool_task = service._find_or_create_pool_task(
        db=db,
        category="道路设施",
        template_task=template_task,
        owner_id=current_user.id,
        label_set_signature="pytest-road-signature",
        class_names=["street_light"],
    )
    reused_from_other_category = service._find_or_create_pool_task(
        db=db,
        category="另一个数据集分类",
        template_task=template_task,
        owner_id=current_user.id,
        label_set_signature="pytest-road-signature",
        class_names=["street_light"],
    )

    admin_id = _ensure_admin_user(db)
    assert int(pool_task.id) == int(existing_pool.id)
    assert int(reused_from_other_category.id) == int(existing_pool.id)
    assert pool_task.name == service.pool_task_name("道路设施")
    assert reused_from_other_category.name == service.pool_task_name("道路设施")
    repaired_config = json.loads(reused_from_other_category.config)
    assert repaired_config["consistent_pool"]["category"] == "道路设施"
    assert int(pool_task.created_by) == admin_id
    assert pool_task.source_type == "consistent_pool"
    assert pool_task.source_external_task_id == "pytest-road-signature"
    assert pool_task.collaborators == []
    assert pool_task.dataset_category_id is None
    assert reused_from_other_category.dataset_category_id is None
    registry_rows = (
        db.query(YoloConsistentPoolRegistry)
        .filter(
            YoloConsistentPoolRegistry.label_set_signature == "pytest-road-signature"
        )
        .all()
    )
    assert len(registry_rows) == 1
    assert registry_rows[0].category_scope_key == "uncategorized"
    assert registry_rows[0].dataset_category_id is None
    assert int(registry_rows[0].task_id) == int(existing_pool.id)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(
                YoloConsistentPoolRegistry(
                    label_set_signature="pytest-road-signature",
                )
            )
            db.flush()
    contract = service.annotation_contract_service.current_contract(db, pool_task.id)
    assert contract is not None
    assert contract.status == "draft"


def test_find_or_create_pool_task_scopes_same_labels_by_dataset_category(
    tmp_path, db, monkeypatch
):
    from labelu.internal.application.service import (
        yolo_train_consistent_pool as service,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    assert current_user is not None

    with begin_transaction(db):
        _ensure_admin_user(db)
        first_category = DatasetCategory(
            category_key="pytest_first_pool_scope",
            name="第一分类",
            status="active",
            version=1,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        second_category = DatasetCategory(
            category_key="pytest_second_pool_scope",
            name="第二分类",
            status="active",
            version=1,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add_all([first_category, second_category])
        db.flush()
        template_task = crud_task.create(
            db,
            Task(
                name="category-scoped-template",
                config=_rect_tool_config("pytest_category_scoped_pool"),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=current_user.id,
                updated_by=current_user.id,
                dataset_category_id=int(first_category.id),
            ),
        )

    first_pool = service._find_or_create_pool_task(
        db=db,
        category=first_category.name,
        dataset_category=first_category,
        template_task=template_task,
        owner_id=current_user.id,
        label_set_signature="same-labels-in-two-categories",
        class_names=["street_light"],
    )
    second_pool = service._find_or_create_pool_task(
        db=db,
        category=second_category.name,
        dataset_category=second_category,
        template_task=template_task,
        owner_id=current_user.id,
        label_set_signature="same-labels-in-two-categories",
        class_names=["street_light"],
    )

    assert int(first_pool.id) != int(second_pool.id)
    assert int(first_pool.dataset_category_id) == int(first_category.id)
    assert int(second_pool.dataset_category_id) == int(second_category.id)
    registry_rows = (
        db.query(YoloConsistentPoolRegistry)
        .filter(
            YoloConsistentPoolRegistry.label_set_signature
            == "same-labels-in-two-categories"
        )
        .order_by(YoloConsistentPoolRegistry.id)
        .all()
    )
    assert [row.category_scope_key for row in registry_rows] == [
        f"category:{int(first_category.id)}",
        f"category:{int(second_category.id)}",
    ]


def test_resolve_source_task_ids_for_create_allows_only_builtin_admin(db, monkeypatch):
    from labelu.internal.application.service import yolo_train_merge as service
    from labelu.internal.adapter.persistence import crud_sample

    monkeypatch.setattr(
        service, "validate_merge_tasks_sample_labels", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        service, "validate_yolo_training_prerequisites", lambda *args, **kwargs: None
    )

    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    assert current_user is not None

    with begin_transaction(db):
        admin_id = _ensure_admin_user(db)
        crud_annotation_taxonomy.label_preset_create(
            db,
            slug="pytest_road_pool",
            name="道路筛选池",
            description=None,
            sort_order=10,
            items=[
                {
                    "label_key": "路灯",
                    "label_value": "street_light",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        )
        pool_task = crud_task.create(
            db,
            Task(
                name="道路筛选池-训练筛选一致",
                description="",
                tips="",
                config=json.dumps(
                    {
                        "tools": [
                            {
                                "tool": "rectTool",
                                "config": {
                                    "attributes": [
                                        {"key": "路灯", "value": "street_light"},
                                    ]
                                },
                            }
                        ],
                        "consistent_pool": {
                            "kind": "train_filtered_consistent",
                            "rect_preset_slug": "pytest_road_pool",
                        },
                    },
                    ensure_ascii=False,
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                created_by=admin_id,
                updated_by=admin_id,
                source_type="consistent_pool",
                source_system="labelu",
                source_external_task_id="pytest-road-pool-signature",
                training_ready=False,
            ),
        )
        crud_sample.batch(
            db=db,
            samples=[
                TaskSample(
                    task_id=pool_task.id,
                    file_id=1,
                    created_by=admin_id,
                    updated_by=admin_id,
                    state=SampleState.DONE.value,
                    data="{}",
                )
            ],
        )

    with pytest.raises(LabelUException) as denied:
        service.resolve_source_task_ids_for_create(
            db,
            primary_task_id=pool_task.id,
            cmd=CreateYoloTrainJobCommand(rect_preset_slug="pytest_road_pool"),
            current_user=current_user,
        )
    assert getattr(denied.value, "status_code", None) == 403
    monkeypatch.setattr(settings, "TASK_SUPERUSER_USERNAMES", current_user.username)
    regular_superuser_task_ids = {
        row.id
        for row in crud_task.visible_query(
            db,
            owner_id=current_user.id,
            list_all_tasks=True,
        ).all()
    }
    assert pool_task.id not in regular_superuser_task_ids

    admin = crud_user.get_user_by_username(db=db, username="admin@123.com")
    admin_task_ids = {
        row.id
        for row in crud_task.visible_query(
            db,
            owner_id=admin.id,
            list_all_tasks=True,
        ).all()
    }
    assert pool_task.id in admin_task_ids
    ids, per_task = service.resolve_source_task_ids_for_create(
        db,
        primary_task_id=pool_task.id,
        cmd=CreateYoloTrainJobCommand(rect_preset_slug="pytest_road_pool"),
        current_user=admin,
    )

    assert ids == [pool_task.id]
    assert per_task[pool_task.id] == 1


def test_compare_completion_never_creates_consistent_pool_task(monkeypatch):
    from labelu.internal.application.service import (
        yolo_train_consistent_pool as service,
    )

    monkeypatch.setattr(settings, "YOLO_CONSISTENT_POOL_ENABLED", True)
    monkeypatch.setattr(settings, "YOLO_CONSISTENT_POOL_AUTO", True)

    def unexpected_sync(*args, **kwargs):
        raise AssertionError("训练完成后不应自动同步或创建一致数据集任务")

    monkeypatch.setattr(service, "sync_consistent_pool_from_job", unexpected_sync)
    monkeypatch.setattr(service.threading, "Thread", unexpected_sync)

    assert service.schedule_consistent_pool_sync_after_compare(123) is None


def test_sync_consistent_pool_skips_strategy_model_capability_job(
    tmp_path, db, monkeypatch
):
    from labelu.internal.application.service import (
        yolo_train_consistent_pool as service,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))

    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    assert current_user is not None

    with begin_transaction(db):
        source_task = crud_task.create(
            db,
            Task(
                name="strategy-model-capability-source-task",
                description="",
                tips="",
                config=_rect_tool_config("pytest_strategy_skip_pool"),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        job = crud_yolo_train_job.create(
            db,
            task_id=int(source_task.id),
            user_id=int(current_user.id),
            model_weights="yolo11n.pt",
            epochs=50,
            imgsz=640,
            category="专家回流任务121-模型优化",
        )
        job.status = "COMPLETED"
        db.add(job)

    class StrategyRow:
        task_type = "model_capability"
        external_task_id = "strategy-task-model-capability-121"

    monkeypatch.setattr(
        service.crud_strategy_task_integration,
        "get_by_derived_train_job_id",
        lambda db, derived_train_job_id: StrategyRow(),
    )

    report = service.sync_consistent_pool_from_job(int(job.id))

    assert report["status"] == "skipped"
    assert report["task_type"] == "model_capability"
    assert "不生成训练筛选一致任务" in report["message"]
    assert (
        db.query(Task)
        .filter(Task.name == service.pool_task_name("专家回流任务121-模型优化"))
        .count()
        == 0
    )


def test_label_dimension_job_is_not_skipped_before_manifest_check(
    tmp_path, db, monkeypatch
):
    from labelu.internal.application.service import (
        yolo_train_consistent_pool as service,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))

    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    assert current_user is not None

    with begin_transaction(db):
        source_task = crud_task.create(
            db,
            Task(
                name="label-dimension-source-task",
                description="",
                tips="",
                config=_rect_tool_config("pytest_skip_pool"),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        training_task = crud_training_task.create(
            db,
            owner_id=int(current_user.id),
            name="零散垃圾训练任务",
            mode="label_dimension",
            status="PROCESSING",
            primary_task_id=int(source_task.id),
            selected_presets_snapshot=[],
            selected_label_items_snapshot=[],
            merged_class_names_snapshot=[],
            source_tasks_snapshot=[],
            ignored_tasks_snapshot=[],
        )
        job = crud_yolo_train_job.create(
            db,
            task_id=int(source_task.id),
            user_id=int(current_user.id),
            model_weights="yolo11n.pt",
            epochs=50,
            imgsz=640,
            category="垃圾暴露",
            training_task_id=int(training_task.id),
        )
        job.status = "COMPLETED"
        db.add(job)

    report = service.sync_consistent_pool_from_job(int(job.id))

    assert report["status"] == "skipped"
    assert report["message"] == "标注对比 manifest 不存在"
    assert (
        db.query(Task).filter(Task.name == service.pool_task_name("垃圾暴露")).count()
        == 0
    )


def test_sync_consistent_pool_splits_samples_back_to_source_categories(
    tmp_path, db, monkeypatch
):
    from labelu.internal.application.service import (
        yolo_train_consistent_pool as service,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setattr(
        service.crud_strategy_task_integration,
        "get_by_derived_train_job_id",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "_copy_sample_into_pool",
        lambda **kwargs: int(kwargs["source_sample"].id),
    )
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    assert current_user is not None

    with begin_transaction(db):
        _ensure_admin_user(db)
        first_category = DatasetCategory(
            category_key="pytest_sync_first",
            name="同步第一分类",
            status="active",
            version=1,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        second_category = DatasetCategory(
            category_key="pytest_sync_second",
            name="同步第二分类",
            status="active",
            version=1,
            created_by=current_user.id,
            updated_by=current_user.id,
        )
        db.add_all([first_category, second_category])
        db.flush()
        source_tasks = []
        source_samples = []
        for index, dataset_category in enumerate(
            [first_category, second_category], start=1
        ):
            source_task = crud_task.create(
                db,
                Task(
                    name=f"sync-source-{index}",
                    config=_rect_tool_config("pytest_sync_split"),
                    media_type=MediaType.IMAGE.value,
                    status=TaskStatus.FINISHED.value,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                    dataset_category_id=int(dataset_category.id),
                ),
            )
            attachment = crud_attachment.create(
                db,
                TaskAttachment(
                    filename=f"sync-{index}.jpg",
                    path=f"upload/{source_task.id}/sync-{index}.jpg",
                    url="",
                    task_id=int(source_task.id),
                    created_by=current_user.id,
                    updated_by=current_user.id,
                ),
            )
            sample = TaskSample(
                task_id=int(source_task.id),
                file_id=int(attachment.id),
                state=SampleState.DONE.value,
                data="{}",
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            crud_sample.batch(db=db, samples=[sample])
            db.flush()
            source_tasks.append(source_task)
            source_samples.append(sample)
        job = crud_yolo_train_job.create(
            db,
            task_id=int(source_tasks[0].id),
            user_id=int(current_user.id),
            model_weights="yolo11n.pt",
            epochs=1,
            imgsz=640,
            category="跨分类训练",
        )
        job.status = "COMPLETED"
        job.label_set_signature = "pytest-sync-same-label-signature"
        db.add(job)

    match_iou = float(settings.YOLO_CONSISTENT_POOL_MATCH_IOU)
    manifest_items = [
        {
            "task_id": int(source_task.id),
            "sample_id": int(source_sample.id),
            "fn": 0,
            "fp": 0,
            "tp": 1,
            "gt": 1,
            "pred": 1,
            "issues": ["ok"],
            "match_iou_threshold": match_iou,
            "min_matched_iou": match_iou,
        }
        for source_task, source_sample in zip(source_tasks, source_samples)
    ]
    monkeypatch.setattr(
        service,
        "_load_manifest",
        lambda job_id: {
            "class_names": ["street_light"],
            "items": manifest_items,
        },
    )

    report = service.sync_consistent_pool_from_job(int(job.id))

    assert report["status"] == "completed"
    assert len(report["pools"]) == 2
    assert {row["dataset_category_id"] for row in report["pools"]} == {
        int(first_category.id),
        int(second_category.id),
    }
    assert {tuple(row["source_task_ids"]) for row in report["pools"]} == {
        (int(source_tasks[0].id),),
        (int(source_tasks[1].id),),
    }
    assert (
        db.query(YoloConsistentPoolRegistry)
        .filter(
            YoloConsistentPoolRegistry.label_set_signature
            == "pytest-sync-same-label-signature"
        )
        .count()
        == 2
    )


def test_reconcile_merges_same_signature_and_deduplicates_filename(
    tmp_path, db, monkeypatch
):
    from labelu.internal.application.service import (
        yolo_train_consistent_pool as service,
    )

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path / "media"))
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    assert current_user is not None

    def pool_config(category: str) -> str:
        return json.dumps(
            {
                "tools": [
                    {
                        "tool": "rectTool",
                        "config": {
                            "attributes": [
                                {"key": "路灯", "value": "street_light"},
                            ]
                        },
                    }
                ],
                "consistent_pool": {
                    "kind": "train_filtered_consistent",
                    "category": category,
                    "label_set_signature": "same-label-signature",
                    "class_names": ["street_light"],
                },
            },
            ensure_ascii=False,
        )

    with begin_transaction(db):
        admin_id = _ensure_admin_user(db)
        db.add(
            AnnotationLabel(
                label_key="路灯",
                label_value="street_light",
                normalized_label_value="street_light",
                annotation_kind="bbox",
                color="#bbccdd",
                attributes_json="[]",
                status="active",
                created_by=admin_id,
                updated_by=admin_id,
            )
        )
        db.flush()
        first = crud_task.create(
            db,
            Task(
                name="第一批-训练筛选一致",
                config=pool_config("第一批"),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        second = crud_task.create(
            db,
            Task(
                name="第二批-训练筛选一致",
                config=pool_config("第二批"),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.FINISHED.value,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        second.collaborators.append(current_user)
        db.add(second)
        sample_rows = []
        for task, filenames in (
            (first, ["same.jpg", "SAME.JPG", "first-only.jpg"]),
            (second, ["same.jpg", "second-only.jpg"]),
        ):
            for filename in filenames:
                attachment = crud_attachment.create(
                    db,
                    TaskAttachment(
                        filename=filename,
                        url="",
                        path=f"upload/{task.id}/{filename}",
                        task_id=task.id,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    ),
                )
                sample_rows.append(
                    TaskSample(
                        task_id=task.id,
                        file_id=attachment.id,
                        created_by=current_user.id,
                        updated_by=current_user.id,
                        state=SampleState.DONE.value,
                        data="{}",
                    )
                )
        crud_sample.batch(db=db, samples=sample_rows)

    preview = service.reconcile_consistent_pools(db, apply=False)
    group = next(
        row
        for row in preview["groups"]
        if row["label_set_signature"] == "same-label-signature"
    )
    assert group["task_ids"] == [first.id, second.id]
    assert group["sample_rows_before"] == 5
    assert group["unique_filename_count"] == 3
    assert group["duplicate_filename_rows"] == 2
    assert group["would_move_unique_samples"] == 1
    assert group["collaborators_to_remove"] == 1
    assert group["metadata_repair_task_ids"] == [first.id, second.id]

    with begin_transaction(db):
        applied = service.reconcile_consistent_pools(db, apply=True)
    applied_group = next(
        row
        for row in applied["groups"]
        if row["label_set_signature"] == "same-label-signature"
    )
    assert applied_group["canonical_task_id"] == first.id
    assert applied_group["moved_unique_samples"] == 1
    assert applied_group["duplicate_rows_removed"] == 1
    assert applied_group["collaborators_removed"] == 1
    assert crud_sample.task_list_sample_total(db=db, task_id=first.id) == 3
    assert crud_task.get(db=db, task_id=second.id) is None
    repaired = crud_task.get(db=db, task_id=first.id)
    assert int(repaired.created_by) == admin_id
    assert repaired.source_type == "consistent_pool"
    assert repaired.training_ready is True
    contract = service.annotation_contract_service.current_contract(db, first.id)
    assert contract is not None
    assert contract.status == "active"
    assert (
        db.query(SampleAnnotationScope)
        .filter(SampleAnnotationScope.contract_id == contract.id)
        .count()
        == 3
    )
