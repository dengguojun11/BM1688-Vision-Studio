from fastapi.testclient import TestClient
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
        status="active",
        version=1,
        created_by=1,
        updated_by=1,
    )
    db.add(row)
    db.flush()
    return row


def test_dataset_creation_exposes_and_freezes_canonical_label_contract(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    first = _seed_label(db, key="stall", value="占道经营", color="#123456")
    second = _seed_label(db, key="person", value="人员", color="#654321")
    db.commit()

    created = client.post(
        "/api/v1/datasets",
        json={
            "name": "占道经营训练数据集",
            "label_ids": [first.id, second.id],
            "annotation_kind": "bbox",
        },
        headers=testadmin_token_headers,
    )
    assert created.status_code == 201, created.text
    dataset = created.json()["data"]
    assert dataset["dataset_label_contract_id"] is not None
    assert dataset["dataset_label_ids"] == [first.id, second.id]

    current = client.get(
        f"/api/v1/datasets/{dataset['id']}/label-contracts/current",
        headers=testadmin_token_headers,
    )
    assert current.status_code == 200, current.text
    contract = current.json()["data"]
    assert "project_id" not in contract
    assert contract["revision"] == 1
    assert contract["label_ids"] == [first.id, second.id]

    frozen = client.post(
        f"/api/v1/datasets/{dataset['id']}/label-contracts/1/freeze",
        headers=testadmin_token_headers,
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["data"]["status"] == "FROZEN"

    refreshed = client.get(
        f"/api/v1/datasets/{dataset['id']}",
        headers=testadmin_token_headers,
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["data"]["dataset_label_contract_status"] == "FROZEN"

    labels = refreshed.json()["data"]["dataset_labels"]
    assert [item["label_id"] for item in labels] == [first.id, second.id]
