import json

from labelu.internal.application.service.dataset_analysis import (
    _label_target,
    build_dataset_analysis,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.adapter.persistence import crud_user


def _sample_data(*, weather: str, scene: str, labels: list[str]) -> str:
    result = {
        "tagTool": {
            "toolName": "tagTool",
            "result": [{"value": {"weather": [weather], "sceneType": [scene]}}],
        },
        "rectTool": {
            "toolName": "rectTool",
            "result": [
                {"label": label, "x": 1, "y": 1, "width": 10, "height": 10}
                for label in labels
            ],
        },
    }
    return json.dumps({"result": json.dumps(result, ensure_ascii=False)})


def test_build_dataset_analysis_aggregates_dimensions_labels_and_task_rows(db):
    user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        category = DatasetCategory(
            category_key="street_scene",
            name="街景数据",
            status="active",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(category)
        db.flush()
        task = Task(
            name="街景任务 A",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            updated_by=user.id,
            dataset_category_id=category.id,
            config=json.dumps(
                {
                    "tools": [
                        {
                            "tool": "rectTool",
                            "config": {"attributes": [{"key": "车辆", "value": "car"}]},
                        },
                        {
                            "tool": "tagTool",
                            "config": {
                                "attributes": [
                                    {
                                        "key": "天气",
                                        "value": "weather",
                                        "type": "enum",
                                        "options": [
                                            {"key": "晴天", "value": "sunny"},
                                            {"key": "雨天", "value": "rainy"},
                                        ],
                                    }
                                ]
                            },
                        },
                    ]
                }
            ),
        )
        db.add(task)
        db.flush()
        db.add_all(
            [
                TaskSample(
                    task_id=task.id,
                    data=_sample_data(
                        weather="sunny", scene="road", labels=["car", "car"]
                    ),
                    state=SampleState.DONE.value,
                ),
                TaskSample(
                    task_id=task.id,
                    data=_sample_data(weather="rainy", scene="road", labels=["car"]),
                    state=SampleState.NEW.value,
                ),
            ]
        )
        db.flush()
        category_id = category.id

    result = build_dataset_analysis(db, user, dataset_category_id=category_id)

    assert result.totals.total_images == 2
    assert result.totals.done_images == 1
    assert result.totals.label_instance_count == 3
    weather = next(item for item in result.dimensions if item.key == "weather")
    assert {item.value: item.count for item in weather.items} == {"晴天": 1, "雨天": 1}
    car = next(item for item in result.labels if item.label == "car")
    assert car.image_count == 2
    assert car.target_count == 134
    assert car.gap_count == 132
    car_gap = next(
        item
        for item in result.recommendations
        if item.dimension == "label" and item.value == "car"
    )
    assert car_gap.current_count == 2
    assert car_gap.target_count == 134
    assert result.tasks[0].pending_images == 1


def test_build_dataset_analysis_scopes_new_flow_by_dataset_id(db):
    user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        dataset = Dataset(
            owner_id=user.id,
            name="按数据集分析",
            status="ACTIVE",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(dataset)
        db.flush()
        task = Task(
            name="按数据集分析任务",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user.id,
            updated_by=user.id,
            dataset_id=dataset.id,
            config=json.dumps({"tools": []}),
        )
        db.add(task)
        db.flush()
        db.add(
            TaskSample(
                task_id=task.id,
                data=_sample_data(weather="晴天", scene="道路", labels=["car"]),
                state=SampleState.DONE.value,
            )
        )
        db.flush()
        dataset_id = dataset.id

    result = build_dataset_analysis(db, user, dataset_id=dataset_id)

    assert result.dataset_id == dataset_id
    assert result.dataset_name == "按数据集分析"
    assert result.category_id == 0
    assert result.totals.total_images == 1


def test_label_target_uses_dataset_share_and_test_instance_support():
    assert (
        _label_target(
            total_images=958,
            image_count=5,
            instance_count=7,
        )
        == 143
    )
    assert (
        _label_target(
            total_images=958,
            image_count=505,
            instance_count=1204,
        )
        == 96
    )
    assert (
        _label_target(
            total_images=958,
            image_count=0,
            instance_count=0,
        )
        == 200
    )
