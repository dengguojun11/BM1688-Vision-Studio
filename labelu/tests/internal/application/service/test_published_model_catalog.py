import json
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from labelu.internal.application.command.published_model_catalog import (
    CreatePublishedModelAliasCommand,
    UpdatePublishedModelCommand,
)
from labelu.internal.application.service import published_model_catalog as service
from labelu.internal.application.service import strategy_evolution
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.published_model_catalog import (
    PublishedModelCatalog,
    PublishedModelIdentifierAlias,
)
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel


def _catalog(
    model_id: str,
    *,
    environment_id: str = "env-test",
    aicm_model_set_id: int | None = None,
    export_id: int | None = None,
    **values,
) -> PublishedModelCatalog:
    defaults = {
        "public_model_id": model_id,
        "aicm_environment_id": environment_id,
        "aicm_model_set_id": (
            int(model_id)
            if aicm_model_set_id is None and model_id.isdigit()
            else aicm_model_set_id
        ),
        "export_id": export_id,
        "name": f"model-{model_id}",
        "display_name": f"model-{model_id}",
        "source_type": "labelu_publish",
        "source_system": "aicm",
        "status": "active",
        "is_public": True,
        "download_enabled": True,
        "availability_status": "available",
    }
    defaults.update(values)
    return PublishedModelCatalog(**defaults)


def _write_zip(path: Path, content: bytes = b"labelu-model") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("model.bin", content)
    return path


def _zip_bytes(content: bytes = b"cached-model") -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("model.bin", content)
    return stream.getvalue()


def test_catalog_resolves_ordered_chinese_english_label_contract(db):
    with begin_transaction(db):
        db.add(
            AnnotationLabel(
                label_key="摩托车",
                label_value="vehicle_motorbike",
                normalized_label_value="vehicle_motorbike",
                annotation_kind="bbox",
                color="#1677ff",
                status="active",
                sort_order=10,
            )
        )
        db.add(
            _catalog(
                "570",
                labels_json=json.dumps(["vehicle_motorbike", "third_party_unknown"]),
            )
        )

    payload, total = service.list_models(db)

    assert total == 1
    assert payload.models[0].class_names == [
        "vehicle_motorbike",
        "third_party_unknown",
    ]
    assert [item.class_index for item in payload.models[0].class_labels] == [0, 1]
    assert payload.models[0].class_labels[0].model_dump() == {
        "class_index": 0,
        "label_key": "摩托车",
        "label_value": "vehicle_motorbike",
        "color": "#1677ff",
        "status": "active",
        "annotation_kind": "bbox",
        "matched": True,
    }
    assert payload.models[0].class_labels[1].model_dump() == {
        "class_index": 1,
        "label_key": None,
        "label_value": "third_party_unknown",
        "color": None,
        "status": None,
        "annotation_kind": None,
        "matched": False,
    }
    assert service.get_model(db, "570").class_labels[0].label_key == "摩托车"


def test_catalog_backfills_precision_from_existing_publish_history(db, monkeypatch):
    with begin_transaction(db):
        db.add(_catalog("572", publish_job_id="publish-572", precision=None))
    monkeypatch.setattr(
        service,
        "load_job",
        lambda _job_id: {"result": {"precision": "fp16"}},
    )

    assert service.get_model(db, "572").precision == "fp16"


def test_resolve_keeps_numeric_aicm_id_and_supports_exact_namespaced_alias(db):
    with begin_transaction(db):
        first = _catalog("560", export_id=44)
        second = _catalog("561", export_id=45)
        db.add_all([first, second])
        db.flush()
        db.add_all(
            [
                PublishedModelIdentifierAlias(
                    catalog_id=first.id,
                    source_system="vendor-a",
                    external_model_id="model-x",
                    alias_type="third_party",
                ),
                PublishedModelIdentifierAlias(
                    catalog_id=second.id,
                    source_system="vendor-b",
                    external_model_id="model-x",
                    alias_type="third_party",
                ),
            ]
        )

    assert service.resolve_model(db, "560").id == first.id
    assert service.resolve_model(db, "vendor-a:model-x").id == first.id
    assert (
        service.resolve_model(db, "model-x", source_system="vendor-b").id == second.id
    )
    with pytest.raises(LabelUException) as exc_info:
        service.resolve_model(db, "model-x")
    assert exc_info.value.status_code == 409

    export_id, detail = strategy_evolution._resolve_export_id_from_model_id(
        db, "vendor-a:model-x"
    )
    assert export_id == 44
    assert detail["resolved_by"] == "published_model_catalog"
    assert strategy_evolution._model_id_candidates("vendor-model-560") == (
        ["vendor-model-560"],
        [],
    )


