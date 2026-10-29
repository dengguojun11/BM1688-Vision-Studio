"""标注与数据治理列表的中英文搜索契约。"""

import json

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.dataset_label_contract import DatasetLabelContract


def _create_label(client, headers, *, label_key: str, label_value: str, color: str):
    response = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=headers,
        json={
            "label_key": label_key,
            "label_value": label_value,
            "annotation_kind": "bbox",
            "color": color,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_label_search_matches_chinese_key_and_case_insensitive_english_value(
    client, testadmin_token_headers
):
    _create_label(
        client,
        testadmin_token_headers,
        label_key="行人",
        label_value="person",
        color="#304050",
    )
    _create_label(
        client,
        testadmin_token_headers,
        label_key="车辆",
        label_value="vehicle",
        color="#405060",
    )
    base = f"{settings.API_V1_STR}/annotation-taxonomy/labels"
    for keyword, expected_value in (("行人", "person"), ("PERSON", "person")):
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20, "include_disabled": "true"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["total"] == 1
        assert body["data"][0]["label_value"] == expected_value

    last_page = client.get(
        base,
        headers=testadmin_token_headers,
        params={"page": 1, "size": 1, "include_disabled": "true"},
    )
    assert last_page.status_code == 200, last_page.text
    last_body = last_page.json()
    assert last_body["meta_data"] == {"total": 2, "page": 1, "size": 1}
    assert len(last_body["data"]) == 1

    empty_page = client.get(
        base,
        headers=testadmin_token_headers,
        params={"page": 2, "size": 1, "include_disabled": "true"},
    )
    assert empty_page.status_code == 200, empty_page.text
    empty_body = empty_page.json()
    assert empty_body["meta_data"] == {"total": 2, "page": 2, "size": 0}
    assert empty_body["data"] == []


def test_global_dimension_search_matches_chinese_english_fields_and_options(
    client, testadmin_token_headers
):
    for payload in (
        {
            "field_key": "scene_type",
            "name_cn": "场景类型",
            "options": [
                {"name_cn": "室内", "value_en": "indoor"},
                {"name_cn": "室外", "value_en": "outdoor"},
            ],
        },
        {
            "field_key": "weather_type",
            "name_cn": "天气类型",
            "options": [{"name_cn": "晴天", "value_en": "sunny"}],
        },
    ):
        created = client.post(
            f"{settings.API_V1_STR}/annotation-taxonomy/global-tag-dimensions",
            headers=testadmin_token_headers,
            json=payload,
        )
        assert created.status_code == 201, created.text

    base = f"{settings.API_V1_STR}/annotation-taxonomy/global-tag-dimensions"
    for keyword in ("场景", "scene_type", "室", "室内", "INDOOR"):
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["total"] == 1
        assert len(body["data"]) == 1
        assert body["data"][0]["field_key"] == "scene_type"

    paged = client.get(
        base,
        headers=testadmin_token_headers,
        params={"page": 0, "size": 1},
    )
    assert paged.status_code == 200, paged.text
    assert paged.json()["meta_data"] == {"total": 2, "page": 0, "size": 1}
    assert len(paged.json()["data"]) == 1

    blank = client.get(
        base,
        headers=testadmin_token_headers,
        params={"q": "   ", "page": 0, "size": 20},
    )
    assert blank.status_code == 200, blank.text
    assert blank.json()["meta_data"]["total"] == 2


def test_dataset_search_matches_name_description_and_current_contract_labels(
    client, db, testadmin_token_headers
):
    label = _create_label(
        client,
        testadmin_token_headers,
        label_key="车辆",
        label_value="vehicle",
        color="#102030",
    )
    second_label = _create_label(
        client,
        testadmin_token_headers,
        label_key="天气",
        label_value="weather",
        color="#203040",
    )
    first = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={
            "name": "城市道路数据",
            "description": "Road traffic training set",
            "label_ids": [label["id"]],
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={
            "name": "天气样本",
            "description": "Weather samples",
            "label_ids": [second_label["id"]],
        },
    )
    assert second.status_code == 201, second.text

    # Older installations serialized Chinese snapshot text as ``\\uXXXX``.
    # Keep the search contract valid for that historical representation too.
    with begin_transaction(db):
        first_contract = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.dataset_id == first.json()["data"]["id"])
            .one()
        )
        first_contract.labels_snapshot_json = json.dumps(
            json.loads(first_contract.labels_snapshot_json),
            ensure_ascii=True,
            separators=(",", ":"),
        )

    base = f"{settings.API_V1_STR}/datasets"
    expected = {
        "城市道路": "城市道路数据",
        "Road traffic": "城市道路数据",
        "VEHICLE": "城市道路数据",
        "天气": "天气样本",
        "weather samples": "天气样本",
    }
    for keyword, expected_name in expected.items():
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["total"] == 1
        assert [row["name"] for row in body["data"]] == [expected_name]

    paged = client.get(
        base,
        headers=testadmin_token_headers,
        params={"page": 1, "size": 1},
    )
    assert paged.status_code == 200, paged.text
    assert paged.json()["meta_data"] == {"total": 2, "page": 1, "size": 1}
    assert len(paged.json()["data"]) == 1

    blank = client.get(
        base,
        headers=testadmin_token_headers,
        params={"q": "", "page": 0, "size": 20},
    )
    assert blank.status_code == 200, blank.text
    assert blank.json()["meta_data"]["total"] == 2


