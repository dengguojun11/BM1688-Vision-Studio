import json
from types import SimpleNamespace

import pytest
from PIL import Image

from labelu.internal.application.service import yolo_dataset


def _sample(sample_id: int, attachment):
    result = {
        "rectTool": {
            "result": [
                {"label": "car", "x": 5, "y": 5, "width": 20, "height": 20}
            ]
        }
    }
    return SimpleNamespace(
        id=sample_id,
        task_id=1,
        state="DONE",
        file=attachment,
        data=json.dumps({"result": json.dumps(result)}),
    )


def _config():
    return {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "car", "value": "car"}]},
            }
        ]
    }


def test_prepare_yolo_dataset_materializes_datasource_images(tmp_path, monkeypatch):
    source = tmp_path / "source.jpg"
    Image.new("RGB", (80, 60), "orange").save(source)
    attachments = [
        SimpleNamespace(
            id=index,
            filename=f"external-{index}.jpg",
            path=f"feedback/{index}.jpg",
            data_source_id=9,
        )
        for index in (1, 2)
    ]
    monkeypatch.setattr(
        "labelu.internal.application.service.attachment._materialize_datasource_attachment_to_cache",
        lambda attachment: (source, "image/jpeg"),
    )

    train_n, val_n, test_n, classes, _ = yolo_dataset.prepare_yolo_dataset(
        task_config=_config(),
        samples=[_sample(1, attachments[0]), _sample(2, attachments[1])],
        media_root=tmp_path,
        dataset_root=tmp_path / "dataset",
        val_ratio=0.5,
        test_ratio=0,
    )

    assert train_n == 1
    assert val_n == 1
    assert test_n == 0
    assert classes == ["car"]
    assert len(list((tmp_path / "dataset" / "images" / "train").glob("*.jpg"))) == 1


def test_prepare_yolo_dataset_fails_closed_when_datasource_fetch_fails(
    tmp_path, monkeypatch
):
    attachment = SimpleNamespace(
        id=1,
        filename="external.jpg",
        path="feedback/missing.jpg",
        data_source_id=9,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.attachment._materialize_datasource_attachment_to_cache",
        lambda attachment: (_ for _ in ()).throw(RuntimeError("S3 404")),
    )

    with pytest.raises(ValueError, match="第三方数据源图片物化失败"):
        yolo_dataset.prepare_yolo_dataset(
            task_config=_config(),
            samples=[_sample(1, attachment), _sample(2, attachment)],
            media_root=tmp_path,
            dataset_root=tmp_path / "dataset",
            val_ratio=0.5,
            test_ratio=0,
        )
