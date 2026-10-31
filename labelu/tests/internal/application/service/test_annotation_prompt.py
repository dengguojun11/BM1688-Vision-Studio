import json
from types import SimpleNamespace

import pytest

from labelu.internal.application.service.annotation_prompt import (
    _build_prompt_spec,
    _validate_effective_prompt,
    render_prompt_template,
)
from labelu.internal.application.service.annotation_contract import labels_snapshot
from labelu.internal.application.service import annotation_prompt as prompt_service
from labelu.internal.domain.models.annotation_prompt import AnnotationPromptBinding
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract


def _contract(*, labels=None, revision=3):
    labels = labels or [
        {
            "label_id": 11,
            "label_key": "行人",
            "label_value": "person",
            "annotation_kind": "bbox",
            "attributes": [],
        },
        {
            "label_id": 12,
            "label_key": "骑行电动车的人",
            "label_value": "person_electricbike",
            "annotation_kind": "bbox",
            "attributes": [],
        },
    ]
    return SimpleNamespace(
        id=42,
        revision=revision,
        annotation_kind="bbox",
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
        labels_snapshot_json=json.dumps(labels, ensure_ascii=False),
        dataset_id=7,
    )


def test_build_prompt_spec_uses_contract_order_and_runtime_size_placeholders():
    spec, prompt, warnings = _build_prompt_spec(
        _contract(),
        operation="bbox_detection",
        provider_key="volcengine",
        model_name="doubao-vision",
        profile_key="human_detection_bbox",
        profile_version="2",
        image_width=2560,
        image_height=1440,
    )

    assert [item["label_value"] for item in spec["labels"]] == [
        "person",
        "person_electricbike",
    ]
    assert spec["contract_id"] == 42
    assert spec["contract_revision"] == 3
    assert spec["image"] == {
        "width": 2560,
        "height": 1440,
        "coordinate_space": "pixel_xywh",
    }
    assert "{{image_width}}" in prompt
    assert "{{image_height}}" in prompt
    assert warnings


def test_build_prompt_spec_preserves_mixed_geometry_contract():
    contract = _contract(
        labels=[
            {
                "label_id": 11,
                "label_key": "车牌框",
                "label_value": "doubleEP",
                "annotation_kind": "bbox",
                "attributes": [],
            },
            {
                "label_id": 12,
                "label_key": "车牌四角点",
                "label_value": "platePoint",
                "annotation_kind": "polygon",
                "attributes": [],
            },
        ]
    )

    spec, prompt, _ = _build_prompt_spec(
        contract,
        operation="bbox_detection",
        provider_key="volcengine",
        model_name=None,
        profile_key="plate_detection_v1",
        profile_version="1",
        image_width=1920,
        image_height=1080,
    )

    assert [item["tool"] for item in spec["labels"]] == [
        "rectTool",
        "polygonTool",
    ]
    assert spec["labels"][1]["point_count"] == 4
    assert spec["constraints"]["allowed_tools"] == ["rectTool", "polygonTool"]
    assert "恰好输出 4 个点" in prompt
    _validate_effective_prompt(prompt, spec)


def test_render_prompt_template_replaces_transient_image_dimensions():
    rendered = render_prompt_template(
        "图像尺寸 {{image_width}} x {{image_height}}，{{image_width}} 不变",
        1280,
        720,
    )

    assert rendered == "图像尺寸 1280 x 720，1280 不变"


def test_effective_prompt_cannot_remove_contract_label_or_geometry_schema():
    spec, prompt, _ = _build_prompt_spec(
        _contract(),
        operation="bbox_detection",
        provider_key="volcengine",
        model_name=None,
        profile_key="annotation_bbox_v1",
        profile_version="1",
        image_width=1920,
        image_height=1080,
    )

    _validate_effective_prompt(prompt, spec)

    with pytest.raises(Exception, match="不能删除契约类别"):
        _validate_effective_prompt(prompt.replace("person_electricbike", ""), spec)

    with pytest.raises(Exception, match="rectTool"):
        _validate_effective_prompt(
            prompt.replace("rectTool", "polygonTool"),
            spec,
        )


