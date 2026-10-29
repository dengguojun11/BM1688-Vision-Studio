import json
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from labelu.internal.application.service import plate_recognition_train as service
from labelu.internal.common.plate_recognition_profile import (
    DEFAULT_PLATE_RECOGNITION_CONFIG,
    PlateInstance,
    PlateSampleAudit,
)


def test_plate_detection_runner_persists_only_single_pose_artifact(
    monkeypatch, tmp_path
):
    media_root = tmp_path / "media"
    job = SimpleNamespace(
        id=17,
        training_task_id=9,
        job_type="plate_detection",
        model_weights="yolov8n-pose.pt",
        status="PENDING",
        component_status_json=json.dumps(service.initial_component_status(("pose",))),
        component_artifacts_json=None,
    )
    training_task = SimpleNamespace(id=9, status="PENDING")
    component_updates = []
    job_updates = []

    @contextmanager
    def fake_session_scope():
        yield object()

    def fake_job_update(_db, target, status_value, **values):
        target.status = status_value
        for key, value in values.items():
            setattr(target, key, value)
        job_updates.append({"status": status_value, **values})
        return target

    def fake_train_pose(**kwargs):
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        pt_path = output_dir / "plate_detection_best.pt"
        onnx_path = output_dir / "plate_detection.onnx"
        pt_path.write_bytes(b"pt")
        onnx_path.write_bytes(b"onnx")
        assert (
            kwargs["artifact_contract"] == service.plate_detection_artifact_contract()
        )
        return {
            "pt": str(pt_path),
            "onnx": str(onnx_path),
            "network": "plate_detection",
            "input_size": [640, 640],
            "train_sample_count": 3,
            "base_final_fit_sample_count": 4,
            "fit_strategy": "independent_split_then_all_confirmed",
            "evaluation_metrics": {
                "metrics/mAP50(B)": 0.9,
                "metrics/mAP50-95(B)": 0.7,
            },
        }

    monkeypatch.setattr(service.settings, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(service.settings, "YOLO_TRAIN_WORK_SUBDIR", "yolo_train")
    monkeypatch.setattr(service, "session_scope", fake_session_scope)
    monkeypatch.setattr(service, "begin_transaction", lambda _db: nullcontext())
    monkeypatch.setattr(
        service,
        "_load_training_context",
        lambda _db, **_kwargs: (job, training_task, {}, [21], []),
    )
    monkeypatch.setattr(
        service,
        "prepare_plate_recognition_dataset",
        lambda **_kwargs: (
            media_root / "yolo_train" / "job-17" / "plate_dataset",
            {
                "composition": {
                    "negative_images": 1,
                    "splits": {
                        "train": {"total_images": 3},
                        "val": {"total_images": 1},
                    },
                }
            },
        ),
    )
    monkeypatch.setattr(service, "resolve_plate_train_device", lambda *_args: "cpu")
    monkeypatch.setattr(service, "_train_pose_component", fake_train_pose)
    monkeypatch.setattr(
        service,
        "_update_job_component",
        lambda _db, **values: component_updates.append(values),
    )
    monkeypatch.setattr(service.crud_yolo_train_job, "get", lambda _db, _id: job)
    monkeypatch.setattr(
        service.crud_training_task, "get", lambda _db, _id: training_task
    )
    monkeypatch.setattr(service.crud_yolo_train_job, "update_status", fake_job_update)
    monkeypatch.setattr(
        service.crud_training_task,
        "update",
        lambda _db, target, **values: [
            setattr(target, k, v) for k, v in values.items()
        ],
    )

    service.run_plate_detection_train_job(
        job_id=17,
        task_id=21,
        epochs=2,
        imgsz=640,
        hyperparams={},
        train_device="cpu",
    )

    output_dir = media_root / "yolo_train" / "job-17" / "plate_detection"
    assert (output_dir / "plate_detection_best.pt").is_file()
    assert (output_dir / "plate_detection.onnx").is_file()
    assert not (media_root / "yolo_train" / "job-17" / "plate_components").exists()
    assert [row["component"] for row in component_updates] == ["pose", "pose"]
    completed = job_updates[-1]
    assert completed["status"] == "COMPLETED"
    assert json.loads(completed["component_artifacts_json"]) == {
        "pose": {
            "pt": str(output_dir / "plate_detection_best.pt"),
            "onnx": str(output_dir / "plate_detection.onnx"),
            "network": "plate_detection",
            "input_size": [640, 640],
            "train_sample_count": 3,
            "base_final_fit_sample_count": 4,
            "fit_strategy": "independent_split_then_all_confirmed",
            "evaluation_metrics": {
                "metrics/mAP50(B)": 0.9,
                "metrics/mAP50-95(B)": 0.7,
            },
        }
    }


def test_plate_component_write_rejects_stale_execution_fence(monkeypatch):
    from labelu.internal.application.service.yolo_train import TrainExecutionFenceLost

    job = SimpleNamespace(
        id=17,
        job_type="plate_detection",
        status="PROCESSING",
        component_status_json=json.dumps(service.initial_component_status(("pose",))),
        component_artifacts_json="{}",
    )
    updates = []
    monkeypatch.setattr(service.crud_yolo_train_job, "get", lambda _db, _id: job)
    monkeypatch.setattr(service, "begin_transaction", lambda _db: nullcontext())
    monkeypatch.setattr(
        service,
        "_assert_plate_train_execution_fence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TrainExecutionFenceLost("stale")
        ),
    )
    monkeypatch.setattr(
        service.crud_yolo_train_job,
        "update_status",
        lambda *_args, **_kwargs: updates.append(True),
    )

    with pytest.raises(TrainExecutionFenceLost):
        service._update_job_component(
            object(),
            job_id=17,
            component="pose",
            status_value="COMPLETED",
            artifact={"pt": "/tmp/old.pt", "onnx": "/tmp/old.onnx"},
            execution_fencing_token=2,
            execution_owner="old-worker",
        )

    assert updates == []


