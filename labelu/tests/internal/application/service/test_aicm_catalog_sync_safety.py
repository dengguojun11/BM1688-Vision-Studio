import json
from datetime import datetime

import pytest

from labelu.internal.application.service import published_model_catalog as service
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.published_model_catalog import PublishedModelCatalog


def _row(identifier, labels, **kwargs):
    return PublishedModelCatalog(
        public_model_id=str(identifier),
        aicm_model_set_id=identifier,
        aicm_environment_id="env-test",
        name=f"历史模型-{identifier}",
        source_type="remote_legacy",
        source_system="aicm",
        status="active",
        is_public=True,
        download_enabled=True,
        availability_status="available",
        labels_json=json.dumps(labels),
        **kwargs,
    )


def _remote_client(monkeypatch, pages):
    class Response:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Session:
        calls = []
        closed = False

        def get(self, _url, *, params, **_kwargs):
            self.calls.append(params["_page"])
            return Response(pages[params["_page"] - 1])

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(
        service,
        "load_aicm_publish_config",
        lambda **_: {"base_url": "http://aicm.test"},
    )
    monkeypatch.setattr(service, "config_is_complete", lambda _: True)
    monkeypatch.setattr(service.yolo_aicm_publish, "_aicm_session", lambda _: session)
    return session


def test_fetch_honors_server_pagination_even_when_page_is_short(monkeypatch):
    session = _remote_client(
        monkeypatch,
        [
            {
                "data": {"list": [{"id": 1}], "total": 2, "has_next": True},
                "error_code": 0,
            },
            {
                "data": {"list": [{"id": 2}], "total": 2, "has_next": False},
                "error_code": 0,
            },
        ],
    )
    assert [row["id"] for row in service._fetch_all_remote_models("env-test")] == [1, 2]
    assert session.calls == [1, 2]
    assert session.closed


@pytest.mark.parametrize(
    "pages",
    [
        [{"error_code": 1, "data": {"list": []}}],
        [{"data": {"not_a_list": []}}],
        [{"data": {"list": [{"id": 1}], "total": 2, "has_next": False}}],
        [
            {"data": {"list": [{"id": 1}], "total": 2, "has_next": True}},
            {"data": {"list": [{"id": 1}], "total": 2, "has_next": False}},
        ],
        [
            {"data": {"list": [{"id": 1}], "total": 2, "has_next": True}},
            {"data": {"list": [{"id": 2}], "total": 3, "has_next": False}},
        ],
    ],
)
def test_fetch_rejects_failed_or_incomplete_snapshot(monkeypatch, pages):
    session = _remote_client(monkeypatch, pages)
    with pytest.raises(LabelUException) as error:
        service._fetch_all_remote_models("env-test")
    assert error.value.status_code == 502
    assert session.closed


def test_sync_uses_english_keys_and_is_idempotent(db, monkeypatch):
    remote = {
        "id": 536,
        "name": "三轮车检测",
        "created_at": 1790000000,
        "label_class_set_obj_info": [{"key": "nonvehicle_tricycle", "name": "三轮车"}],
    }
    monkeypatch.setattr(service, "list_jobs", lambda **_: ([], 0))
    monkeypatch.setattr(service, "_fetch_all_remote_models", lambda _: [remote])
    first = service.sync_models(db, environment_id="env-test")
    second = service.sync_models(db, environment_id="env-test")
    row = service.resolve_model(db, "536")
    assert (first.created, second.created) == (1, 0)
    assert json.loads(row.labels_json) == ["nonvehicle_tricycle"]
    assert row.export_id is None
    assert db.query(PublishedModelCatalog).count() == 1
    assert len(row.aliases) == 1


def test_filtered_list_and_versions_stay_stable_across_pages(db):
    with begin_transaction(db):
        db.add_all(
            [
                _row(10, ["person", "car"], published_at=datetime(2026, 1, 1)),
                _row(20, ["car", "person"], published_at=datetime(2026, 2, 1)),
                _row(30, ["car"], published_at=datetime(2026, 3, 1)),
            ]
        )
    payload, total = service.list_models(db, q="20", page=0, size=1)
    assert total == 1
    assert payload.models[0].model_id == "20"
    assert payload.models[0].label_group_version == "v2"
    assert payload.models[0].label_group_size == 2
    assert payload.models[0].related_model_count == 2
    assert service.get_model(db, "10").label_group_version == "v1"
    payload, total = service.list_models(db, yolo_train_job_id=999)
    assert total == 0


def test_summary_does_not_count_remote_downloads_as_unavailable(db):
    with begin_transaction(db):
        db.add(_row(536, ["car"]))
        missing = _row(537, ["car"])
        missing.availability_status = "unavailable"
        db.add(missing)
    payload, total = service.list_models(db)
    assert total == 2
    assert payload.summary.unavailable == 1


def test_failed_snapshot_cannot_mark_existing_model_missing(db, monkeypatch):
    with begin_transaction(db):
        db.add(_row(603, ["car"]))
    monkeypatch.setattr(service, "list_jobs", lambda **_: ([], 0))
    _remote_client(
        monkeypatch, [{"data": {"list": [], "total": 20, "has_next": False}}]
    )
    with pytest.raises(LabelUException):
        service.sync_models(db, environment_id="env-test")
    assert service.resolve_model(db, "603").availability_status == "available"


def test_duplicate_sync_is_rejected_without_second_remote_request(db, monkeypatch):
    monkeypatch.setattr(
        service,
        "_fetch_all_remote_models",
        lambda _: pytest.fail("must not query twice"),
    )
    assert service._catalog_sync_lock.acquire(blocking=False)
    try:
        with pytest.raises(LabelUException) as error:
            service.sync_models(db, environment_id="env-test")
        assert error.value.status_code == 409
    finally:
        service._catalog_sync_lock.release()