def test_dataset_search_keeps_user_visibility_filter(
    client, testuser_token_headers, testadmin_token_headers
):
    own = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testuser_token_headers,
        json={"name": "用户可见数据集", "description": "shared keyword"},
    )
    assert own.status_code == 201, own.text
    hidden = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "管理员隐藏数据集", "description": "shared keyword"},
    )
    assert hidden.status_code == 201, hidden.text

    response = client.get(
        f"{settings.API_V1_STR}/datasets",
        headers=testuser_token_headers,
        params={"q": "shared keyword", "page": 0, "size": 20},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta_data"]["total"] == 1
    assert [row["name"] for row in body["data"]] == ["用户可见数据集"]


def test_dataset_search_falls_back_to_latest_contract_when_pointer_is_missing(
    client, db, testadmin_token_headers
):
    label = _create_label(
        client,
        testadmin_token_headers,
        label_key="历史车辆",
        label_value="legacy_vehicle",
        color="#304050",
    )
    created = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "历史数据集", "label_ids": [label["id"]]},
    )
    assert created.status_code == 201, created.text
    dataset_id = created.json()["data"]["id"]

    # Rows created before the pointer column was introduced can still have a
    # usable contract keyed by dataset_id, while the pointer itself is null.
    with begin_transaction(db):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
        dataset.dataset_label_contract_id = None
        sibling = Dataset(
            owner_id=dataset.owner_id,
            name="其他历史数据集",
            description=None,
            status="ACTIVE",
            created_by=dataset.created_by,
            updated_by=dataset.updated_by,
        )
        db.add(sibling)
        db.flush()
        # A higher revision on a different dataset must not hide the first
        # dataset's fallback contract from the search.
        db.add(
            DatasetLabelContract(
                dataset_id=sibling.id,
                revision=2,
                annotation_kind="bbox",
                label_ids_json="[]",
                labels_snapshot_json="other",
                label_set_signature="b" * 64,
                class_order_signature="b" * 64,
                status="DRAFT",
                created_by=dataset.created_by,
            )
        )

    base = f"{settings.API_V1_STR}/datasets"
    for keyword in ("历史车辆", "LEGACY_VEHICLE"):
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["total"] == 1
        assert [row["name"] for row in body["data"]] == ["历史数据集"]


def test_dataset_search_ignores_older_contract_revision_for_pointerless_history(
    client, db, testadmin_token_headers
):
    label = _create_label(
        client,
        testadmin_token_headers,
        label_key="旧标签",
        label_value="old_vehicle",
        color="#405060",
    )
    created = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "多版本历史数据集", "label_ids": [label["id"]]},
    )
    assert created.status_code == 201, created.text
    dataset_id = created.json()["data"]["id"]

    with begin_transaction(db):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
        first_contract = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.dataset_id == dataset_id)
            .order_by(DatasetLabelContract.revision.asc())
            .first()
        )
        assert first_contract is not None
        first_contract.labels_snapshot_json = (
            '[{"label_key":"旧标签","label_value":"old_vehicle"}]'
        )
        db.add(
            DatasetLabelContract(
                dataset_id=dataset_id,
                revision=int(first_contract.revision) + 1,
                annotation_kind="bbox",
                label_ids_json="[]",
                labels_snapshot_json=(
                    '[{"label_key":"新标签","label_value":"new_vehicle"}]'
                ),
                label_set_signature="c" * 64,
                class_order_signature="c" * 64,
                status="DRAFT",
                created_by=dataset.created_by,
            )
        )
        # Exercise the legacy path that has no current-contract pointer.
        dataset.dataset_label_contract_id = None

    base = f"{settings.API_V1_STR}/datasets"
    for keyword in ("旧标签", "OLD_VEHICLE"):
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20},
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 0

    for keyword in ("新标签", "NEW_VEHICLE"):
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["total"] == 1
        assert [row["name"] for row in body["data"]] == ["多版本历史数据集"]