def test_register_publish_keeps_aicm_public_id_and_existing_management_fields(
    db, monkeypatch, tmp_path
):
    existing = _catalog(
        "569",
        display_name="运营侧名称",
        status="hidden",
        is_public=False,
        download_enabled=False,
        source_type="remote_legacy",
    )
    with begin_transaction(db):
        db.add(existing)

    export = SimpleNamespace(id=88, yolo_train_job_id=99, task_id=100, imgsz=640)
    train_job = SimpleNamespace(id=99)
    task = SimpleNamespace(id=100, name="电动车加装的雨伞")
    monkeypatch.setattr(service.crud_yolo_bm1688_export, "get", lambda *_: export)
    monkeypatch.setattr(service.crud_yolo_train_job, "get", lambda *_: train_job)
    monkeypatch.setattr(service.crud_task, "get", lambda *_: task)
    monkeypatch.setattr(
        service.yolo_aicm_publish,
        "_class_names_for_publish",
        lambda _: ["非机动车_雨伞电动车"],
    )
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    package = _write_zip(tmp_path / "publish-work" / "model-569.zip")

    with begin_transaction(db):
        row = service.register_published_model(
            db,
            export_id=88,
            model_set_id=569,
            environment_id="env-test",
            precision="int8",
            publish_job_id="publish-569",
            upload_name="电动车加装的雨伞_bm1688_v2",
            publisher_username="admin@123.com",
            package_path=str(package),
            package_required=True,
        )

    db.refresh(row)
    assert row.public_model_id == "569"
    assert row.aicm_model_set_id == 569
    assert row.precision == "int8"
    assert row.source_type == "labelu_publish"
    assert row.display_name == "运营侧名称"
    assert row.status == "hidden"
    assert row.is_public is False
    assert row.download_enabled is False
    assert row.package_rel_path == "published_model_packages/569/model.zip"
    assert (tmp_path / row.package_rel_path).is_file()
    assert row.package_size_bytes == (tmp_path / row.package_rel_path).stat().st_size
    assert len(row.package_sha256) == 64
    assert json.loads(row.labels_json) == ["非机动车_雨伞电动车"]
    assert service.resolve_model(db, "aicm:569").id == row.id


def test_register_publish_rejects_cross_environment_aicm_id_rebinding(
    db, monkeypatch, tmp_path
):
    existing = _catalog("569", environment_id="production-aicm")
    with begin_transaction(db):
        db.add(existing)

    export = SimpleNamespace(id=88, yolo_train_job_id=99, task_id=100, imgsz=640)
    monkeypatch.setattr(service.crud_yolo_bm1688_export, "get", lambda *_: export)
    monkeypatch.setattr(
        service.crud_yolo_train_job, "get", lambda *_: SimpleNamespace(id=99)
    )
    monkeypatch.setattr(
        service.crud_task,
        "get",
        lambda *_: SimpleNamespace(id=100, name="安全帽检测"),
    )
    monkeypatch.setattr(
        service.yolo_aicm_publish,
        "_class_names_for_publish",
        lambda _: ["helmet"],
    )
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    package = _write_zip(tmp_path / "publish-work" / "model-569.zip")

    with pytest.raises(LabelUException) as exc_info:
        with begin_transaction(db):
            service.register_published_model(
                db,
                export_id=88,
                model_set_id=569,
                environment_id="test-aicm",
                package_path=str(package),
                package_required=True,
            )

    assert exc_info.value.status_code == 409
    assert "不能自动改绑" in exc_info.value.detail
    db.refresh(existing)
    assert existing.aicm_environment_id == "production-aicm"
    assert not (tmp_path / "published_model_packages" / "569" / "model.zip").exists()


