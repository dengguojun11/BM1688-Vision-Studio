from io import BytesIO

from PIL import Image
from sqlalchemy.orm import Session

from labelu.internal.application.service import vision_llm_chain
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample


def _image_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (120, 90), (20, 30, 40)).save(stream, format="PNG")
    return stream.getvalue()


def _seed_label(db: Session) -> AnnotationLabel:
    row = AnnotationLabel(
        label_key="人员",
        label_value="trial_person",
        normalized_label_value="trial_person",
        annotation_kind="bbox",
        color="#2266aa",
        attributes_json="[]",
        description="可见的人体目标",
        status="active",
        version=1,
        created_by=1,
        updated_by=1,
    )
    db.add(row)
    db.flush()
    return row


def test_trial_upload_calls_model_without_creating_annotation_records(
    client,
    testadmin_token_headers,
    db: Session,
    monkeypatch,
):
    label = _seed_label(db)
    db.commit()

    created = client.post(
        "/api/v1/datasets",
        headers=testadmin_token_headers,
        json={
            "name": "提示词试运行路由测试",
            "label_ids": [label.id],
            "annotation_kind": "bbox",
        },
    )
    assert created.status_code == 201, created.text
    dataset = created.json()["data"]
    dataset_id = dataset["id"]
    revision = dataset["dataset_label_contract_revision"]

    captured = {}

    def fake_call(payload, *, provider_key):
        captured["payload"] = payload
        captured["provider_key"] = provider_key
        return {
            "model": "trial-model",
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "trial_person",
                    "result": {"x": 2, "y": 3, "width": 30, "height": 40},
                    "score": 0.95,
                }
            ],
        }

    monkeypatch.setattr(vision_llm_chain, "call_vision_llm_chain", fake_call)
    samples_before = db.query(TaskSample).count()
    pre_annotations_before = db.query(TaskPreAnnotation).count()

    response = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts/trial",
        headers=testadmin_token_headers,
        data={
            "operation": "bbox_detection",
            "provider_key": "volcengine",
            "model_name": "trial-model",
            "profile_key": "annotation_bbox_v1",
            "profile_version": "1",
        },
        files={"image": ("trial.png", _image_bytes(), "image/png")},
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["status"] == "COMPLETED"
    assert payload["image_width"] == 120
    assert payload["image_height"] == 90
    assert payload["normalized_result"]["annotations"]["rectTool"]["result"][0][
        "label"
    ] == "trial_person"
    assert captured["provider_key"] == "bbox_detection"
    assert captured["payload"]["image_url"].startswith("data:image/png;base64,")
    assert captured["payload"]["contract_prompt"]["contract_revision"] == revision
    assert db.query(TaskSample).count() == samples_before
    assert db.query(TaskPreAnnotation).count() == pre_annotations_before