def test_plate_artifact_recovery_requires_current_execution_generation(tmp_path):
    output_dir = tmp_path / "plate_components"
    output_dir.mkdir()
    pt_path = output_dir / "plate_pose_best.pt"
    onnx_path = output_dir / "plate_pose.onnx"
    pt_path.write_bytes(b"pt")
    onnx_path.write_bytes(b"onnx")
    artifact = {
        "pt": str(pt_path.resolve()),
        "onnx": str(onnx_path.resolve()),
        "network": "plate_pose",
    }

    service._write_plate_artifact_generation_marker(
        output_dir=output_dir,
        component="pose",
        artifact=artifact,
        execution_owner="worker-a",
        execution_fencing_token=7,
    )

    assert service._plate_artifact_marker_matches(
        output_dir=output_dir,
        component="pose",
        artifact=artifact,
        execution_owner="worker-a",
        execution_fencing_token=7,
    )
    assert not service._plate_artifact_marker_matches(
        output_dir=output_dir,
        component="pose",
        artifact=artifact,
        execution_owner="worker-a",
        execution_fencing_token=8,
    )
    assert not service._plate_artifact_marker_matches(
        output_dir=output_dir,
        component="pose",
        artifact=artifact,
        execution_owner="worker-b",
        execution_fencing_token=7,
    )
    (output_dir / ".pose.artifact-generation.json").write_text(
        json.dumps({"execution_fencing_token": "not-a-number"}), encoding="utf-8"
    )
    assert not service._plate_artifact_marker_matches(
        output_dir=output_dir,
        component="pose",
        artifact=artifact,
        execution_owner="worker-a",
        execution_fencing_token=7,
    )


def test_plate_artifact_generation_decoration_keeps_legacy_path_unchanged():
    artifact = {"pt": "/tmp/a.pt", "onnx": "/tmp/a.onnx"}
    assert service._decorate_plate_artifact_generation(
        artifact,
        execution_owner=None,
        execution_fencing_token=None,
    ) is artifact
    fenced = service._decorate_plate_artifact_generation(
        artifact,
        execution_owner="worker-a",
        execution_fencing_token=3,
    )
    assert fenced["phase_generation"] == "worker-a:3"
    assert fenced["execution_owner"] == "worker-a"
    assert fenced["execution_fencing_token"] == 3


def test_unfenced_plate_component_write_rejects_active_worker_lease():
    from labelu.internal.application.service.yolo_train import TrainExecutionFenceLost

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return SimpleNamespace(
                execution_lease_owner="worker-a",
                execution_lease_expires_at=datetime.now() + timedelta(minutes=5),
            )

    class Db:
        def query(self, *_args):
            return Query()

    with pytest.raises(TrainExecutionFenceLost):
        service._assert_plate_train_execution_fence(
            Db(),
            job_id=17,
            execution_owner=None,
            execution_fencing_token=None,
        )


