from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import json
import zipfile

from labelu.internal.application.service import published_model_catalog as service
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.published_model_catalog import (
    PublishedModelCatalog,
    PublishedModelIdentifierAlias,
)
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel


def _row(model_id: int, **values) -> PublishedModelCatalog:
    defaults = {
        "public_model_id": str(model_id),
        "aicm_environment_id": "env-test",
        "aicm_model_set_id": model_id,
        "name": f"model-{model_id}",
        "display_name": f"model-{model_id}",
        "source_type": "remote_legacy",
        "source_system": "aicm",
        "status": "active",
        "is_public": True,
        "download_enabled": True,
        "availability_status": "available",
    }
    defaults.update(values)
    return PublishedModelCatalog(**defaults)


def test_public_catalog_routes_expose_only_downloadable_models(
    client: TestClient, db: Session
):
    with begin_transaction(db):
        db.add(
            AnnotationLabel(
                label_key="摩托车",
                label_value="vehicle_motorbike",
                normalized_label_value="vehicle_motorbike",
                annotation_kind="bbox",
                color="#52c41a",
                status="active",
                sort_order=10,
            )
        )
        db.add_all(
            [
                _row(701, labels_json=json.dumps(["vehicle_motorbike"])),
                _row(702, status="hidden"),
                _row(703, is_public=False),
                _row(704, availability_status="unavailable"),
                _row(705, download_enabled=False),
            ]
        )

    response = client.get("/api/v1/public/models")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta_data"]["total"] == 1
    assert [item["model_id"] for item in payload["data"]["models"]] == ["701"]
    assert payload["data"]["models"][0]["download_path"] == (
        "/api/v1/public/models/701/download"
    )
    assert payload["data"]["models"][0]["download_enabled"] is True
    assert payload["data"]["models"][0]["class_names"] == ["vehicle_motorbike"]
    assert payload["data"]["models"][0]["class_labels"] == [
        {
            "class_index": 0,
            "label_key": "摩托车",
            "label_value": "vehicle_motorbike",
            "color": "#52c41a",
            "status": "active",
            "annotation_kind": "bbox",
            "matched": True,
        }
    ]

    detail = client.get("/api/v1/public/models/701")
    assert detail.status_code == 200
    assert detail.json()["data"]["aicm_model_set_id"] == 701
    assert client.get("/api/v1/public/models/702").status_code == 404


def test_management_catalog_list_requires_authentication(
    client: TestClient, testuser_token_headers: dict, db: Session
):
    with begin_transaction(db):
        db.add(_row(710))

    assert client.get("/api/v1/published-models").status_code in {401, 403}
    response = client.get("/api/v1/published-models", headers=testuser_token_headers)
    assert response.status_code == 200, response.text
    assert response.json()["meta_data"]["total"] == 1


def test_admin_can_toggle_model_download(
    client: TestClient,
    testadmin_token_headers: dict,
    db: Session,
):
    with begin_transaction(db):
        row = _row(711)
        db.add(row)
        db.flush()
        catalog_id = int(row.id)

    response = client.patch(
        f"/api/v1/published-models/{catalog_id}",
        headers=testadmin_token_headers,
        json={"download_enabled": False, "expected_download_enabled": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["download_enabled"] is False
    assert client.get("/api/v1/public/models/711").status_code == 404

    # The switch is a platform download gate. It must close both the
    # authenticated management download and the anonymous public download;
    # neither path may fall through to an AICM fetch after the operator closes it.
    management_download = client.get(
        "/api/v1/published-models/711/download",
        headers=testadmin_token_headers,
    )
    assert management_download.status_code == 409
    assert "下载开关已由管理员关闭" in management_download.json()["msg"]
    public_download = client.get("/api/v1/public/models/711/download")
    assert public_download.status_code == 404

    stale = client.patch(
        f"/api/v1/published-models/{catalog_id}",
        headers=testadmin_token_headers,
        json={"download_enabled": True, "expected_download_enabled": True},
    )
    assert stale.status_code == 409
    assert "刷新后重试" in stale.json()["msg"]

    missing_expected = client.patch(
        f"/api/v1/published-models/{catalog_id}",
        headers=testadmin_token_headers,
        json={"download_enabled": True},
    )
    assert missing_expected.status_code == 409
    assert "必须携带当前值" in missing_expected.json()["msg"]

    withdrawal = client.patch(
        f"/api/v1/published-models/{catalog_id}",
        headers=testadmin_token_headers,
        json={"status": "revoked"},
    )
    assert withdrawal.status_code == 400
    assert "不支持撤回" in withdrawal.json()["msg"]


def test_public_download_url_keeps_aicm_id_and_third_party_alias_alignment(
    client: TestClient,
    db: Session,
    monkeypatch,
    tmp_path,
    testuser_token_headers: dict,
):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    package = tmp_path / "published_model_packages" / "712" / "model.zip"
    package.parent.mkdir(parents=True)
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("model.bmodel", b"model-712")
    with begin_transaction(db):
        row = _row(
            712,
            availability_status="unavailable",
            package_rel_path="published_model_packages/712/model.zip",
            package_size_bytes=package.stat().st_size,
        )
        db.add(row)
        db.flush()
        db.add(
            PublishedModelIdentifierAlias(
                catalog_id=row.id,
                source_system="专家回流",
                external_model_id="回流模型-712",
                alias_type="third_party",
            )
        )

    response = client.get(
        "/api/v1/published-models/专家回流:回流模型-712/download-url",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["model_id"] == "712"
    assert payload["aicm_model_set_id"] == 712
    assert payload["download_url"] == "/api/v1/published-models/712/download"


def test_public_download_serves_labelu_package_when_aicm_is_unavailable(
    client: TestClient,
    db: Session,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    package = tmp_path / "published_model_packages" / "570" / "model.zip"
    package.parent.mkdir(parents=True)
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("model.bin", b"model-570")
    expected = package.read_bytes()
    with begin_transaction(db):
        db.add(
            _row(
                570,
                availability_status="unavailable",
                package_rel_path="published_model_packages/570/model.zip",
                package_size_bytes=len(expected),
            )
        )
    monkeypatch.setattr(
        service.yolo_aicm_publish,
        "_aicm_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local model download must not query AICM")
        ),
    )

    response = client.get("/api/v1/public/models/570/download")

    assert response.status_code == 200, response.text
    assert response.content == expected
    assert response.headers["content-type"] == "application/zip"
    assert "model-570.zip" in response.headers["content-disposition"]
