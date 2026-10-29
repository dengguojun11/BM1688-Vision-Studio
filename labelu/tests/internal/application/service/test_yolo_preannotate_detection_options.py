"""YOLO detection controls preserve legacy requests without network or inference."""

import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from labelu.internal.application.command.yolo_preannotate import (
    BatchYoloPreannotateCommand,
)
from labelu.internal.application.service import yolo_preannotate
from labelu.internal.common import db as db_module

TASK_CONFIG = {
    "tools": [
        {
            "tool": "rectTool",
            "config": {"attributes": [{"key": "person", "value": "person"}]},
        }
    ]
}


@pytest.mark.parametrize(
    "overwrite,canvas,expected",
    [
        (True, None, True),
        (False, None, False),
        (True, False, False),
        (False, True, True),
    ],
)
def test_command_and_durable_payload_keep_canvas_control_independent(
    overwrite, canvas, expected
):
    options = (
        {}
        if canvas is None
        else {"apply_to_canvas_overwrite": canvas, "agnostic_nms": True}
    )
    command = BatchYoloPreannotateCommand(
        model_kind="imported", model_id=1, overwrite=overwrite, **options
    )
    assert command.apply_to_canvas_overwrite is canvas
    assert command.agnostic_nms is bool(options)

    payload = yolo_preannotate._build_worker_payload(
        task_id=1,
        user_id=1,
        sample_ids=[2],
        weights_path=Path("offline.pt"),
        imgsz=1920,
        conf=0.25,
        overwrite=command.overwrite,
        model_kind=command.model_kind,
        model_id=command.model_id,
        model_runtime={},
        apply_to_canvas_overwrite=command.apply_to_canvas_overwrite,
        agnostic_nms=command.agnostic_nms,
    )
    stored = json.loads(json.dumps(payload))
    assert stored["overwrite"] is overwrite
    assert stored["apply_to_canvas_overwrite"] is expected
    assert stored["agnostic_nms"] is command.agnostic_nms


@pytest.mark.parametrize(
    "overwrite,canvas,expected,agnostic_nms",
    [
        (True, None, True, False),
        (False, None, False, False),
        (True, False, False, True),
        (False, True, True, True),
    ],
)
def test_sync_runtime_forwards_effective_canvas_and_nms_options(
    monkeypatch, overwrite, canvas, expected, agnostic_nms
):
    job = SimpleNamespace(task_contract_id=None, labels_snapshot_json=None)
    task = SimpleNamespace(id=1)
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        SimpleNamespace(YOLO=lambda _: SimpleNamespace(names={0: "person"})),
    )
    monkeypatch.setattr(db_module, "session_scope", lambda: nullcontext(object()))
    monkeypatch.setattr(yolo_preannotate, "begin_transaction", lambda _: nullcontext())
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_preannotate_job, "get", lambda **_: job
    )
    monkeypatch.setattr(yolo_preannotate.crud_task, "get", lambda **_: task)
    monkeypatch.setattr(
        yolo_preannotate.auto_label_mod,
        "resolve_ai_task_config",
        lambda *_: TASK_CONFIG,
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_preannotate_job,
        "increment_progress",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_preannotate_job,
        "update_status",
        lambda *_, **__: None,
    )

    def process(**kwargs):
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(yolo_preannotate, "_process_one_sample", process)
    yolo_preannotate._run_yolo_preannotate_sync(
        1,
        1,
        [2],
        "offline.pt",
        1920,
        0.25,
        overwrite,
        "imported",
        1,
        1,
        apply_to_canvas_overwrite=canvas,
        agnostic_nms=agnostic_nms,
    )
    assert captured["overwrite"] is overwrite
    assert captured["apply_to_canvas_overwrite"] is expected
    assert captured["agnostic_nms"] is agnostic_nms


@pytest.mark.parametrize("canvas", [False, True])
@pytest.mark.parametrize("agnostic_nms", [False, True])
def test_sample_prediction_replaces_yolo_record_without_forcing_canvas(
    monkeypatch, canvas, agnostic_nms
):
    captured = {}
    sample = SimpleNamespace(
        id=2,
        task_id=1,
        data='{"result":"manual geometry"}',
        file=SimpleNamespace(id=3, filename="offline.jpg", path="offline.jpg"),
    )

    class Boxes:
        xyxy = [[10, 20, 40, 80]]
        cls = [0]
        conf = [0.9]

        def __len__(self):
            return 1

    def predict(**kwargs):
        captured["predict"] = kwargs
        return [SimpleNamespace(boxes=Boxes())]

    def store(**kwargs):
        captured["payload"] = json.loads(kwargs["pre_annotations"][0].data)
        return kwargs["pre_annotations"]

    def delete(*args):
        captured["delete"] = args[1:]
        return 1

    monkeypatch.setattr(db_module, "session_scope", lambda: nullcontext(object()))
    monkeypatch.setattr(yolo_preannotate, "begin_transaction", lambda _: nullcontext())
    monkeypatch.setattr(yolo_preannotate.crud_sample, "get", lambda **_: sample)
    monkeypatch.setattr(
        yolo_preannotate, "_predict_source_for_attachment", lambda _: "offline.jpg"
    )
    monkeypatch.setattr(
        yolo_preannotate, "_delete_yolo_preannotations_for_sample", delete
    )
    monkeypatch.setattr(yolo_preannotate.crud_pre_annotation, "batch", store)

    count = yolo_preannotate._process_one_sample(
        task_id=1,
        task_cfg_merged=TASK_CONFIG,
        sample_id=2,
        model=SimpleNamespace(predict=predict),
        names_map={0: "person"},
        allowed_labels={"person"},
        imgsz=1920,
        conf=0.25,
        overwrite=True,
        model_kind="imported",
        model_id=1,
        user_id=1,
        weights_name="offline.pt",
        apply_to_canvas_overwrite=canvas,
        agnostic_nms=agnostic_nms,
    )
    assert count == 1
    assert captured["delete"] == (1, "offline.jpg")
    assert captured["predict"]["agnostic_nms"] is agnostic_nms
    assert captured["predict"]["imgsz"] == 1920
    meta = captured["payload"]["meta"]
    assert meta["include_geometry_tools"] is True
    assert meta.get("apply_to_canvas_overwrite", False) is canvas
    assert meta.get("overwrite_existing_annotations", False) is canvas
    assert sample.data == '{"result":"manual geometry"}'