def test_dataset_search_skips_empty_or_invalid_pointer_snapshot_and_uses_latest_valid(
    client, db, testadmin_token_headers
):
    label = _create_label(
        client,
        testadmin_token_headers,
        label_key="最新车辆",
        label_value="latest_vehicle",
        color="#415161",
    )
    created = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "空指针快照数据集", "label_ids": [label["id"]]},
    )
    assert created.status_code == 201, created.text
    dataset_id = created.json()["data"]["id"]

    base = f"{settings.API_V1_STR}/datasets"
    with begin_transaction(db):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
        pointer = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == dataset.dataset_label_contract_id)
            .one()
        )
        pointer.labels_snapshot_json = "[]"
        db.add(
            DatasetLabelContract(
                dataset_id=dataset_id,
                revision=int(pointer.revision) + 1,
                annotation_kind="bbox",
                label_ids_json=f'[{label["id"]}]',
                labels_snapshot_json=(
                    '[{"label_key":"最新车辆","label_value":"latest_vehicle",'
                    '"label_id":%d}]' % label["id"]
                ),
                label_set_signature="e" * 64,
                class_order_signature="e" * 64,
                status="ACTIVE",
                created_by=dataset.created_by,
            )
        )

    for keyword in ("最新车辆", "LATEST_VEHICLE"):
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["meta_data"]["total"] == 1
        assert body["data"][0]["name"] == "空指针快照数据集"

    # A non-empty array containing only IDs/metadata is also incomplete and
    # must not block the usable higher revision.
    with begin_transaction(db):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
        pointer = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == dataset.dataset_label_contract_id)
            .one()
        )
        pointer.labels_snapshot_json = json.dumps(
            [
                {
                    "label_id": label["id"],
                    "label_key": "最新车辆",
                    "label_value": "latest_vehicle",
                },
                {"label_id": label["id"], "annotation_kind": "bbox"},
            ],
            separators=(",", ":"),
        )

    response = client.get(
        base,
        headers=testadmin_token_headers,
        params={"q": "latest_vehicle", "page": 0, "size": 20},
    )
    assert response.status_code == 200, response.text
    assert response.json()["meta_data"]["total"] == 1

    # An invalid pointer snapshot must follow the same fallback path.
    with begin_transaction(db):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
        pointer = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == dataset.dataset_label_contract_id)
            .one()
        )
        pointer.labels_snapshot_json = "not-json"

    response = client.get(
        base,
        headers=testadmin_token_headers,
        params={"q": "latest_vehicle", "page": 0, "size": 20},
    )
    assert response.status_code == 200, response.text
    assert response.json()["meta_data"]["total"] == 1


