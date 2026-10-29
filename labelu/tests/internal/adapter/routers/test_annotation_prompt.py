from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel


def _seed_label(
    db: Session,
    *,
    key: str,
    value: str,
    color: str,
    description: str,
) -> AnnotationLabel:
    row = AnnotationLabel(
        label_key=key,
        label_value=value,
        normalized_label_value=value.lower(),
        annotation_kind="bbox",
        color=color,
        attributes_json="[]",
        description=description,
        status="active",
        version=1,
        created_by=1,
        updated_by=1,
    )
    db.add(row)
    db.flush()
    return row


def test_annotation_prompt_contract_lifecycle(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    person = _seed_label(
        db,
        key="人员",
        value="person_prompt_api",
        color="#aa1101",
        description="可见的人体目标",
    )
    bicycle = _seed_label(
        db,
        key="自行车",
        value="bicycle_prompt_api",
        color="#aa1102",
        description="可见的自行车目标",
    )
    db.commit()

    dataset_response = client.post(
        "/api/v1/datasets",
        headers=testadmin_token_headers,
        json={
            "name": "提示词契约 API 测试",
            "label_ids": [person.id, bicycle.id],
            "annotation_kind": "bbox",
        },
    )
    assert dataset_response.status_code == 201, dataset_response.text
    dataset = dataset_response.json()["data"]
    dataset_id = dataset["id"]
    revision = dataset["dataset_label_contract_revision"]
    current = client.get(
        f"/api/v1/datasets/{dataset_id}/label-contracts/current",
        headers=testadmin_token_headers,
    )
    assert current.status_code == 200, current.text
    contract_payload = current.json()["data"]

    base = {
        "operation": "bbox_detection",
        "provider_key": "volcengine",
        "model_name": "doubao-vision-test",
        "profile_key": "human_detection_bbox",
        "profile_version": "1",
        "image_width": 1920,
        "image_height": 1080,
    }
    preview_response = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts/preview",
        headers=testadmin_token_headers,
        json=base,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()["data"]
    assert preview["status"] == "PREVIEW"
    assert preview["contract_id"] == contract_payload["id"]
    assert preview["contract_revision"] == revision
    assert preview["provider_key"] == "volcengine"
    assert preview["model_name"] == "doubao-vision-test"
    assert (
        preview["prompt_spec"]["label_set_signature"]
        == contract_payload["label_set_signature"]
    )
    assert "person_prompt_api" in preview["generated_prompt"]
    assert "bicycle_prompt_api" in preview["generated_prompt"]
    assert "可见的人体目标" in preview["generated_prompt"]
    assert "{{image_width}}" in preview["generated_prompt"]

    edited_prompt = preview["generated_prompt"] + "\n仅返回契约内目标。"
    save_response = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts",
        headers=testadmin_token_headers,
        json={
            **base,
            "expected_label_set_signature": preview["label_set_signature"],
            "expected_class_order_signature": preview["class_order_signature"],
            "generated_prompt": preview["generated_prompt"],
            "edited_prompt": edited_prompt,
            "effective_prompt": edited_prompt,
        },
    )
    assert save_response.status_code == 201, save_response.text
    draft = save_response.json()["data"]
    assert draft["status"] == "DRAFT"
    assert draft["provider_key"] == "volcengine"
    assert draft["model_name"] == "doubao-vision-test"
    assert draft["effective_prompt"] == edited_prompt
    assert len(draft["prompt_sha256"]) == 64

    revised_prompt = edited_prompt + "\n输出前检查遮挡目标。"
    update_response = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts",
        headers=testadmin_token_headers,
        json={
            **base,
            "expected_prompt_id": draft["id"],
            "expected_label_set_signature": draft["label_set_signature"],
            "expected_class_order_signature": draft["class_order_signature"],
            "effective_prompt": revised_prompt,
        },
    )
    assert update_response.status_code == 201, update_response.text
    updated_draft = update_response.json()["data"]
    assert updated_draft["id"] == draft["id"]
    assert updated_draft["effective_prompt"] == revised_prompt
    assert updated_draft["prompt_sha256"] != draft["prompt_sha256"]

    listed = client.get(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts",
        headers=testadmin_token_headers,
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["data"]] == [draft["id"]]

    publish_response = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts/{draft['id']}/publish",
        headers=testadmin_token_headers,
        json={
            "expected_contract_revision": revision,
            "expected_prompt_sha256": updated_draft["prompt_sha256"],
        },
    )
    assert publish_response.status_code == 200, publish_response.text
    published = publish_response.json()["data"]
    assert published["status"] == "PUBLISHED"
    assert published["published_at"] is not None

    stale_signature = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts",
        headers=testadmin_token_headers,
        json={
            **base,
            "expected_label_set_signature": "0" * 64,
            "effective_prompt": edited_prompt,
        },
    )
    assert stale_signature.status_code == 409, stale_signature.text

    cannot_edit_published = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts",
        headers=testadmin_token_headers,
        json={
            **base,
            "expected_prompt_id": published["id"],
            "effective_prompt": edited_prompt,
            "expected_label_set_signature": published["label_set_signature"],
            "expected_class_order_signature": published["class_order_signature"],
        },
    )
    assert cannot_edit_published.status_code == 409, cannot_edit_published.text

    bad_hash = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts/{draft['id']}/publish",
        headers=testadmin_token_headers,
        json={"expected_prompt_sha256": "f" * 64},
    )
    assert bad_hash.status_code == 409, bad_hash.text


def test_annotation_prompt_preview_requires_contract_access(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
):
    response = client.post(
        "/api/v1/datasets/999999/label-contracts/1/ai-prompts/preview",
        headers=testadmin_token_headers,
        json={"operation": "bbox_detection"},
    )
    assert response.status_code in {400, 404}