def test_register_publish_links_canonical_model_version_to_platform_catalog(
    db, monkeypatch, tmp_path
):
    export = SimpleNamespace(id=89, yolo_train_job_id=109, task_id=110, imgsz=640)
    train_job = SimpleNamespace(id=109)
    task = SimpleNamespace(id=110, name="安全帽检测")
    model_version = SimpleNamespace(
        id=901,
        published_catalog_id=None,
        status="VALIDATED",
        published_at=None,
        published_by=None,
    )
    monkeypatch.setattr(service.crud_yolo_bm1688_export, "get", lambda *_: export)
    monkeypatch.setattr(service.crud_yolo_train_job, "get", lambda *_: train_job)
    monkeypatch.setattr(service.crud_task, "get", lambda *_: task)
    monkeypatch.setattr(
        service.crud_model_version,
        "get_by_training_job",
        lambda *_: model_version,
    )
    monkeypatch.setattr(
        service.yolo_aicm_publish,
        "_class_names_for_publish",
        lambda _: ["helmet"],
    )
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    package = _write_zip(tmp_path / "publish-work" / "model-570.zip")

    with begin_transaction(db):
        row = service.register_published_model(
            db,
            export_id=89,
            model_set_id=570,
            environment_id="env-test",
            precision="int8",
            publish_job_id="publish-570",
            publisher_username="annotator@example.com",
            published_by_id=7,
            package_path=str(package),
            package_required=True,
        )

    assert row.id is not None
    assert model_version.published_catalog_id == row.id
    assert model_version.status == "PUBLISHED"
    assert model_version.published_by == 7
    assert model_version.published_at is not None


def test_persist_local_package_never_overwrites_retained_model(tmp_path, monkeypatch):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    retained = _write_zip(
        tmp_path / "published_model_packages" / "570" / "model.zip",
        b"retained-model",
    )
    replacement = _write_zip(tmp_path / "publish-work" / "model-570.zip", b"new-model")
    retained_bytes = retained.read_bytes()

    with pytest.raises(LabelUException) as exc_info:
        service._persist_local_package("570", replacement)

    assert exc_info.value.status_code == 409
    assert "不会自动覆盖" in str(exc_info.value.msg or exc_info.value)
    assert retained.read_bytes() == retained_bytes


