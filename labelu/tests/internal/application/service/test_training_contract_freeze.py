from __future__ import annotations

import json

import pytest
from PIL import Image

from labelu.internal.adapter.persistence import (
    crud_task,
    crud_training_task,
    crud_user,
    crud_yolo_train_job,
)
from labelu.internal.application.command.training_task import (
    CreateLabelDimensionTrainingTaskCommand,
)
from labelu.internal.application.service import annotation_contract
from labelu.internal.application.service import training_task as svc
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.annotation_taxonomy import (
    AnnotationLabel,
    GlobalTagDimension,
    GlobalTagOption,
    RuntimePublishTemplate,
    TrainingRecipe,
)
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.annotation_contract import SampleAnnotationScope
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.yolo_train_job import YoloTrainStatus


def _write_test_image(path) -> None:
    Image.new("RGB", (64, 64), color=(32, 64, 96)).save(path, format="JPEG")


def _seed_contract_training_case(
    db,
    tmp_path,
    monkeypatch,
    *,
    primary_label_value: str = "contract_person",
    extra_label_values: tuple[str, ...] = (),
):
    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", tmp_path)
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    image_path = tmp_path / "contract-source.jpg"
    _write_test_image(image_path)
    annotation_data = {
        "result": json.dumps(
            {
                "rectTool": {
                    "result": [
                        {
                            "label": primary_label_value,
                            "x": 1,
                            "y": 2,
                            "width": 10,
                            "height": 20,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
    }
    with begin_transaction(db):
        label = AnnotationLabel(
            label_key="Person",
            label_value=primary_label_value,
            normalized_label_value=primary_label_value.lower(),
            annotation_kind="bbox",
            color="#1677ff",
            status="active",
            version=1,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(label)
        db.flush()
        extra_labels = []
        for index, value in enumerate(extra_label_values, start=1):
            extra_label = AnnotationLabel(
                label_key=value,
                label_value=value,
                normalized_label_value=value.lower(),
                annotation_kind="bbox",
                color=f"#{0x220000 + index:06x}",
                status="active",
                version=1,
                created_by=user.id,
                updated_by=user.id,
            )
            db.add(extra_label)
            db.flush()
            extra_labels.append(extra_label)
        db.add(
            TrainingRecipe(
                recipe_key="pytest_detection",
                version=1,
                name="Pytest detection",
                job_type="yolo_detection",
                model_family="yolov11",
                annotation_kind="bbox",
                component_contract_json="{}",
                default_hyperparams_json="{}",
                status="active",
            )
        )
        db.add(
            RuntimePublishTemplate(
                template_key="pytest-runtime",
                name="Pytest runtime",
                environment_id="default",
                runtime_profile="pytest_detection",
                interface_version=1,
                aicm_model_set_id="123",
                capability_contract_json="{}",
                status="active",
            )
        )
        task = crud_task.create(
            db,
            Task(
                name="Contract source task",
                description="",
                tips="",
                config="{}",
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                training_ready=True,
                created_by=user.id,
                updated_by=user.id,
            ),
        )
        contract = annotation_contract.create_contract(
            db,
            task=task,
            label_ids=[label.id] + [row.id for row in extra_labels],
            created_by=user.id,
        )
        attachment = TaskAttachment(
            filename=image_path.name,
            url=f"/media/{image_path.name}",
            path=image_path.name,
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
            annotated_count=1,
            data=json.dumps(annotation_data, ensure_ascii=False),
            state=SampleState.DONE.value,
        )
        db.add(sample)
        db.flush()
        annotation_contract.upsert_sample_scope(
            db,
            sample=sample,
            contract=contract,
            annotation_data=annotation_data,
            state=SampleState.DONE.value,
            reviewed_by=user.id,
            explicit_negative=False,
        )
    return user, label, task, sample, image_path, annotation_data


def test_task_dimension_creation_derives_labels_from_selected_task(
    db, tmp_path, monkeypatch
):
    user, label, task, _sample, _image_path, _annotation_data = (
        _seed_contract_training_case(db, tmp_path, monkeypatch)
    )
    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *args, **kwargs: None
    )
    with begin_transaction(db):
        db.query(SampleAnnotationScope).filter(
            SampleAnnotationScope.sample_id == _sample.id
        ).delete(synchronize_session=False)
        contract = annotation_contract.current_contract(db, task.id)
        contract.status = "review_required"
        db.add(contract)
        task.training_ready = False
        db.add(task)

    preview = svc.preview_label_dimension_training_task(
        db,
        current_user=user,
        cmd=CreateLabelDimensionTrainingTaskCommand(
            name="Task-first preview",
            selected_task_ids=[task.id],
            label_ids=[],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
        ),
    )
    assert preview.selected_label_ids == [label.id]
    assert preview.merged_classes[0].class_name == "contract_person"
    assert preview.included_task_count == 1

    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=CreateLabelDimensionTrainingTaskCommand(
            name="Task-first training",
            selected_task_ids=[task.id],
            label_ids=[],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
            model_weights="yolo11n.pt",
            weights_source="preset",
            epochs=1,
            imgsz=640,
        ),
    )
    detail = svc.get_training_task_detail(
        db, current_user=user, training_task_id=created.training_task_id
    )
    assert created.mode == "task_dimension"
    assert detail.primary_task_id == task.id
    assert detail.selected_label_ids == [label.id]
    assert detail.class_order_signature
    _config, samples, per_task_counts, source_task_ids = (
        svc.build_label_dimension_dataset_context(
            db,
            current_user=user,
            training_task_id=created.training_task_id,
        )
    )
    assert [sample.id for sample in samples] == [_sample.id]
    assert per_task_counts == {task.id: 1}
    assert source_task_ids == [task.id]


def test_contract_preview_reports_condition_counts_and_zero_match_reason(
    db, tmp_path, monkeypatch
):
    user, label, task, sample, _image_path, _annotation_data = (
        _seed_contract_training_case(db, tmp_path, monkeypatch)
    )
    result = {
        "rectTool": {
            "result": [
                {
                    "label": label.label_value,
                    "x": 1,
                    "y": 2,
                    "width": 10,
                    "height": 20,
                }
            ]
        },
        "tagTool": {
            "result": [
                {"value": {"sceneType": ["urbanArterial"]}},
                {"value": {"lighting": ["nightWithLight"]}},
            ]
        },
    }
    with begin_transaction(db):
        scene_dimension = GlobalTagDimension(
            field_key="sceneType", name_cn="场景类型", sort_order=1
        )
        db.add(scene_dimension)
        db.flush()
        db.add_all(
            [
                GlobalTagOption(
                    dimension_id=scene_dimension.id,
                    name_cn="城市主干道",
                    value_en="urbanArterial",
                    sort_order=1,
                ),
                GlobalTagOption(
                    dimension_id=scene_dimension.id,
                    name_cn="背街小巷",
                    value_en="backAlley",
                    sort_order=2,
                ),
            ]
        )
        sample.data = json.dumps(
            {"result": json.dumps(result, ensure_ascii=False)}, ensure_ascii=False
        )
        db.add(sample)

    matching = svc.preview_label_dimension_training_task(
        db,
        current_user=user,
        cmd=CreateLabelDimensionTrainingTaskCommand(
            name="Matching condition preview",
            selected_task_ids=[task.id],
            label_ids=[],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
            sample_conditions={"sceneType": ["urbanArterial"]},
        ),
    )
    assert matching.included_image_count == 1
    assert matching.sample_condition_option_counts == {
        "sceneType": {"urbanArterial": 1},
        "lighting": {"nightWithLight": 1},
    }

    empty = svc.preview_label_dimension_training_task(
        db,
        current_user=user,
        cmd=CreateLabelDimensionTrainingTaskCommand(
            name="Empty condition preview",
            selected_task_ids=[task.id],
            label_ids=[],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
            sample_conditions={"sceneType": ["backAlley"]},
        ),
    )
    assert empty.included_image_count == 0
    assert empty.sample_condition_option_counts["sceneType"] == {"urbanArterial": 1}
    assert empty.ignored_tasks[0].reason_code == "NO_SAMPLES_MATCH_CONDITIONS"


def test_garbage_contract_keeps_non_garbage_as_model_output(db, tmp_path, monkeypatch):
    business_class_names = [
        "garbage_street_dump",
        "garbage_street_pack",
        "garbage_street_scattered",
        "object_box",
        "garbage_street_can",
        "non_garbage",
        "garbage_bulky",
    ]
    user, _label, task, positive, _image_path, _annotation_data = (
        _seed_contract_training_case(
            db,
            tmp_path,
            monkeypatch,
            primary_label_value=business_class_names[0],
            extra_label_values=tuple(business_class_names[1:]),
        )
    )
    labels = (
        db.query(AnnotationLabel)
        .filter(AnnotationLabel.label_value.in_(business_class_names))
        .order_by(AnnotationLabel.id)
        .all()
    )
    label_ids = [int(row.id) for row in labels]
    non_garbage_label = next(row for row in labels if row.label_value == "non_garbage")
    contract = annotation_contract.current_contract(db, task.id)
    assert contract is not None
    negative_path = tmp_path / "garbage-background.jpg"
    _write_test_image(negative_path)
    negative_data = {
        "result": json.dumps(
            {
                "rectTool": {
                    "result": [
                        {
                            "label": "non_garbage",
                            "x": 1,
                            "y": 2,
                            "width": 10,
                            "height": 20,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
    }
    with begin_transaction(db):
        attachment = TaskAttachment(
            filename=negative_path.name,
            url=f"/media/{negative_path.name}",
            path=negative_path.name,
            task_id=task.id,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(attachment)
        db.flush()
        negative = TaskSample(
            inner_id=2,
            task_id=task.id,
            file_id=attachment.id,
            created_by=user.id,
            updated_by=user.id,
            annotated_count=1,
            data=json.dumps(negative_data, ensure_ascii=False),
            state=SampleState.DONE.value,
        )
        db.add(negative)
        db.flush()
        annotation_contract.upsert_sample_scope(
            db,
            sample=negative,
            contract=contract,
            annotation_data=negative_data,
            state=SampleState.DONE.value,
            reviewed_by=user.id,
            explicit_negative=False,
        )

    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *_args, **_kwargs: None
    )
    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=CreateLabelDimensionTrainingTaskCommand(
            name="Garbage contract model outputs",
            label_ids=label_ids,
            selected_task_ids=[task.id],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
            model_weights="yolo11n.pt",
        ),
    )

    training_task = crud_training_task.get(db, created.training_task_id)
    job = crud_yolo_train_job.get(db, created.latest_job_id)
    expected_model_classes = business_class_names
    assert json.loads(training_task.selected_label_ids_json) == label_ids
    assert json.loads(training_task.class_names_snapshot_json) == expected_model_classes
    assert [
        row["class_name"]
        for row in json.loads(training_task.merged_class_names_snapshot_json)
    ] == expected_model_classes
    assert json.loads(job.class_names_json) == expected_model_classes

    task_config, samples, counts, source_task_ids = (
        svc.build_label_dimension_dataset_context(
            db,
            current_user=user,
            training_task_id=created.training_task_id,
        )
    )
    assert source_task_ids == [task.id]
    assert counts == {task.id: 2}
    assert {sample.id for sample in samples} == {positive.id, negative.id}
    assert any(
        attr["value"] == non_garbage_label.label_value
        for attr in task_config["tools"][0]["config"]["attributes"]
    )


def test_task_dimension_requires_identical_source_label_sets(db, tmp_path, monkeypatch):
    user, label, task, _sample, _image_path, _annotation_data = (
        _seed_contract_training_case(db, tmp_path, monkeypatch)
    )
    with begin_transaction(db):
        matching_task = crud_task.create(
            db,
            Task(
                name="Matching contract source",
                description="",
                tips="",
                config="{}",
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                created_by=user.id,
                updated_by=user.id,
            ),
        )
        matching_contract = annotation_contract.create_contract(
            db,
            task=matching_task,
            label_ids=[label.id],
            created_by=user.id,
        )
        matching_contract.status = "active"
        extra_label = AnnotationLabel(
            label_key="Vehicle",
            label_value="contract_vehicle",
            normalized_label_value="contract_vehicle",
            annotation_kind="bbox",
            color="#722ed1",
            status="active",
            version=1,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(extra_label)
        db.flush()
        incompatible_task = crud_task.create(
            db,
            Task(
                name="Different contract source",
                description="",
                tips="",
                config="{}",
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.INPROGRESS.value,
                created_by=user.id,
                updated_by=user.id,
            ),
        )
        incompatible_contract = annotation_contract.create_contract(
            db,
            task=incompatible_task,
            label_ids=[label.id, extra_label.id],
            created_by=user.id,
        )
        incompatible_contract.status = "active"

    base_task_id, label_ids = svc._resolve_task_dimension_labels(
        db,
        current_user=user,
        selected_task_ids=[task.id, matching_task.id],
    )
    assert base_task_id == task.id
    assert label_ids == [label.id]

    with pytest.raises(LabelUException, match="different label set"):
        svc._resolve_task_dimension_labels(
            db,
            current_user=user,
            selected_task_ids=[task.id, incompatible_task.id],
        )


def test_contract_preview_accepts_label_superset_and_scoped_negative_only(
    db, tmp_path, monkeypatch
):
    user, label, task, positive, _image_path, _annotation_data = (
        _seed_contract_training_case(
            db,
            tmp_path,
            monkeypatch,
            extra_label_values=("contract_vehicle",),
        )
    )
    contract = annotation_contract.current_contract(db, task.id)
    assert contract is not None

    partial_path = tmp_path / "contract-partial.jpg"
    negative_path = tmp_path / "contract-negative.jpg"
    _write_test_image(partial_path)
    _write_test_image(negative_path)
    empty_annotation = {"result": json.dumps({"rectTool": {"result": []}})}
    negative_annotation = {
        "training_sample_type": "negative",
        "result": json.dumps({"rectTool": {"result": []}}),
    }
    with begin_transaction(db):
        partial_attachment = TaskAttachment(
            filename=partial_path.name,
            url=f"/media/{partial_path.name}",
            path=partial_path.name,
            task_id=task.id,
            created_by=user.id,
            updated_by=user.id,
        )
        negative_attachment = TaskAttachment(
            filename=negative_path.name,
            url=f"/media/{negative_path.name}",
            path=negative_path.name,
            task_id=task.id,
            created_by=user.id,
            updated_by=user.id,
        )
        db.add_all([partial_attachment, negative_attachment])
        db.flush()
        partial = TaskSample(
            inner_id=2,
            task_id=task.id,
            file_id=partial_attachment.id,
            created_by=user.id,
            updated_by=user.id,
            data=json.dumps(empty_annotation),
            state=SampleState.INCOMPLETE.value,
        )
        negative = TaskSample(
            inner_id=3,
            task_id=task.id,
            file_id=negative_attachment.id,
            created_by=user.id,
            updated_by=user.id,
            data=json.dumps(negative_annotation),
            state=SampleState.DONE.value,
        )
        db.add_all([partial, negative])
        db.flush()
        annotation_contract.upsert_sample_scope(
            db,
            sample=partial,
            contract=contract,
            annotation_data=empty_annotation,
            state=SampleState.INCOMPLETE.value,
            reviewed_by=user.id,
            explicit_negative=False,
        )
        annotation_contract.upsert_sample_scope(
            db,
            sample=negative,
            contract=contract,
            annotation_data=negative_annotation,
            state=SampleState.DONE.value,
            reviewed_by=user.id,
            explicit_negative=True,
        )

    preview = svc.preview_label_dimension_training_task(
        db,
        current_user=user,
        cmd=svc.PreviewLabelDimensionTrainingTaskCommand(
            label_ids=[label.id],
            selected_task_ids=[task.id],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
        ),
    )
    assert preview.included_task_count == 1
    assert preview.positive_image_count == 1
    assert preview.negative_image_count == 1
    assert preview.included_image_count == 2
    assert preview.invalid_image_count == 0

    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *_args, **_kwargs: None
    )
    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=CreateLabelDimensionTrainingTaskCommand(
            name="Superset and negative scope dataset",
            label_ids=[label.id],
            selected_task_ids=[task.id],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
            model_weights="yolo11n.pt",
        ),
    )
    _config, samples, counts, _task_ids = svc.build_label_dimension_dataset_context(
        db,
        current_user=user,
        training_task_id=created.training_task_id,
    )
    assert {row.id for row in samples} == {positive.id, negative.id}
    assert counts == {task.id: 2}


def test_frozen_contract_rejects_annotation_and_image_changes(
    db, tmp_path, monkeypatch
):
    user, label, task, sample, image_path, annotation_data = (
        _seed_contract_training_case(db, tmp_path, monkeypatch)
    )
    monkeypatch.setattr(
        svc, "_enqueue_label_dimension_train_job", lambda *_args, **_kwargs: None
    )
    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=CreateLabelDimensionTrainingTaskCommand(
            name="Frozen contract dataset",
            label_ids=[label.id],
            selected_task_ids=[task.id],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
            model_weights="yolo11n.pt",
        ),
    )

    _task_config, samples, _counts, _task_ids = (
        svc.build_label_dimension_dataset_context(
            db,
            current_user=user,
            training_task_id=created.training_task_id,
        )
    )
    assert [row.id for row in samples] == [sample.id]

    changed = dict(annotation_data)
    changed["result"] = json.dumps({"rectTool": {"result": []}})
    with begin_transaction(db):
        sample.data = json.dumps(changed)
        db.add(sample)
    with pytest.raises(Exception, match="冻结样本.*已变化"):
        svc.build_label_dimension_dataset_context(
            db,
            current_user=user,
            training_task_id=created.training_task_id,
        )

    with begin_transaction(db):
        sample.data = json.dumps(annotation_data, ensure_ascii=False)
        db.add(sample)
    image_path.write_bytes(b"changed-image-bytes")
    with pytest.raises(Exception, match="冻结样本.*已变化"):
        svc.build_label_dimension_dataset_context(
            db,
            current_user=user,
            training_task_id=created.training_task_id,
        )


def test_contract_iteration_inherits_frozen_execution_contract(
    db, tmp_path, monkeypatch
):
    user, label, task, _sample, _image_path, _annotation_data = (
        _seed_contract_training_case(db, tmp_path, monkeypatch)
    )
    submitted = []
    monkeypatch.setattr(
        svc,
        "_enqueue_label_dimension_train_job",
        lambda job_id, task_id, cmd: submitted.append((job_id, task_id, cmd)),
    )
    created = svc.create_label_dimension_training_task(
        db,
        current_user=user,
        cmd=CreateLabelDimensionTrainingTaskCommand(
            name="Contract iteration source",
            label_ids=[label.id],
            selected_task_ids=[task.id],
            training_recipe_key="pytest_detection",
            training_recipe_version=1,
            runtime_profile="pytest_detection",
            runtime_interface_version=1,
            model_weights="yolo11n.pt",
        ),
    )
    trained_weights = tmp_path / "contract-iteration-best.pt"
    trained_weights.write_bytes(b"pytest-weights")
    with begin_transaction(db):
        source_job = crud_yolo_train_job.get(db, created.latest_job_id)
        source_job.status = YoloTrainStatus.COMPLETED.value
        source_job.weights_path = str(trained_weights)
        db.add(source_job)
    source_detail = svc.get_training_task_detail(
        db,
        current_user=user,
        training_task_id=created.training_task_id,
    )

    iterated = svc.iterate_training_task(
        db,
        current_user=user,
        training_task_id=created.training_task_id,
    )
    iterated_detail = svc.get_training_task_detail(
        db,
        current_user=user,
        training_task_id=iterated.training_task_id,
    )

    assert iterated_detail.selected_label_ids == source_detail.selected_label_ids
    assert iterated_detail.label_set_signature == source_detail.label_set_signature
    assert iterated_detail.class_order_signature == source_detail.class_order_signature
    assert iterated_detail.dataset_category_id == source_detail.dataset_category_id
    assert iterated_detail.training_recipe_key == source_detail.training_recipe_key
    assert (
        iterated_detail.training_recipe_version == source_detail.training_recipe_version
    )
    assert iterated_detail.runtime_profile == source_detail.runtime_profile
    assert (
        iterated_detail.runtime_interface_version
        == source_detail.runtime_interface_version
    )
    assert len(submitted) == 2
    iteration_cmd = submitted[-1][2]
    assert iteration_cmd.model_weights == f"trained:{created.latest_job_id}"
    assert iteration_cmd.weights_source is None
    assert iteration_cmd.weights_ref_id is None
