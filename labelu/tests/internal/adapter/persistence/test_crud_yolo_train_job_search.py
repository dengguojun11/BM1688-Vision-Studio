from labelu.internal.adapter.persistence import crud_user, crud_yolo_train_job
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus


def _test_user_id(db):
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    user_id = int(user.id)
    db.rollback()
    return user_id


def test_completed_model_search_matches_related_fields_and_escapes_like_tokens(db):
    user_id = _test_user_id(db)
    with db.begin():
        category = DatasetCategory(
            category_key="road_%_category",
            name="Road % category",
            description="category with wildcard characters",
            status="active",
            created_by=user_id,
            updated_by=user_id,
        )
        db.add(category)
        db.flush()

        dataset = Dataset(
            name="dataset-special-road",
            description="dataset used by the trained model search test",
            status="ACTIVE",
            owner_id=user_id,
            created_by=user_id,
            updated_by=user_id,
            dataset_category_id=category.id,
        )
        db.add(dataset)
        db.flush()

        task = Task(
            name="task-special-road",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user_id,
            dataset_id=dataset.id,
            dataset_category_id=category.id,
        )
        db.add(task)
        db.flush()

        other_task = Task(
            name="task-ordinary",
            description="",
            tips="",
            config="{}",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.FINISHED.value,
            created_by=user_id,
        )
        db.add(other_task)
        db.flush()

        training_task = TrainingTask(
            owner_id=user_id,
            name="training-special-road",
            mode="label_dimension",
            status="COMPLETED",
            primary_task_id=task.id,
            dataset_category_id=category.id,
        )
        db.add(training_task)
        db.flush()

        matching = YoloTrainJob(
            task_id=task.id,
            created_by=user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="base-road.pt",
            weights_path="yolo_train/job-special/weights/road_%_best.pt",
            category="road_%_category",
            dataset_category_id=category.id,
            training_task_id=training_task.id,
        )
        other = YoloTrainJob(
            task_id=other_task.id,
            created_by=user_id,
            status=YoloTrainStatus.COMPLETED.value,
            model_weights="base-other.pt",
            weights_path="yolo_train/job-other/weights/other_best.pt",
            category="other",
        )
        db.add_all([matching, other])
        db.flush()
        matching_id = int(matching.id)
        other_id = int(other.id)

    def ids_for(search):
        rows, total = crud_yolo_train_job.list_completed_models_for_user(
            db,
            user_id,
            search=search,
            page=0,
            size=20,
            task_access_unrestricted=True,
        )
        return [int(job.id) for job, _ in rows], total

    assert ids_for("base-road.pt") == ([matching_id], 1)
    assert ids_for("task-special-road") == ([matching_id], 1)
    assert ids_for("training-special-road") == ([matching_id], 1)
    assert ids_for("dataset-special-road") == ([matching_id], 1)
    assert ids_for(str(other_id)) == ([other_id], 1)
    assert ids_for("Road % category") == ([matching_id], 1)

    # A percent sign in user input is literal, not a wildcard that returns both rows.
    assert ids_for("%") == ([matching_id], 1)
    # The underscore in the category is likewise matched literally.
    assert ids_for("road_%_category") == ([matching_id], 1)