def test_dataset_search_only_matches_label_key_and_value_not_snapshot_metadata(
    client, db, testadmin_token_headers
):
    label = _create_label(
        client,
        testadmin_token_headers,
        label_key="车辆",
        label_value="vehicle",
        color="#ABCDEF",
    )
    created = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "结构字段搜索数据集", "label_ids": [label["id"]]},
    )
    assert created.status_code == 201, created.text
    dataset_id = created.json()["data"]["id"]

    with begin_transaction(db):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
        contract = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == dataset.dataset_label_contract_id)
            .one()
        )
        contract.labels_snapshot_json = json.dumps(
            [
                {
                    "label_id": label["id"],
                    "label_key": "车辆",
                    "label_value": "vehicle",
                    "annotation_kind": "bbox",
                    "color": "#ABCDEF",
                }
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )

    base = f"{settings.API_V1_STR}/datasets"
    for keyword in ("车辆", "VEHICLE"):
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20},
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 1

    for keyword in (str(label["id"]), "annotation_kind", "bbox", "ABCDEF"):
        response = client.get(
            base,
            headers=testadmin_token_headers,
            params={"q": keyword, "page": 0, "size": 20},
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 0


def test_dataset_search_falls_back_for_stale_or_cross_dataset_contract_pointer(
    client, db, testadmin_token_headers
):
    old_label = _create_label(
        client,
        testadmin_token_headers,
        label_key="旧车辆",
        label_value="old_vehicle_pointer",
        color="#506070",
    )
    current_label = _create_label(
        client,
        testadmin_token_headers,
        label_key="当前车辆",
        label_value="current_vehicle_pointer",
        color="#607080",
    )
    first = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "指针异常数据集", "label_ids": [old_label["id"]]},
    )
    assert first.status_code == 201, first.text
    dataset_id = first.json()["data"]["id"]

    # Create a second dataset so its contract can be used as a deliberately
    # cross-dataset pointer.  The first dataset receives a newer usable
    # contract, while the original contract is made non-usable.
    second = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "指针来源数据集", "label_ids": [old_label["id"]]},
    )
    assert second.status_code == 201, second.text
    second_id = second.json()["data"]["id"]

    with begin_transaction(db):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
        original = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.id == dataset.dataset_label_contract_id)
            .one()
        )
        original.status = "SUPERSEDED"
        current = DatasetLabelContract(
            dataset_id=dataset_id,
            revision=int(original.revision) + 1,
            annotation_kind="bbox",
            label_ids_json=f'[{current_label["id"]}]',
            labels_snapshot_json=(
                '[{"label_key":"当前车辆","label_value":"current_vehicle_pointer"}]'
            ),
            label_set_signature="d" * 64,
            class_order_signature="d" * 64,
            status="ACTIVE",
            created_by=dataset.created_by,
        )
        db.add(current)
        db.flush()
        source_contract = (
            db.query(DatasetLabelContract)
            .filter(DatasetLabelContract.dataset_id == second_id)
            .one()
        )

        # First verify the stale pointer falls back to this dataset's current
        # contract, rather than matching the superseded snapshot.
        dataset.dataset_label_contract_id = original.id

    base = f"{settings.API_V1_STR}/datasets"
    stale = client.get(
        base,
        headers=testadmin_token_headers,
        params={"q": "current_vehicle_pointer", "page": 0, "size": 20},
    )
    assert stale.status_code == 200, stale.text
    stale_rows = stale.json()["data"]
    assert [row["name"] for row in stale_rows] == ["指针异常数据集"]
    assert stale_rows[0]["dataset_label_contract_id"] == current.id
    assert [item["label_value"] for item in stale_rows[0]["dataset_labels"]] == [
        "current_vehicle_pointer"
    ]

    old = client.get(
        base,
        headers=testadmin_token_headers,
        params={"q": "old_vehicle_pointer", "page": 0, "size": 20},
    )
    assert old.status_code == 200, old.text
    assert all(row["name"] != "指针异常数据集" for row in old.json()["data"])

    with begin_transaction(db):
        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).one()
        dataset.dataset_label_contract_id = source_contract.id

    cross_dataset = client.get(
        base,
        headers=testadmin_token_headers,
        params={"q": "current_vehicle_pointer", "page": 0, "size": 20},
    )
    assert cross_dataset.status_code == 200, cross_dataset.text
    cross_dataset_rows = cross_dataset.json()["data"]
    assert [row["name"] for row in cross_dataset_rows] == ["指针异常数据集"]
    assert cross_dataset_rows[0]["dataset_label_contract_id"] == current.id
    assert [
        item["label_value"] for item in cross_dataset_rows[0]["dataset_labels"]
    ] == ["current_vehicle_pointer"]

    cross_dataset_old = client.get(
        base,
        headers=testadmin_token_headers,
        params={"q": "old_vehicle_pointer", "page": 0, "size": 20},
    )
    assert cross_dataset_old.status_code == 200, cross_dataset_old.text
    assert [row["name"] for row in cross_dataset_old.json()["data"]] == [
        "指针来源数据集"
    ]


def test_governance_search_treats_like_metacharacters_literally(
    client, testadmin_token_headers
):
    """Percent and underscore in a query are text, not implicit wildcards."""
    _create_label(
        client,
        testadmin_token_headers,
        label_key="路况_%",
        label_value="road_literal",
        color="#718293",
    )
    _create_label(
        client,
        testadmin_token_headers,
        label_key="路况_XQ",
        label_value="road_wildcard_control",
        color="#8293A4",
    )
    labels = client.get(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        params={"q": "路况_%", "page": 0, "size": 20, "include_disabled": "true"},
    )
    assert labels.status_code == 200, labels.text
    assert [row["label_key"] for row in labels.json()["data"]] == ["路况_%"]

    for payload in (
        {
            "field_key": "weather_%",
            "name_cn": "字面通配符天气",
            "options": [{"name_cn": "晴", "value_en": "sunny_literal"}],
        },
        {
            "field_key": "weather_XQ",
            "name_cn": "通配符控制天气",
            "options": [{"name_cn": "阴", "value_en": "sunny_wildcard_control"}],
        },
    ):
        created = client.post(
            f"{settings.API_V1_STR}/annotation-taxonomy/global-tag-dimensions",
            headers=testadmin_token_headers,
            json=payload,
        )
        assert created.status_code == 201, created.text
    dimensions = client.get(
        f"{settings.API_V1_STR}/annotation-taxonomy/global-tag-dimensions",
        headers=testadmin_token_headers,
        params={"q": "weather_%", "page": 0, "size": 20},
    )
    assert dimensions.status_code == 200, dimensions.text
    assert [row["field_key"] for row in dimensions.json()["data"]] == ["weather_%"]

    for name in ("数据集_%", "数据集_XQ"):
        created = client.post(
            f"{settings.API_V1_STR}/datasets",
            headers=testadmin_token_headers,
            json={"name": name},
        )
        assert created.status_code == 201, created.text
    datasets = client.get(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        params={"q": "数据集_%", "page": 0, "size": 20},
    )
    assert datasets.status_code == 200, datasets.text
    assert [row["name"] for row in datasets.json()["data"]] == ["数据集_%"]