def test_persist_local_package_does_not_overwrite_concurrent_winner(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    source = _write_zip(tmp_path / "publish-work" / "model-571.zip", b"new-model")
    target = tmp_path / "published_model_packages" / "571" / "model.zip"
    winner_bytes: bytes = b""

    def install_winner_then_conflict(_temporary, destination):
        nonlocal winner_bytes
        winner = _write_zip(Path(destination), b"concurrent-winner")
        winner_bytes = winner.read_bytes()
        raise FileExistsError(str(destination))

    monkeypatch.setattr(service.os, "link", install_winner_then_conflict)

    with pytest.raises(LabelUException) as exc_info:
        service._persist_local_package("571", source)

    assert exc_info.value.status_code == 409
    assert "另一进程写入不同内容" in exc_info.value.detail
    assert target.read_bytes() == winner_bytes


def test_sync_preserves_management_and_marks_missing_remote_models_unavailable(
    db, monkeypatch
):
    with begin_transaction(db):
        db.add_all(
            [
                _catalog(
                    "560",
                    display_name="保留展示名",
                    status="hidden",
                    is_public=False,
                    download_enabled=False,
                ),
                _catalog("559"),
            ]
        )

    monkeypatch.setattr(service, "list_jobs", lambda **_: ([], 0))
    monkeypatch.setattr(
        service,
        "_fetch_all_remote_models",
        lambda _env: [
            {
                "id": 560,
                "name": "远端新名称",
                "chip_type": "SOPHGO,BM1688,NPU",
                "algorithm_type": "Detection",
                "resolution": "640x640",
                "mAP": 0.91,
                "created_at": 1787206230,
                "label_class_set_obj_info": [{"name": "umbrella"}],
            },
            {
                "id": 561,
                "name": "新增历史模型",
                "chip_type": "SOPHGO,BM1688,NPU",
                "algorithm_type": "Detection",
                "resolution": "640x640",
                "mAP": 0.8,
                "created_at": 1787206231,
                "label_class_set_obj_info": [{"name": "streetVendor"}],
            },
        ],
    )

    result = service.sync_models(db, environment_id="env-test")

    kept = service.resolve_model(db, "560")
    missing = service.resolve_model(db, "559")
    created = service.resolve_model(db, "561")
    assert kept.display_name == "保留展示名"
    assert kept.status == "hidden"
    assert kept.is_public is False
    assert kept.download_enabled is False
    assert kept.map50 == "0.91"
    assert missing.availability_status == "unavailable"
    assert created.source_type == "remote_legacy"
    assert result.remote_scanned == 2
    assert result.created == 1


def test_sync_rejects_same_aicm_id_from_another_environment(db, monkeypatch):
    with begin_transaction(db):
        existing = _catalog("560", environment_id="production-aicm")
        db.add(existing)

    monkeypatch.setattr(service, "list_jobs", lambda **_: ([], 0))
    monkeypatch.setattr(
        service,
        "_fetch_all_remote_models",
        lambda _env: [{"id": 560, "name": "测试环境同号模型"}],
    )

    with pytest.raises(LabelUException) as exc_info:
        service.sync_models(db, environment_id="test-aicm")

    assert exc_info.value.status_code == 409
    assert "production-aicm" in exc_info.value.detail
    db.refresh(existing)
    assert existing.aicm_environment_id == "production-aicm"
    assert existing.name == "model-560"


def test_sync_backfills_model_570_package_from_publish_history(
    db, monkeypatch, tmp_path
):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    history_package = _write_zip(tmp_path / "aicm_publish_work" / "model-570.zip")
    history = {
        "job_id": "2238ed88199d4461",
        "export_id": 20,
        "environment_id": "env-test",
        "publisher_username": "admin@123.com",
        "task_name": "电动车违停",
        "created_at": "2026-08-21T10:31:02",
        "result": {
            "ok": True,
            "model_set_id": 570,
            "upload_name": "非机动车违停_bm1688_v2",
            "package_path": str(history_package),
        },
    }
    monkeypatch.setattr(service, "list_jobs", lambda **_: ([history], 1))

    result = service.sync_models(
        db, environment_id="env-test", include_remote_models=False
    )

    row = service.resolve_model(db, "570")
    stored = tmp_path / "published_model_packages" / "570" / "model.zip"
    assert result.created == 1
    assert row.publish_job_id == "2238ed88199d4461"
    assert row.package_rel_path == "published_model_packages/570/model.zip"
    assert stored.is_file()
    assert zipfile.is_zipfile(stored)


def test_fetch_all_remote_models_uses_unfiltered_aicm_list(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        text = ""

        def __init__(self, rows):
            self.rows = rows

        def json(self):
            return {"data": {"list": self.rows}}

    class Session:
        closed = False

        def get(self, _url, *, params, **_kwargs):
            calls.append(dict(params))
            return Response(
                [{"id": i} for i in range(1, 101)]
                if params["_page"] == 1
                else [{"id": 101}]
            )

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(
        service,
        "load_aicm_publish_config",
        lambda **_: {
            "base_url": "http://aicm.local:8102",
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
        },
    )
    monkeypatch.setattr(service, "config_is_complete", lambda _: True)
    monkeypatch.setattr(service.yolo_aicm_publish, "_aicm_session", lambda _: session)

    rows = service._fetch_all_remote_models("env-test")

    assert len(rows) == 101
    assert [call["_page"] for call in calls] == [1, 2]
    assert all(call["_filter_in_use"] == "" for call in calls)
    assert all(call["_filter_engine_id"] == "" for call in calls)
    assert session.closed is True


def test_download_uses_labelu_local_package_without_aicm(db, monkeypatch, tmp_path):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    package = _write_zip(tmp_path / "published_model_packages" / "570" / "model.zip")
    with begin_transaction(db):
        row = _catalog(
            "570",
            availability_status="unavailable",
            package_rel_path="published_model_packages/570/model.zip",
            package_size_bytes=package.stat().st_size,
        )
        db.add(row)
        db.flush()
        db.add(
            PublishedModelIdentifierAlias(
                catalog_id=row.id,
                source_system="vendor-a",
                external_model_id="third-party-570",
                alias_type="third_party",
            )
        )

    monkeypatch.setattr(
        service.yolo_aicm_publish,
        "_aicm_session",
        lambda *_args, **_kwargs: pytest.fail("local package must not query AICM"),
    )

    resolved = service.resolve_download_file(db, "570")
    compatibility = service.resolve_download(db, "570")

    assert resolved.aicm_model_set_id == 570
    assert resolved.file_path == package
    assert (
        service.resolve_download_file(db, "vendor-a:third-party-570").file_path
        == package
    )
    assert compatibility.download_url == "/api/v1/published-models/570/download"
    assert service.get_model(db, "570").local_package_available is True


def test_download_caches_remote_package_once_when_labelu_has_no_package(
    db, monkeypatch, tmp_path
):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    with begin_transaction(db):
        db.add(_catalog("570", availability_status="unavailable"))

    payload = _zip_bytes()

    class Response:
        status_code = 200
        text = ""

        def iter_content(self, chunk_size):
            assert chunk_size == 1024 * 1024
            yield payload

        def close(self):
            return None

    class Session:
        closed = False

        def get(self, url, **_kwargs):
            assert url == "http://example/model-570.zip"
            return Response()

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(
        service,
        "load_aicm_publish_config",
        lambda **_: {
            "base_url": "http://192.168.54.169:8102",
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
        },
    )
    monkeypatch.setattr(service, "config_is_complete", lambda _: True)
    monkeypatch.setattr(
        service.yolo_aicm_publish,
        "_aicm_session",
        lambda *_args, **_kwargs: session,
    )
    monkeypatch.setattr(
        service.yolo_aicm_publish,
        "_get_download_url",
        lambda *_args, **_kwargs: "http://example/model-570.zip",
    )

    resolved = service.resolve_download_file(db, "570")

    assert resolved.file_path.read_bytes() == payload
    row = service.resolve_model(db, "570")
    assert row.availability_status == "available"
    assert row.package_rel_path == "published_model_packages/570/model.zip"
    assert row.remote_download_url == "http://example/model-570.zip"
    assert session.closed is True


def test_download_adopts_retained_package_without_querying_aicm(
    db, monkeypatch, tmp_path
):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    retained = _write_zip(tmp_path / "published_model_packages" / "570" / "model.zip")
    with begin_transaction(db):
        db.add(_catalog("570", availability_status="unavailable"))

    monkeypatch.setattr(
        service,
        "load_aicm_publish_config",
        lambda **_: pytest.fail("retained package must be adopted before AICM lookup"),
    )

    resolved = service.resolve_download_file(db, "570")
    row = service.resolve_model(db, "570")

    assert resolved.file_path == retained
    assert row.package_rel_path == "published_model_packages/570/model.zip"
    assert row.package_sha256 == service._file_hash(retained, "sha256")


def test_download_rejects_manually_disabled_model_without_remote_lookup(
    db, monkeypatch
):
    with begin_transaction(db):
        db.add(_catalog("571", download_enabled=False))

    monkeypatch.setattr(
        service.yolo_aicm_publish,
        "_aicm_session",
        lambda *_args, **_kwargs: pytest.fail("disabled model must not query AICM"),
    )

    with pytest.raises(LabelUException) as exc_info:
        service.resolve_download(db, "571")

    assert exc_info.value.status_code == 409
    assert "模型 571 的下载开关已由管理员关闭" in exc_info.value.detail


def test_download_rejects_replaced_package_when_registered_sha256_differs(
    db, monkeypatch, tmp_path
):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    package = _write_zip(tmp_path / "published_model_packages" / "572" / "model.zip")
    with begin_transaction(db):
        db.add(
            _catalog(
                "572",
                package_rel_path="published_model_packages/572/model.zip",
                package_size_bytes=package.stat().st_size,
                package_sha256="0" * 64,
            )
        )

    with pytest.raises(LabelUException) as exc_info:
        service.resolve_download_file(db, "572")

    assert exc_info.value.status_code == 409
    assert "摘要与登记值不一致" in exc_info.value.detail


def test_download_rejects_package_path_outside_managed_root(db, monkeypatch, tmp_path):
    monkeypatch.setattr(service.settings, "BASE_DATA_DIR", str(tmp_path))
    outside = _write_zip(tmp_path.parent / "outside-model.zip")
    with begin_transaction(db):
        db.add(_catalog("573", package_rel_path=str(outside)))

    with pytest.raises(LabelUException) as exc_info:
        service.resolve_download_file(db, "573")

    assert exc_info.value.status_code == 409
    assert "本地模型包路径无效" in exc_info.value.detail


def test_admin_can_close_download_switch_without_hiding_catalog_record(db):
    with begin_transaction(db):
        row = _catalog("572")
        db.add(row)

    updated = service.update_model(
        db,
        SimpleNamespace(id=1),
        int(row.id),
        UpdatePublishedModelCommand(
            download_enabled=False,
            expected_download_enabled=True,
        ),
    )

    assert updated.download_enabled is False
    assert service.get_model(db, "572").download_enabled is False
    with pytest.raises(LabelUException) as exc_info:
        service.get_model(db, "572", public_only=True)
    assert exc_info.value.status_code == 404


def test_download_switch_requires_expected_value_for_concurrent_safety(db):
    with begin_transaction(db):
        row = _catalog("574")
        db.add(row)

    with pytest.raises(LabelUException) as exc_info:
        service.update_model(
            db,
            SimpleNamespace(id=1),
            int(row.id),
            UpdatePublishedModelCommand(download_enabled=False),
        )

    assert exc_info.value.status_code == 409
    assert "必须携带当前值" in exc_info.value.detail
    assert service.get_model(db, "574").download_enabled is True


def test_model_catalog_rejects_withdrawal_style_controls(db):
    with begin_transaction(db):
        row = _catalog("575")
        db.add(row)

    with pytest.raises(LabelUException) as exc_info:
        service.update_model(
            db,
            SimpleNamespace(id=1),
            int(row.id),
            UpdatePublishedModelCommand(status="revoked"),
        )

    assert exc_info.value.status_code == 400
    assert "不支持撤回" in exc_info.value.detail
    db.refresh(row)
    assert row.status == "active"


def test_public_list_excludes_hidden_private_and_unavailable_models(db):
    with begin_transaction(db):
        db.add_all(
            [
                _catalog("601"),
                _catalog("602", status="hidden"),
                _catalog("603", is_public=False),
                _catalog("604", availability_status="unavailable"),
                _catalog("605", download_enabled=False),
            ]
        )

    payload, total = service.list_models(db, public_only=True)
    management_payload, _ = service.list_models(db)

    assert total == 1
    assert [row.model_id for row in payload.models] == ["601"]
    assert management_payload.summary.public == 1


def test_admin_alias_api_rejects_reserved_aicm_source(db):
    with begin_transaction(db):
        row = _catalog("620")
        db.add(row)
    current_user = SimpleNamespace(id=1)

    with pytest.raises(LabelUException) as exc_info:
        service.add_alias(
            db,
            current_user,
            row.id,
            CreatePublishedModelAliasCommand(
                source_system="aicm",
                external_model_id="custom-620",
            ),
        )
    assert exc_info.value.status_code == 400
