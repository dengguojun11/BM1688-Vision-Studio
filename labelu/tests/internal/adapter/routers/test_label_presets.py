"""拉框预设 CRUD 校验与物理删除。"""

from labelu.internal.common.config import settings


def test_create_label_preset_success(client, testadmin_token_headers):
    r = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "illegalParking",
            "name": "违停检测",
            "description": None,
            "sort_order": 0,
            "items": [
                {
                    "label_key": "违停",
                    "label_value": "illegalParking",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["data"]["slug"] == "illegalParking"
    assert body["data"]["items"][0]["label_key"] == "违停"


def test_create_label_preset_rejects_whitespace_name(client, testadmin_token_headers):
    r = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "badNamePreset",
            "name": "   ",
            "items": [
                {
                    "label_key": "标签",
                    "label_value": "tag",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 422, r.text


def test_create_label_preset_rejects_empty_label_key(client, testadmin_token_headers):
    r = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "emptyKeyPreset",
            "name": "测试",
            "items": [
                {
                    "label_key": "   ",
                    "label_value": "tests",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert r.status_code == 422, r.text


def test_duplicate_slug_case_insensitive_shows_existing(client, testadmin_token_headers):
    first = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "illegalParking",
            "name": "机动车违停",
            "items": [
                {
                    "label_key": "违停",
                    "label_value": "illegal_parking",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "IllegalParking",
            "name": "另一个名字",
            "items": [
                {
                    "label_key": "违停2",
                    "label_value": "illegal_parking2",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert second.status_code == 400, second.text
    msg = second.json().get("msg") or ""
    assert "illegalParking" in msg
    assert "id=" in msg


def test_hard_delete_allows_recreate_same_slug(client, testadmin_token_headers):
    create = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "reviveMe",
            "name": "旧名",
            "items": [
                {
                    "label_key": "A",
                    "label_value": "a",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert create.status_code == 201, create.text
    pid = create.json()["data"]["id"]
    deleted = client.delete(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets/{pid}",
        headers=testadmin_token_headers,
    )
    assert deleted.status_code == 200, deleted.text
    recreated = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "reviveMe",
            "name": "新名",
            "items": [
                {
                    "label_key": "B",
                    "label_value": "b",
                    "color": "#ff0000",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert recreated.status_code == 201, recreated.text
    data = recreated.json()["data"]
    assert data["name"] == "新名"
    assert data["items"][0]["label_key"] == "B"
    listed = client.get(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets?page=0&size=500",
        headers=testadmin_token_headers,
    )
    slugs = [p["slug"] for p in listed.json()["data"]]
    assert slugs.count("reviveMe") == 1


def test_hard_delete_slug_case_variant_recreate(client, testadmin_token_headers):
    create = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "TmpParking",
            "name": "临时",
            "items": [
                {
                    "label_key": "A",
                    "label_value": "a",
                    "color": "#1677ff",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert create.status_code == 201, create.text
    pid = create.json()["data"]["id"]
    assert create.json()["data"]["slug"] == "tmpParking"
    client.delete(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets/{pid}",
        headers=testadmin_token_headers,
    )
    recreated = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
        headers=testadmin_token_headers,
        json={
            "slug": "TmpParking",
            "name": "恢复后",
            "items": [
                {
                    "label_key": "B",
                    "label_value": "b",
                    "color": "#ff0000",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["data"]["name"] == "恢复后"