def test_effective_prompt_does_not_treat_long_label_as_short_label():
    spec, prompt, _ = _build_prompt_spec(
        _contract(),
        operation="bbox_detection",
        provider_key="volcengine",
        model_name=None,
        profile_key="annotation_bbox_v1",
        profile_version="1",
        image_width=1920,
        image_height=1080,
    )

    edited = prompt.replace("label='person'", "label='human'")
    with pytest.raises(Exception, match="不能删除契约类别: person"):
        _validate_effective_prompt(edited, spec)


def test_global_tag_prompt_does_not_invent_geometry_labels():
    spec, prompt, warnings = _build_prompt_spec(
        _contract(),
        operation="global_tag",
        provider_key="volcengine",
        model_name=None,
        profile_key="global_tag_v1",
        profile_version="1",
        image_width=640,
        image_height=480,
    )

    assert spec["labels"] == []
    assert spec["operation"] == "global_tag"
    assert warnings == []
    assert "rectTool" not in prompt
    _validate_effective_prompt(prompt, spec)
    with pytest.raises(Exception, match="tagTool"):
        _validate_effective_prompt(prompt.replace("tagTool", "rectTool"), spec)


def test_label_snapshot_omits_empty_description_for_legacy_revision_stability():
    base = SimpleNamespace(
        id=9,
        label_key="人员",
        label_value="person",
        color="#123456",
        annotation_kind="bbox",
        attributes_json="[]",
        description=None,
        version=1,
    )
    described = SimpleNamespace(**{**base.__dict__, "description": "可见的人体目标"})

    assert "description" not in labels_snapshot([base])[0]
    assert labels_snapshot([described])[0]["description"] == "可见的人体目标"


def test_runtime_resolution_rejects_tampered_published_artifact(monkeypatch):
    contract = _contract()
    contract.dataset_id = 7
    task = SimpleNamespace(id=101, dataset_id=7, dataset_label_contract_id=None)
    dataset = SimpleNamespace(id=7, dataset_label_contract_id=42, deleted_at=None)
    row = SimpleNamespace(
        id=55,
        dataset_label_contract_id=42,
        task_annotation_contract_id=None,
        contract_revision=contract.revision,
        label_set_signature=contract.label_set_signature,
        class_order_signature=contract.class_order_signature,
        operation="bbox_detection",
        provider_key="volcengine",
        model_name=None,
        profile_key="annotation_bbox_v1",
        profile_version="1",
        prompt_spec_json=json.dumps(
            {
                "contract_id": contract.id,
                "contract_revision": contract.revision,
                "operation": "bbox_detection",
                "label_set_signature": contract.label_set_signature,
                "class_order_signature": contract.class_order_signature,
                "labels": json.loads(contract.labels_snapshot_json),
            },
            ensure_ascii=False,
        ),
        effective_prompt='person person_electricbike rectTool "results" x y width height',
        prompt_sha256=prompt_service._prompt_sha256(
            'person person_electricbike rectTool "results" x y width height'
        ),
        status="PUBLISHED",
    )

    class Query:
        def __init__(self, model):
            self.model = model

        def filter(self, *args, **kwargs):
            return self

        def one_or_none(self):
            if self.model is Dataset:
                return dataset
            if self.model is DatasetLabelContract:
                return contract
            return None

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return [row]

    class DB:
        def query(self, model):
            if model is AnnotationPromptBinding:

                class PromptQuery(Query):
                    def one_or_none(self):
                        return None

                return PromptQuery(model)
            return Query(model)

    monkeypatch.setattr(
        prompt_service.annotation_contract, "current_contract", lambda *_: None
    )
    assert prompt_service.resolve_published_prompt_for_task(DB(), task) is not None

    row.effective_prompt += " tampered"
    assert prompt_service.resolve_published_prompt_for_task(DB(), task) is None