def test_plate_execution_write_guard_rechecks_generation_before_disk_write(monkeypatch):
    calls = []

    class DbContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    def assert_fence(db, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(service, "session_scope", lambda: DbContext())
    monkeypatch.setattr(service, "_assert_plate_train_execution_fence", assert_fence)
    guard = service._plate_execution_write_guard(
        17, execution_owner="worker-a", execution_fencing_token=4
    )
    guard()
    assert calls == [
        {
            "job_id": 17,
            "execution_owner": "worker-a",
            "execution_fencing_token": 4,
        }
    ]


def test_unfenced_plate_lease_inspection_failure_fails_closed():
    from labelu.internal.application.service.yolo_train import TrainExecutionFenceLost

    class Db:
        def query(self, *_args):
            raise RuntimeError("database unavailable")

    # Treat the test double as a real SQLAlchemy session for this contract.
    original_session = service.Session
    service.Session = Db
    try:
        with pytest.raises(TrainExecutionFenceLost, match="无法确认执行租约"):
            service._assert_plate_train_execution_fence(
                Db(),
                job_id=17,
                execution_owner=None,
                execution_fencing_token=None,
            )
    finally:
        service.Session = original_session


def test_resolve_plate_train_device_uses_job_device_resolution(monkeypatch):
    from labelu.internal.application.service import yolo_train

    seen = {}
    monkeypatch.setattr(
        yolo_train,
        "_resolve_train_device_for_job",
        lambda job_id, override=None: seen.update(
            {"job_id": job_id, "override": override}
        )
        or "0",
    )

    assert service.resolve_plate_train_device(17, None) == "0"
    assert seen == {"job_id": 17, "override": None}


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def all(self):
        return self.rows


def _sample(
    sample_id: int,
    task_id: int,
    relative_path: str,
    *,
    plate_number: str | None = None,
) -> service.PlateDatasetSample:
    attachment = SimpleNamespace(path=relative_path, filename=Path(relative_path).name)
    sample = SimpleNamespace(id=sample_id, task_id=task_id, file=attachment)
    if plate_number is None:
        audit = PlateSampleAudit(kind="negative")
    else:
        audit = PlateSampleAudit(
            kind="positive",
            instances=(
                PlateInstance(
                    rect={
                        "x": 16,
                        "y": 12,
                        "width": 80,
                        "height": 40,
                    },
                    corners=((18, 14), (94, 14), (94, 50), (18, 50)),
                    plate_number=plate_number,
                    color="green",
                    plate_type="greenPlate",
                ),
            ),
        )
    return service.PlateDatasetSample(sample=sample, audit=audit)


def test_grouped_split_keeps_same_plate_identity_together():
    samples = [
        _sample(1, 124, "source/a.jpg", plate_number="陕A12345"),
        _sample(2, 125, "source/b.jpg", plate_number=" 陕A12345 "),
        _sample(3, 126, "source/c.jpg", plate_number="陕B54321"),
        _sample(4, 127, "source/negative.jpg"),
    ]

    splits = service._grouped_splits(samples, seed=42)
    sample_to_split = {
        item.sample.id: split for split, rows in splits.items() for item in rows
    }

    assert sample_to_split[1] == sample_to_split[2]
    assert sorted(sample_to_split) == [1, 2, 3, 4]


def test_frozen_split_rejects_same_plate_identity_across_splits():
    first = _sample(1, 124, "source/a.jpg", plate_number="陕A12345")
    second = _sample(2, 125, "source/b.jpg", plate_number=" 陕A12345 ")
    third = _sample(3, 126, "source/c.jpg", plate_number="陕B54321")
    first.sample.frozen_split = "train"
    second.sample.frozen_split = "val"
    third.sample.frozen_split = "train"

    with pytest.raises(RuntimeError, match="plate identity.*frozen splits"):
        service._grouped_splits([first, second, third], seed=42)


def test_grouped_split_keeps_transitively_shared_plate_identities_together():
    first = _sample(1, 124, "source/a.jpg", plate_number="陕A11111")
    bridge_base = _sample(2, 125, "source/b.jpg", plate_number="陕A11111")
    second_instance = PlateInstance(
        rect={"x": 20, "y": 20, "width": 60, "height": 30},
        corners=((20, 20), (80, 20), (80, 50), (20, 50)),
        plate_number="陕B22222",
        color="green",
        plate_type="greenPlate",
    )
    bridge = service.PlateDatasetSample(
        sample=bridge_base.sample,
        audit=PlateSampleAudit(
            kind="positive",
            instances=bridge_base.audit.instances + (second_instance,),
        ),
    )
    last = _sample(3, 126, "source/c.jpg", plate_number="陕B22222")
    negatives = [_sample(index, 127, f"source/n{index}.jpg") for index in range(4, 10)]

    splits = service._grouped_splits([first, bridge, last, *negatives], seed=42)
    sample_to_split = {
        item.sample.id: split for split, rows in splits.items() for item in rows
    }

    assert sample_to_split[1] == sample_to_split[2] == sample_to_split[3]


def test_grouped_split_stratifies_positive_and_negative_groups():
    positives = [
        _sample(
            index,
            124,
            f"source/p{index}.jpg",
            plate_number=f"陕A{index:05d}",
        )
        for index in range(1, 11)
    ]
    negatives = [_sample(index, 127, f"source/n{index}.jpg") for index in range(11, 21)]

    splits = service._grouped_splits([*positives, *negatives], seed=42)

    for split in ("train", "val", "test"):
        assert any(item.audit.kind == "positive" for item in splits[split])
        assert any(item.audit.kind == "negative" for item in splits[split])


def test_grouped_split_keeps_all_component_labels_represented_in_train():
    samples = [
        _sample(
            index,
            124,
            f"source/p{index}.jpg",
            plate_number=f"A{index % 5}P{index % 3}",
        )
        for index in range(1, 31)
    ]
    for sample_index in (3, 13, 23):
        rare_base = samples[sample_index]
        rare_instance = rare_base.audit.instances[0]
        samples[sample_index] = service.PlateDatasetSample(
            sample=rare_base.sample,
            audit=PlateSampleAudit(
                kind="positive",
                instances=(
                    PlateInstance(
                        rect=rare_instance.rect,
                        corners=rare_instance.corners,
                        plate_number=rare_instance.plate_number,
                        color="blue",
                        plate_type="bluePlate",
                    ),
                ),
            ),
        )

    splits = service._grouped_splits(samples, seed=42)
    all_instances = [instance for item in samples for instance in item.audit.instances]
    train_instances = [
        instance for item in splits["train"] for instance in item.audit.instances
    ]

    assert {
        character for instance in all_instances for character in instance.plate_number
    } <= {
        character for instance in train_instances for character in instance.plate_number
    }
    assert {instance.color for instance in all_instances} <= {
        instance.color for instance in train_instances
    }
    assert {instance.plate_type for instance in all_instances} <= {
        instance.plate_type for instance in train_instances
    }


def test_prepare_dataset_writes_pose_labels_crops_and_negative_empty_label(
    tmp_path, monkeypatch
):
    media_root = tmp_path / "media"
    source_dir = media_root / "source"
    source_dir.mkdir(parents=True)
    image = np.full((72, 128, 3), 180, dtype=np.uint8)
    assert cv2.imwrite(str(source_dir / "positive.jpg"), image)
    assert cv2.imwrite(str(source_dir / "negative.jpg"), image)
    work_root = tmp_path / "job-7"
    monkeypatch.setattr(service.settings, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(service, "_job_work_root", lambda _job_id: work_root)
    samples = [
        _sample(1, 124, "source/positive.jpg", plate_number="陕A12345"),
        _sample(2, 124, "source/negative.jpg"),
    ]

    dataset_root, manifest = service.prepare_plate_recognition_dataset(
        job_id=7,
        profile_config=DEFAULT_PLATE_RECOGNITION_CONFIG,
        source_task_ids=[124],
        samples=samples,
        seed=42,
    )

    assert manifest["composition"]["positive_images"] == 1
    assert manifest["composition"]["negative_images"] == 1
    assert manifest["composition"]["instances"] == 1
    assert manifest["component_sample_count"] == 1
    rows = [
        json.loads(line)
        for line in (dataset_root / "component_samples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[0]["plateNumber"] == "陕A12345"
    assert rows[0]["ocr_layout"] == "lower_row"
    assert (dataset_root / rows[0]["ocr_path"]).is_file()
    assert (dataset_root / rows[0]["classification_path"]).is_file()

    negative_row = next(row for row in manifest["samples"] if row["kind"] == "negative")
    negative_label = dataset_root / negative_row["pose_label"]
    assert negative_label.is_file()
    assert negative_label.read_text(encoding="utf-8") == ""

    positive_row = next(row for row in manifest["samples"] if row["kind"] == "positive")
    pose_values = (
        (dataset_root / positive_row["pose_label"]).read_text(encoding="utf-8").split()
    )
    assert len(pose_values) == 17
    assert pose_values[0] == "0"


def test_final_fit_rows_preserve_original_split_and_use_all_rows():
    rows = [
        {"split": "train", "plateNumber": "A1"},
        {"split": "val", "plateNumber": "B2"},
        {"split": "test", "plateNumber": "C3"},
    ]

    final_rows = service._final_fit_rows(rows)

    assert [row["split"] for row in final_rows] == ["train", "train", "train"]
    assert [row["original_split"] for row in final_rows] == [
        "train",
        "val",
        "test",
    ]


def test_pose_final_fit_yaml_includes_positive_and_empty_label_images(tmp_path):
    pose_root = tmp_path / "pose"
    for split, name, label in (
        ("train", "positive", "0 0.5 0.5 0.2 0.2\n"),
        ("val", "negative", ""),
    ):
        image_path = pose_root / "images" / split / f"{name}.jpg"
        label_path = pose_root / "labels" / split / f"{name}.txt"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.write_bytes(b"image")
        label_path.write_text(label, encoding="utf-8")

    yaml_path, sample_count = service._write_pose_final_fit_yaml(tmp_path)

    all_images = (pose_root / "all_images.txt").read_text(encoding="utf-8")
    assert sample_count == 2
    assert "positive.jpg" in all_images
    assert "negative.jpg" in all_images
    assert "all_images.txt" in yaml_path.read_text(encoding="utf-8")


def test_pose_training_generates_evaluation_plots_only(tmp_path, monkeypatch):
    import ultralytics
    from ultralytics import utils as ultralytics_utils

    dataset_root = tmp_path / "dataset"
    for split in ("train", "val", "test"):
        image_path = dataset_root / "pose" / "images" / split / f"{split}.jpg"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"image")

    user_config_dir = tmp_path / "ultralytics-config"
    user_config_dir.mkdir()
    (user_config_dir / "Arial.ttf").write_bytes(b"font")
    monkeypatch.setattr(ultralytics_utils, "USER_CONFIG_DIR", user_config_dir)
    monkeypatch.setattr(
        service,
        "resolve_plate_pose_pretrained_model",
        lambda _weights: str(tmp_path / "pretrained.pt"),
    )

    train_calls = []

    class FakeResult:
        results_dict = {"metrics/mAP50(B)": 0.8}

    class FakeYolo:
        def __init__(self, source):
            self.source = Path(source)

        def train(self, **kwargs):
            train_calls.append(kwargs)
            run_dir = Path(kwargs["project"]) / kwargs["name"]
            weights_dir = run_dir / "weights"
            weights_dir.mkdir(parents=True)
            (weights_dir / "best.pt").write_bytes(b"weights")
            (run_dir / "results.csv").write_text(
                "epoch,metrics/mAP50(B)\n1,0.8\n", encoding="utf-8"
            )
            if kwargs["plots"]:
                (run_dir / "results.png").write_bytes(b"plot")
            return FakeResult()

        def export(self, **_kwargs):
            exported = self.source.with_suffix(".onnx")
            exported.write_bytes(b"onnx")
            return str(exported)

    monkeypatch.setattr(ultralytics, "YOLO", FakeYolo)
    output_dir = tmp_path / "plate_components"

    artifact = service._train_pose_component(
        dataset_root=dataset_root,
        output_dir=output_dir,
        epochs=1,
        imgsz=640,
        device="cpu",
        hyperparams={"workers": 8, "amp": True},
    )

    assert train_calls[0]["plots"] is True
    assert train_calls[1]["plots"] is False
    assert train_calls[0]["workers"] == 8
    assert train_calls[0]["amp"] is True
    assert (output_dir / "pose_train" / "train" / "results.png").is_file()
    assert not (output_dir / "pose_final_fit" / "train" / "results.png").exists()
    assert Path(artifact["pt"]).is_file()
    assert Path(artifact["onnx"]).is_file()


def test_pose_resume_continues_existing_final_fit_checkpoint(tmp_path, monkeypatch):
    import ultralytics
    from ultralytics import utils as ultralytics_utils

    dataset_root = tmp_path / "dataset"
    for split in ("train", "val", "test"):
        image_path = dataset_root / "pose" / "images" / split / f"{split}.jpg"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"image")

    user_config_dir = tmp_path / "ultralytics-config"
    user_config_dir.mkdir()
    (user_config_dir / "Arial.ttf").write_bytes(b"font")
    monkeypatch.setattr(ultralytics_utils, "USER_CONFIG_DIR", user_config_dir)
    monkeypatch.setattr(
        service,
        "resolve_plate_pose_pretrained_model",
        lambda _weights: str(tmp_path / "pretrained.pt"),
    )

    output_dir = tmp_path / "plate_components"
    evaluation_run = output_dir / "pose_train" / "train"
    evaluation_weights = evaluation_run / "weights" / "best.pt"
    evaluation_weights.parent.mkdir(parents=True)
    evaluation_weights.write_bytes(b"evaluation")
    (evaluation_run / "results.csv").write_text(
        "epoch,metrics/mAP50(B)\n1,0.7\n2,0.8\n3,0.9\n",
        encoding="utf-8",
    )
    final_run = output_dir / "pose_final_fit" / "train"
    final_last = final_run / "weights" / "last.pt"
    final_last.parent.mkdir(parents=True)
    final_last.write_bytes(b"partial")
    (final_run / "results.csv").write_text(
        "epoch,metrics/mAP50(B)\n1,0.6\n",
        encoding="utf-8",
    )

    train_calls = []

    class FakeResult:
        results_dict = {"metrics/mAP50(B)": 0.85}

    class FakeYolo:
        def __init__(self, source):
            self.source = Path(source)

        def train(self, **kwargs):
            train_calls.append((self.source, kwargs))
            (final_run / "weights" / "best.pt").write_bytes(b"resumed")
            return FakeResult()

        def export(self, **_kwargs):
            exported = self.source.with_suffix(".onnx")
            exported.write_bytes(b"onnx")
            return str(exported)

    monkeypatch.setattr(ultralytics, "YOLO", FakeYolo)

    artifact = service._train_pose_component(
        dataset_root=dataset_root,
        output_dir=output_dir,
        epochs=3,
        imgsz=640,
        device="cpu",
        hyperparams={"workers": 8, "amp": True},
        resume=True,
    )

    assert len(train_calls) == 1
    source, kwargs = train_calls[0]
    assert source == final_last
    assert kwargs == {"resume": str(final_last), "device": "cpu"}
    assert artifact["evaluation_metrics"]["metrics/mAP50(B)"] == 0.9
    assert artifact["final_fit_metrics"]["metrics/mAP50(B)"] == 0.85
    assert Path(artifact["pt"]).is_file()
    assert Path(artifact["onnx"]).is_file()


def test_augment_ocr_rows_from_pose_writes_predicted_crop(tmp_path, monkeypatch):
    import torch
    import ultralytics

    dataset_root = tmp_path / "dataset"
    image_path = dataset_root / "pose" / "images" / "train" / "sample.jpg"
    label_path = dataset_root / "pose" / "labels" / "train" / "sample.txt"
    image_path.parent.mkdir(parents=True)
    label_path.parent.mkdir(parents=True)
    image = np.full((100, 100, 3), 180, dtype=np.uint8)
    assert cv2.imwrite(str(image_path), image)
    label_path.write_text(
        "0 0.5 0.5 0.4 0.4 0.3 0.3 2 0.7 0.3 2 " "0.7 0.7 2 0.3 0.7 2\n",
        encoding="utf-8",
    )
    (dataset_root / "dataset_manifest.json").write_text(
        json.dumps(
            {
                "samples": [
                    {
                        "sample_id": 1,
                        "pose_image": "pose/images/train/sample.jpg",
                        "pose_label": "pose/labels/train/sample.txt",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    pose_pt = tmp_path / "pose.pt"
    pose_pt.write_bytes(b"weights")

    prediction = SimpleNamespace(
        boxes=SimpleNamespace(
            xyxy=torch.tensor([[30.0, 30.0, 70.0, 70.0]], dtype=torch.float32)
        ),
        keypoints=SimpleNamespace(
            xy=torch.tensor(
                [[[30.0, 30.0], [70.0, 30.0], [70.0, 70.0], [30.0, 70.0]]],
                dtype=torch.float32,
            )
        ),
    )

    class FakePoseModel:
        def predict(self, **_kwargs):
            return [prediction]

    monkeypatch.setattr(ultralytics, "YOLO", lambda _path: FakePoseModel())
    rows = [
        {
            "split": "train",
            "sample_id": 1,
            "task_id": 124,
            "ocr_path": "ocr/train/t124_s1_i0.png",
            "ocr_layout": "lower_row",
            "plateNumber": "A12345",
        }
    ]

    augmented, report = service._augment_ocr_rows_from_pose(
        dataset_root=dataset_root,
        rows=rows,
        pose_pt=pose_pt,
        imgsz=640,
        device="cpu",
    )

    assert report["augmented_row_count"] == 1
    assert report["unmatched_count"] == 0
    assert augmented[0]["source_variant"] == "pose_predicted"
    assert augmented[0]["ocr_layout"] == "lower_row"
    assert (dataset_root / augmented[0]["ocr_path"]).is_file()
    assert (dataset_root / augmented[0]["classification_path"]).is_file()


def test_component_contract_has_four_distinct_networks_and_artifacts():
    contract = service.component_artifact_contract()

    assert list(contract) == ["pose", "ocr", "color", "plate_type"]
    assert len({item["network"] for item in contract.values()}) == 4
    assert len({item["pt"] for item in contract.values()}) == 4
    assert len({item["onnx"] for item in contract.values()}) == 4


def test_component_status_only_persists_compact_artifact_index(monkeypatch):
    large_artifact = {
        "pt": "/data/job-257/plate_pose_best.pt",
        "onnx": "/data/job-257/plate_pose_best.onnx",
        "pose_augmentation_report": {
            "unmatched": [{"reason": "x" * 1000}] * 100,
        },
    }
    job = SimpleNamespace(
        status="PROCESSING",
        component_status_json=json.dumps(service.initial_component_status()),
        component_artifacts_json="{}",
    )
    captured = {}

    monkeypatch.setattr(service.crud_yolo_train_job, "get", lambda _db, _id: job)
    monkeypatch.setattr(service, "begin_transaction", lambda _db: nullcontext())

    def capture_update(_db, _job, _status, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        service.crud_yolo_train_job,
        "update_status",
        capture_update,
    )

    service._update_job_component(
        object(),
        job_id=257,
        component="pose",
        status_value="COMPLETED",
        artifact=large_artifact,
    )

    statuses = json.loads(captured["component_status_json"])
    artifacts = json.loads(captured["component_artifacts_json"])
    assert statuses["pose"]["artifact"] == {
        "pt": large_artifact["pt"],
        "onnx": large_artifact["onnx"],
    }
    assert len(captured["component_status_json"].encode("utf-8")) < 65535
    assert artifacts["pose"] == large_artifact


def test_component_iteration_checkpoint_remaps_output_labels(tmp_path):
    import torch

    from labelu.internal.common.plate_recognition_models import (
        build_plate_classifier,
        save_component_checkpoint,
    )

    source_model = build_plate_classifier(2)
    with torch.no_grad():
        source_model.features[0].weight.fill_(0.25)
        source_model.classifier.weight[0].fill_(1.0)
        source_model.classifier.weight[1].fill_(2.0)
        source_model.classifier.bias[0] = 3.0
        source_model.classifier.bias[1] = 4.0
    checkpoint_path = tmp_path / "plate_color_best.pt"
    save_component_checkpoint(
        checkpoint_path,
        model=source_model,
        component="color",
        labels=["green", "blue"],
        input_size=(128, 64),
    )

    target_model = build_plate_classifier(3)
    metadata = service._load_component_iteration_checkpoint(
        target_model,
        component="color",
        labels=["blue", "yellow", "green"],
        checkpoint_path=checkpoint_path,
    )

    assert metadata["copied_labels"] == ["blue", "green"]
    assert torch.allclose(
        target_model.features[0].weight,
        torch.full_like(target_model.features[0].weight, 0.25),
    )
    assert torch.allclose(
        target_model.classifier.weight[0],
        torch.full_like(target_model.classifier.weight[0], 2.0),
    )
    assert torch.allclose(
        target_model.classifier.weight[2],
        torch.full_like(target_model.classifier.weight[2], 1.0),
    )
    assert float(target_model.classifier.bias[0]) == 4.0
    assert float(target_model.classifier.bias[2]) == 3.0


def test_resume_only_reuses_completed_components_with_existing_artifacts(tmp_path):
    pose_pt = tmp_path / "pose.pt"
    pose_onnx = tmp_path / "pose.onnx"
    pose_pt.write_bytes(b"pt")
    pose_onnx.write_bytes(b"onnx")
    missing_onnx = tmp_path / "ocr.onnx"
    job = SimpleNamespace(
        component_status_json=json.dumps(
            {
                "pose": {"status": "COMPLETED"},
                "ocr": {"status": "COMPLETED"},
                "color": {"status": "PROCESSING"},
            }
        ),
        component_artifacts_json=json.dumps(
            {
                "pose": {"pt": str(pose_pt), "onnx": str(pose_onnx)},
                "ocr": {"pt": str(pose_pt), "onnx": str(missing_onnx)},
            }
        ),
    )

    completed = service._completed_component_artifacts(job)

    assert list(completed) == ["pose"]


def test_resume_recovers_only_complete_component_artifact_pairs(tmp_path, monkeypatch):
    job_root = tmp_path / "job-257"
    output_dir = job_root / "plate_components"
    output_dir.mkdir(parents=True)
    contract = service.component_artifact_contract()
    (output_dir / contract["pose"]["pt"]).write_bytes(b"pt")
    (output_dir / contract["pose"]["onnx"]).write_bytes(b"onnx")
    (output_dir / contract["ocr"]["pt"]).write_bytes(b"partial")
    monkeypatch.setattr(service, "_job_work_root", lambda _job_id: job_root)

    recovered = service._recover_component_artifacts_from_disk(257)

    assert list(recovered) == ["pose"]
    assert recovered["pose"] == {
        "pt": str((output_dir / contract["pose"]["pt"]).resolve()),
        "onnx": str((output_dir / contract["pose"]["onnx"]).resolve()),
        "network": contract["pose"]["network"],
        "recovered_from_disk": True,
    }


def test_regenerate_plate_component_analysis_uses_existing_checkpoints(
    tmp_path, monkeypatch
):
    import torch

    from labelu.internal.common.plate_recognition_models import (
        build_plate_classifier,
        build_plate_ocr_model,
        save_component_checkpoint,
    )

    job_root = tmp_path / "job-253"
    dataset_root = job_root / "plate_dataset"
    ocr_path = dataset_root / "ocr" / "val" / "plate.png"
    classification_path = dataset_root / "classification" / "val" / "plate.png"
    ocr_path.parent.mkdir(parents=True)
    classification_path.parent.mkdir(parents=True)
    image = np.full((64, 192, 3), 160, dtype=np.uint8)
    assert cv2.imwrite(str(ocr_path), image)
    assert cv2.imwrite(str(classification_path), image)
    row = {
        "split": "val",
        "ocr_path": "ocr/val/plate.png",
        "classification_path": "classification/val/plate.png",
        "ocr_layout": "lower_row",
        "plateNumber": "A12",
        "color": "green",
        "plateType": "greenPlate",
    }
    (dataset_root / "component_samples.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    pose_data = dataset_root / "pose" / "data.yaml"
    pose_data.parent.mkdir(parents=True)
    pose_data.write_text("path: .\nval: images/val\n", encoding="utf-8")
    pose_checkpoint = (
        job_root / "plate_components" / "pose_train" / "train" / "weights" / "best.pt"
    )
    pose_checkpoint.parent.mkdir(parents=True)
    pose_checkpoint.write_bytes(b"pose")
    artifacts = {}
    for component, labels, model in (
        ("ocr", ["<blank>", "A", "1", "2"], build_plate_ocr_model(4)),
        ("color", ["green", "blue"], build_plate_classifier(2)),
        (
            "plate_type",
            ["greenPlate", "bluePlate"],
            build_plate_classifier(2),
        ),
    ):
        checkpoint = (
            job_root
            / "plate_components"
            / "evaluation"
            / component
            / service.component_artifact_contract()[component]["pt"]
        )
        checkpoint.parent.mkdir(parents=True)
        save_component_checkpoint(
            checkpoint,
            model=model,
            component=component,
            labels=labels,
            input_size=(192, 48) if component == "ocr" else (128, 64),
        )
        artifacts[component] = {
            "evaluation_stage": {
                "pt": str(checkpoint),
                "final_loss": 0.25,
            }
        }

    class FakePoseModel:
        def __init__(self, checkpoint):
            assert Path(checkpoint) == pose_checkpoint

        def val(self, **kwargs):
            output = Path(kwargs["project"]) / kwargs["name"]
            output.mkdir(parents=True)
            (output / "BoxPR_curve.png").write_bytes(b"curve")
            (output / "val_batch0_pred.jpg").write_bytes(b"preview")
            return SimpleNamespace(save_dir=output)

    import ultralytics

    monkeypatch.setattr(ultralytics, "YOLO", FakePoseModel)
    monkeypatch.setattr(service, "_job_work_root", lambda _job_id: job_root)

    rebuilt = service.regenerate_plate_component_analysis(
        253,
        component_artifacts=artifacts,
        epochs=20,
    )

    assert list(rebuilt) == ["pose", "ocr", "color", "plate_type"]
    assert rebuilt["pose"]["checkpoint"] == str(pose_checkpoint.resolve())
    assert len(rebuilt["pose"]["generated_files"]) == 2
    assert (
        job_root / "plate_components" / "pose_train" / "train" / "BoxPR_curve.png"
    ).is_file()
    for component in ("ocr", "color", "plate_type"):
        analysis_dir = Path(rebuilt[component]["analysis"]["directory"])
        assert (analysis_dir / "results.csv").is_file()
        assert (analysis_dir / "confusion_matrix.png").is_file()
        assert (analysis_dir / "validation_predictions.png").is_file()
        assert rebuilt[component]["legacy_loss_history"] is True


def test_resolve_plate_pose_pretrained_model_prefers_explicit_path(
    tmp_path, monkeypatch
):
    explicit = tmp_path / "pose.pt"
    explicit.write_bytes(b"weights")
    monkeypatch.setenv("LABELU_PLATE_POSE_PRETRAINED_MODEL", str(explicit))

    assert service.resolve_plate_pose_pretrained_model() == str(explicit.resolve())


def test_resolve_plate_pose_pretrained_model_uses_media_pretrained(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("LABELU_PLATE_POSE_PRETRAINED_MODEL", raising=False)
    media_root = tmp_path / "media"
    expected = media_root / "pretrained" / service.PLATE_POSE_PRETRAINED_MODEL
    expected.parent.mkdir(parents=True)
    expected.write_bytes(b"weights")
    monkeypatch.setattr(service.settings, "YOLO_PRETRAINED_WEIGHTS_DIR", "")
    monkeypatch.setattr(service.settings, "MEDIA_ROOT", str(media_root))

    assert service.resolve_plate_pose_pretrained_model() == str(expected.resolve())


def test_resolve_plate_pose_pretrained_model_fails_without_local_file(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("LABELU_PLATE_POSE_PRETRAINED_MODEL", raising=False)
    monkeypatch.setattr(
        service.settings, "YOLO_PRETRAINED_WEIGHTS_DIR", str(tmp_path / "configured")
    )
    monkeypatch.setattr(service.settings, "MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setattr(service.Path, "home", lambda: tmp_path / "home")

    try:
        service.resolve_plate_pose_pretrained_model()
    except RuntimeError as exc:
        assert service.PLATE_POSE_PRETRAINED_MODEL in str(exc)
        assert str(tmp_path / "media" / "pretrained") in str(exc)
    else:
        raise AssertionError("expected missing local weights to fail before download")


def test_plate_audit_only_includes_explicit_empty_negative(monkeypatch):
    unconfirmed = SimpleNamespace(
        id=1,
        inner_id=1,
        data={"result": "{}"},
        file=SimpleNamespace(filename="unconfirmed.jpg"),
    )
    explicit = SimpleNamespace(
        id=2,
        inner_id=2,
        data={"training_sample_type": "negative", "result": "{}"},
        file=SimpleNamespace(filename="negative.jpg"),
    )
    db = SimpleNamespace(query=lambda _model: _Query([unconfirmed, explicit]))
    monkeypatch.setattr(
        service,
        "_parse_sample_annotation",
        lambda sample: (sample.data, {}),
    )

    audit, samples = service.audit_task_done_samples(
        db,
        task_id=127,
        profile_config=DEFAULT_PLATE_RECOGNITION_CONFIG,
        include_samples=True,
    )

    assert audit.positive_image_count == 0
    assert audit.negative_image_count == 1
    assert audit.invalid_image_count == 1
    assert audit.invalid_samples[0]["sample_id"] == 1
    assert "not an explicit negative" in audit.invalid_samples[0]["errors"][0]
    assert [item.sample.id for item in samples] == [2]


def test_partial_instance_is_kept_for_pose_but_not_attribute_components(
    tmp_path, monkeypatch
):
    media_root = tmp_path / "media"
    source_dir = media_root / "source"
    source_dir.mkdir(parents=True)
    image = np.full((72, 128, 3), 180, dtype=np.uint8)
    assert cv2.imwrite(str(source_dir / "partial.jpg"), image)
    base = _sample(1, 124, "source/partial.jpg", plate_number="unused")
    partial = service.PlateDatasetSample(
        sample=base.sample,
        audit=PlateSampleAudit(
            kind="positive",
            instances=(
                PlateInstance(
                    rect=base.audit.instances[0].rect,
                    corners=base.audit.instances[0].corners,
                    plate_number="",
                    color="",
                    plate_type="",
                ),
            ),
            errors=("plate rectangle missing attributes",),
        ),
    )
    work_root = tmp_path / "job-8"
    monkeypatch.setattr(service.settings, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(service, "_job_work_root", lambda _job_id: work_root)

    dataset_root, manifest = service.prepare_plate_recognition_dataset(
        job_id=8,
        profile_config=DEFAULT_PLATE_RECOGNITION_CONFIG,
        source_task_ids=[124],
        samples=[partial],
        seed=42,
    )

    sample_row = manifest["samples"][0]
    assert (dataset_root / sample_row["pose_label"]).read_text(encoding="utf-8").strip()
    assert manifest["component_sample_counts"] == {
        "pose": 1,
        "ocr": 0,
        "color": 0,
        "plate_type": 0,
    }


def test_component_trainers_write_loadable_pt_and_onnx(tmp_path):
    import onnx
    import torch

    dataset_root = tmp_path / "dataset"
    ocr_path = dataset_root / "ocr" / "train" / "plate.png"
    classification_path = dataset_root / "classification" / "train" / "plate.png"
    ocr_path.parent.mkdir(parents=True)
    classification_path.parent.mkdir(parents=True)
    image = np.full((64, 192, 3), 160, dtype=np.uint8)
    assert cv2.imwrite(str(ocr_path), image)
    assert cv2.imwrite(str(classification_path), image)
    rows = [
        {
            "split": "train",
            "ocr_path": "ocr/train/plate.png",
            "classification_path": "classification/train/plate.png",
            "plateNumber": "A12",
            "color": "green",
            "plateType": "greenPlate",
        }
    ]
    output_dir = tmp_path / "artifacts"
    device = torch.device("cpu")

    artifacts = {
        "ocr": service._train_ocr_component(
            dataset_root=dataset_root,
            rows=rows,
            output_dir=output_dir,
            epochs=1,
            device=device,
            real_finetune_epochs=1,
        ),
        "color": service._train_classifier_component(
            component="color",
            dataset_root=dataset_root,
            rows=rows,
            class_names=["green", "blue"],
            output_dir=output_dir,
            epochs=1,
            device=device,
        ),
        "plate_type": service._train_classifier_component(
            component="plate_type",
            dataset_root=dataset_root,
            rows=rows,
            class_names=["greenPlate", "bluePlate"],
            output_dir=output_dir,
            epochs=1,
            device=device,
        ),
    }

    for component, artifact in artifacts.items():
        checkpoint = torch.load(artifact["pt"], map_location="cpu", weights_only=True)
        assert checkpoint["format"] == "labelu_plate_recognition_v1"
        assert checkpoint["component"] == component
        model = onnx.load(artifact["onnx"])
        onnx.checker.check_model(model)
        assert model.graph.input[0].name == "images"

    assert onnx.load(artifacts["ocr"]["onnx"]).graph.output[0].name == (
        "sequence_logits"
    )
    assert artifacts["ocr"]["length_counts"] == {"3": 1}
    assert artifacts["ocr"]["preferred_length"] == 3
    assert artifacts["ocr"]["crop_ratios"] == [0.45, 0.5, 0.55]
    assert artifacts["ocr"]["fallback_crop_ratios"] == [0.47]
    assert artifacts["ocr"]["length_anchor_crop_ratio"] == 0.5
    assert artifacts["ocr"]["non_preferred_length_preserve_confidence"] == 0.99
    assert artifacts["ocr"]["guarded_consensus_enabled"] is True
    assert artifacts["ocr"]["anchor_group_min_confidence"] == 0.90
    assert artifacts["ocr"]["non_anchor_group_min_confidence"] == 0.94
    assert artifacts["ocr"]["real_finetune_epochs"] == 1
    assert artifacts["ocr"]["real_finetune_final_loss"] is not None
    for artifact in artifacts.values():
        assert artifact["evaluation_metrics"]["train"]["total"] == 1
        assert artifact["evaluation_metrics"]["val"] == {
            "correct": 0,
            "total": 0,
            "accuracy": None,
        }
        analysis_dir = Path(artifact["analysis"]["directory"])
        assert (analysis_dir / "progress.json").is_file()
        progress = json.loads(
            (analysis_dir / "progress.json").read_text(encoding="utf-8")
        )
        assert progress["current_epoch"] == progress["target_epochs"]
        assert (analysis_dir / "results.csv").is_file()
        assert (analysis_dir / "analysis.json").is_file()
        assert (analysis_dir / "results.png").is_file()
        assert (analysis_dir / "loss_curve.png").is_file()
        assert (analysis_dir / "confusion_matrix.png").is_file()
        assert (analysis_dir / "confusion_matrix_normalized.png").is_file()
        assert (analysis_dir / "validation_predictions.png").is_file()
    assert onnx.load(artifacts["color"]["onnx"]).graph.output[0].name == "logits"
