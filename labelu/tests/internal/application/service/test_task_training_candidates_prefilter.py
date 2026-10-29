import json

from labelu.internal.adapter.persistence import crud_annotation_taxonomy, crud_task, crud_user
from labelu.internal.application.service import task as task_service
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus


def test_training_candidate_prefilter_shrinks_candidate_set_without_breaking_fallback(db):
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None

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
    crud_annotation_taxonomy.label_preset_create(
        db,
        slug="pytest_garbage",
        name="垃圾暴露",
        description=None,
        sort_order=20,
        items=[
            {
                "label_key": "垃圾",
                "label_value": "garbage",
                "color": "#52c41a",
                "sort_order": 0,
            }
        ],
    )

    explicit_target = crud_task.create(
        db,
        Task(
            name="prefilter-explicit-target",
            description="",
            tips="",
            config=json.dumps({"defaultRectPresetId": "pytest_road"}, ensure_ascii=False),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            training_ready=True,
        ),
    )
    explicit_other = crud_task.create(
        db,
        Task(
            name="prefilter-explicit-other",
            description="",
            tips="",
            config=json.dumps({"defaultRectPresetId": "pytest_garbage"}, ensure_ascii=False),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            training_ready=True,
        ),
    )
    fallback_no_preset = crud_task.create(
        db,
        Task(
            name="prefilter-fallback-no-preset",
            description="",
            tips="",
            config=json.dumps(
                {
                    "tools": [
                        {
                            "tool": "rectTool",
                            "config": {
                                "attributes": [{"key": "路灯", "value": "street_light"}]
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            training_ready=True,
        ),
    )
    fallback_unknown_preset = crud_task.create(
        db,
        Task(
            name="prefilter-fallback-unknown-preset",
            description="",
            tips="",
            config=json.dumps(
                {
                    "defaultRectPresetId": "legacy_unknown_slug",
                    "tools": [
                        {
                            "tool": "rectTool",
                            "config": {
                                "attributes": [{"key": "路灯", "value": "street_light"}]
                            },
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            training_ready=True,
        ),
    )
    consistent_pool = crud_task.create(
        db,
        Task(
            name="prefilter-consistent-pool",
            description="",
            tips="",
            config=json.dumps(
                {
                    "defaultRectPresetId": "pytest_road",
                    "consistent_pool": {
                        "kind": "train_filtered_consistent",
                        "rect_preset_slug": "pytest_road",
                    },
                },
                ensure_ascii=False,
            ),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            training_ready=True,
        ),
    )
    training_not_ready = crud_task.create(
        db,
        Task(
            name="prefilter-not-ready",
            description="",
            tips="",
            config=json.dumps({"defaultRectPresetId": "pytest_road"}, ensure_ascii=False),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            training_ready=False,
        ),
    )
    training_unknown = crud_task.create(
        db,
        Task(
            name="prefilter-training-ready-unknown",
            description="",
            tips="",
            config=json.dumps({"defaultRectPresetId": "pytest_road"}, ensure_ascii=False),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            training_ready=None,
        ),
    )
    video_task = crud_task.create(
        db,
        Task(
            name="prefilter-video",
            description="",
            tips="",
            config=json.dumps({"defaultRectPresetId": "pytest_road"}, ensure_ascii=False),
            media_type=MediaType.VIDEO.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            training_ready=True,
        ),
    )
    db.commit()

    _, preset_classes_by_slug = task_service._load_preset_classes_by_slug(db)
    tasks = task_service._list_training_candidate_prefiltered_tasks(
        db,
        user,
        rect_preset_slug="pytest_road",
        preset_classes_by_slug=preset_classes_by_slug,
    )
    task_ids = {task.id for task in tasks}

    assert explicit_target.id in task_ids
    assert fallback_no_preset.id in task_ids
    assert fallback_unknown_preset.id in task_ids
    assert training_unknown.id in task_ids

    assert explicit_other.id not in task_ids
    assert consistent_pool.id not in task_ids
    assert training_not_ready.id not in task_ids
    assert video_task.id not in task_ids
