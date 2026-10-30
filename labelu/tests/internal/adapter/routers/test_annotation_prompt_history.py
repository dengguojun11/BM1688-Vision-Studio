from sqlalchemy.orm import Session

from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel


def _seed_label(db: Session, *, key: str, value: str, color: str) -> AnnotationLabel:
    row = AnnotationLabel(
        label_key=key,
        label_value=value,
        normalized_label_value=value.lower(),
        annotation_kind="bbox",
        color=color,
        attributes_json="[]",
        description=None,
        status="active",
        version=1,
        created_by=1,
        updated_by=1,
    )
    db.add(row)
    db.flush()
    return row


def test_contract_revision_invalidates_prompt_and_history_keeps_record(
    client,
    testadmin_token_headers,
    db: Session,
):
    person = _seed_label(db, key="人员", value="history_person", color="#aa1101")
    vehicle = _seed_label(db, key="车辆", value="history_vehicle", color="#aa1102")
    bicycle = _seed_label(db, key="自行车", value="history_bicycle", color="#aa1103")
    db.commit()

    created = client.post(
        "/api/v1/datasets",
        headers=testadmin_token_headers,
        json={
            "name": "提示词历史契约测试",
            "label_ids": [person.id, vehicle.id],
            "annotation_kind": "bbox",
        },
    )
    assert created.status_code == 201, created.text
    dataset = created.json()["data"]
    dataset_id = dataset["id"]
    revision = dataset["dataset_label_contract_revision"]

    preview = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts/preview",
        headers=testadmin_token_headers,
        json={"operation": "bbox_detection", "provider_key": "volcengine"},
    )
    assert preview.status_code == 200, preview.text
    prompt = preview.json()["data"]
    saved = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts",
        headers=testadmin_token_headers,
        json={
            "operation": "bbox_detection",
            "provider_key": "volcengine",
            "effective_prompt": prompt["generated_prompt"],
            "expected_label_set_signature": prompt["label_set_signature"],
            "expected_class_order_signature": prompt["class_order_signature"],
        },
    )
    assert saved.status_code == 201, saved.text
    draft = saved.json()["data"]

    # Keep a second, unsaved model variant so contract replacement covers
    # both executable and editable prompt artifacts.
    second_draft = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts",
        headers=testadmin_token_headers,
        json={
            "operation": "bbox_detection",
            "provider_key": "volcengine",
            "model_name": "history-draft-model",
            "effective_prompt": prompt["generated_prompt"],
            "expected_label_set_signature": prompt["label_set_signature"],
            "expected_class_order_signature": prompt["class_order_signature"],
        },
    )
    assert second_draft.status_code == 201, second_draft.text

    published = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision}/ai-prompts/{draft['id']}/publish",
        headers=testadmin_token_headers,
        json={"expected_prompt_sha256": draft["prompt_sha256"]},
    )
    assert published.status_code == 200, published.text

    replacement = client.post(
        f"/api/v1/datasets/{dataset_id}/label-contracts",
        headers=testadmin_token_headers,
        json={"label_ids": [person.id, bicycle.id], "annotation_kind": "bbox"},
    )
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["data"]["revision"] == revision + 1

    history = client.get(
        f"/api/v1/datasets/{dataset_id}/ai-prompts/history",
        headers=testadmin_token_headers,
    )
    assert history.status_code == 200, history.text
    rows = history.json()["data"]
    old_rows = [row for row in rows if row["contract_revision"] == revision]
    assert len(old_rows) == 2
    assert {row["status"] for row in old_rows} == {"INVALIDATED"}

    active_only = client.get(
        f"/api/v1/datasets/{dataset_id}/ai-prompts/history?include_invalidated=false",
        headers=testadmin_token_headers,
    )
    assert active_only.status_code == 200, active_only.text
    assert all(
        row["contract_revision"] != revision
        for row in active_only.json()["data"]
    )

    current = client.get(
        f"/api/v1/datasets/{dataset_id}/label-contracts/{revision + 1}/ai-prompts",
        headers=testadmin_token_headers,
    )
    assert current.status_code == 200, current.text
    assert current.json()["data"] == []
