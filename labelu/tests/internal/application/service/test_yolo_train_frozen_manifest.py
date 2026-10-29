import hashlib
import json
from types import SimpleNamespace

from labelu.internal.application.service import yolo_train


class _Storage:
    def __init__(self, files):
        self.files = files

    def read_text(self, relative_path):
        return self.files[relative_path]

    def get_local_path(self, relative_path):
        return self.files[relative_path]


def test_frozen_sample_ids_for_job_reads_immutable_manifest(monkeypatch):
    manifest_path = "datasets/project-1/dataset-3/v2/manifest.json"
    manifest = {
        "sample_count": 2,
        "samples": [
            {"sample_id": 118300, "task_id": 150},
            {"sample_id": 118486, "task_id": 150},
        ],
    }
    monkeypatch.setattr(
        yolo_train,
        "get_storage_backend",
        lambda: _Storage({manifest_path: json.dumps(manifest)}),
    )

    raw_manifest = json.dumps(manifest, ensure_ascii=False)
    job = SimpleNamespace(
        hyperparams_json=json.dumps(
            {
                "dataset_manifest": {
                    "manifest_rel_path": manifest_path,
                    "manifest_sha256": hashlib.sha256(
                        raw_manifest.encode("utf-8")
                    ).hexdigest(),
                }
            }
        )
    )

    assert yolo_train._frozen_sample_ids_for_job(job) == {118300, 118486}


def test_frozen_sample_ids_for_job_rejects_manifest_digest_mismatch(monkeypatch):
    manifest_path = "datasets/dataset-3/v1/manifest.json"
    manifest = {"samples": [{"sample_id": 118300, "task_id": 150}]}
    raw_manifest = json.dumps(manifest, ensure_ascii=False)
    monkeypatch.setattr(
        yolo_train,
        "get_storage_backend",
        lambda: _Storage({manifest_path: raw_manifest}),
    )
    job = SimpleNamespace(
        hyperparams_json=json.dumps(
            {
                "dataset_manifest": {
                    "manifest_rel_path": manifest_path,
                    "manifest_sha256": "0" * 64,
                }
            }
        )
    )

    try:
        yolo_train._frozen_sample_ids_for_job(job)
    except RuntimeError as exc:
        assert "manifest_sha256" in str(exc)
    else:
        raise AssertionError("清单摘要不一致时必须失败关闭，不能回退任务数据")


def test_frozen_sample_ids_for_job_rejects_unreadable_hashed_manifest(monkeypatch):
    manifest_path = "datasets/dataset-3/v1/missing.json"
    monkeypatch.setattr(
        yolo_train,
        "get_storage_backend",
        lambda: _Storage({}),
    )
    job = SimpleNamespace(
        hyperparams_json=json.dumps(
            {
                "dataset_manifest": {
                    "manifest_rel_path": manifest_path,
                    "manifest_sha256": "a" * 64,
                }
            }
        )
    )

    try:
        yolo_train._frozen_sample_ids_for_job(job)
    except RuntimeError as exc:
        assert "不可读" in str(exc)
    else:
        raise AssertionError("冻结清单不可读时必须失败关闭")


def test_frozen_sample_ids_for_job_preserves_legacy_compatibility(monkeypatch):
    monkeypatch.setattr(
        yolo_train,
        "get_storage_backend",
        lambda: _Storage({}),
    )

    legacy_job = SimpleNamespace(hyperparams_json=json.dumps({}))
    unreadable_manifest_job = SimpleNamespace(
        hyperparams_json=json.dumps(
            {
                "dataset_manifest": {
                    "manifest_rel_path": "missing/manifest.json",
                }
            }
        )
    )

    assert yolo_train._frozen_sample_ids_for_job(legacy_job) == set()
    assert yolo_train._frozen_sample_ids_for_job(unreadable_manifest_job) == set()


def _frozen_sample_fixture(tmp_path, *, annotation):
    image_path = tmp_path / "sample.jpg"
    image_path.write_bytes(b"frozen-image")
    sample = SimpleNamespace(
        id=1,
        data=json.dumps(annotation, ensure_ascii=False),
        file=SimpleNamespace(path="sample.jpg", sha256=None),
    )
    manifest_sample = {
        "sample_id": 1,
        "asset_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "annotation_sha256": yolo_train.canonical_annotation_sha256(annotation),
    }
    return image_path, sample, manifest_sample


def test_frozen_sample_content_rejects_annotation_change(monkeypatch, tmp_path):
    image_path, sample, manifest_sample = _frozen_sample_fixture(
        tmp_path, annotation={"rectTool": {"result": [{"label": "车牌"}]}}
    )
    monkeypatch.setattr(
        yolo_train,
        "get_storage_backend",
        lambda: _Storage({"sample.jpg": image_path}),
    )
    sample.data = json.dumps({"rectTool": {"result": [{"label": "车辆"}]}})

    try:
        yolo_train._validate_frozen_sample_content([sample], [manifest_sample])
    except RuntimeError as exc:
        assert "标注内容已变化" in str(exc)
    else:
        raise AssertionError("冻结后标注发生变化时必须失败关闭")


def test_frozen_sample_content_rejects_image_change(monkeypatch, tmp_path):
    image_path, sample, manifest_sample = _frozen_sample_fixture(
        tmp_path, annotation={"rectTool": {"result": [{"label": "车牌"}]}}
    )
    monkeypatch.setattr(
        yolo_train,
        "get_storage_backend",
        lambda: _Storage({"sample.jpg": image_path}),
    )
    image_path.write_bytes(b"changed-image")

    try:
        yolo_train._validate_frozen_sample_content([sample], [manifest_sample])
    except RuntimeError as exc:
        assert "图片内容已变化" in str(exc)
    else:
        raise AssertionError("冻结后图片发生变化时必须失败关闭")


def test_frozen_sample_content_uses_source_annotation_for_label_subset(
    monkeypatch, tmp_path
):
    source_annotation = {
        "rectTool": {
            "result": [
                {"label": "车牌"},
                {"label": "车辆"},
            ]
        }
    }
    image_path, sample, manifest_sample = _frozen_sample_fixture(
        tmp_path, annotation=source_annotation
    )
    projected_annotation = {"rectTool": {"result": [{"label": "车牌"}]}}
    manifest_sample["annotation_sha256"] = yolo_train.canonical_annotation_sha256(
        projected_annotation
    )
    manifest_sample["source_annotation_sha256"] = yolo_train.canonical_annotation_sha256(
        source_annotation
    )
    monkeypatch.setattr(
        yolo_train,
        "get_storage_backend",
        lambda: _Storage({"sample.jpg": image_path}),
    )

    yolo_train._validate_frozen_sample_content([sample], [manifest_sample])
