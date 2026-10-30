"""AICM 发布流水线辅助函数。"""

import json
import hashlib
import io
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from labelu.internal.adapter.persistence import crud_user, crud_yolo_bm1688_export
from labelu.internal.application.response.yolo_aicm_publish import (
    AicmPublishResultResponse,
)
from labelu.internal.application.service import yolo_aicm_publish as svc
from labelu.internal.application.service import (
    published_model_catalog as catalog_service,
)
from labelu.internal.common import db as db_module
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.label_contract import class_order_signature
from labelu.internal.domain.models.annotation_taxonomy import RuntimePublishTemplate


def test_environment_response_distinguishes_encrypted_password_from_decrypt_failure(
    monkeypatch,
):
    monkeypatch.setattr(
        svc,
        "_env_record_to_cfg",
        lambda _record: {
            "base_url": "http://aicm.local:8102",
            "username": "admin",
            "password": "",
            "chip_type": "SOPHGO,BM1688,NPU",
            "algorithm_type": "Detection",
            "verify_ssl": False,
        },
    )

    response = svc._environment_to_response(
        {
            "id": "env_test",
            "name": "测试环境",
            "password_encrypted": "encrypted-value",
            "password_is_configured": True,
            "password_masked_hint": "********word",
        },
        active_id="env_test",
    )

    assert response.password_configured is True
    assert response.password_runtime_available is False
    assert response.ready_to_publish is False
    assert response.missing_fields == ["password"]
    assert "无法解密" in response.readiness_reason
    assert "PASSWORD_SECRET_KEY" in response.readiness_reason


def test_environment_response_reports_runtime_ready(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_env_record_to_cfg",
        lambda _record: {
            "base_url": "http://aicm.local:8102",
            "username": "admin",
            "password": "secret",
            "chip_type": "SOPHGO,BM1688,NPU",
            "algorithm_type": "Detection",
            "verify_ssl": False,
        },
    )

    response = svc._environment_to_response(
        {
            "id": "env_ready",
            "name": "就绪环境",
            "password_encrypted": "encrypted-value",
            "password_is_configured": True,
            "password_masked_hint": "********cret",
        },
        active_id="env_ready",
    )

    assert response.password_configured is True
    assert response.password_runtime_available is True
    assert response.ready_to_publish is True
    assert response.missing_fields == []
    assert response.readiness_reason == "连接地址、账号和 MySQL 加密密码均已就绪"


def test_safe_dir_name():
    assert svc._safe_dir_name("垃圾暴露") == "垃圾暴露"
    assert "/" not in svc._safe_dir_name("a/b:c")


def test_pick_newest_model_set():
    rows = [
        {"id": 10, "created_at": "2026-01-01"},
        {"id": 430, "created_at": "2026-03-01"},
        {"id": 20, "created_at": "2026-06-01"},
    ]
    picked = svc._pick_newest_model_set(rows)
    assert picked["id"] == 430


def test_parse_accuracy_labels_from_template():
    tpl = """accuracy_list:
- garbage_street_scattered
- 0.908
- garbage_street_can
- 0.968
epoch: 80000.0
"""
    labels = svc._parse_accuracy_labels_from_running_info(tpl)
    assert labels == ["garbage_street_scattered", "garbage_street_can"]


def test_parse_accuracy_labels_double_dash_template():
    """AICM 旧模板 `- - slug` 不应把 `- slug` 当作类别名写回。"""
    tpl = """accuracy_list:
- - garbage_street_scattered
- 0.533
- - garbage_street_pack
- 0.826
epoch: 50.0
"""
    labels = svc._parse_accuracy_labels_from_running_info(tpl)
    assert labels == ["garbage_street_scattered", "garbage_street_pack"]


def test_write_running_info_ignores_template_class_order(tmp_path):
    """accuracy_list 顺序须与训练 classes 一致，不得沿用模板 zip 内旧顺序。"""
    path = tmp_path / "running_info.yml"
    tpl = path.parent / "template.yml"
    tpl.write_text(
        "accuracy_list:\n  - - garbage_street_scattered\n    - 0.1\n"
        "  - - garbage_street_pack\n    - 0.2\nepoch: 1\n",
        encoding="utf-8",
    )
    train_order = [
        "garbage_street_dump",
        "garbage_street_pack",
        "garbage_street_scattered",
        "object_box",
        "garbage_street_can",
        "non_garbage",
        "garbage_bulky",
    ]

    class Row:
        def __init__(self, index, label, test_recall):
            self.index = index
            self.label = label
            self.test_recall = test_recall

    rows = [Row(i, name, 0.1 * (i + 1)) for i, name in enumerate(train_order)]
    svc._write_running_info_yml(
        path,
        classes=train_order,
        class_metrics_rows=rows,
        epoch=50.0,
        map_val=0.4,
        res_l_count=100,
        imgsz=640,
        template_path=tpl,
        accuracy_names=train_order,
    )
    text = path.read_text(encoding="utf-8")
    dump_pos = text.index("garbage_street_dump")
    scattered_pos = text.index("garbage_street_scattered")
    assert dump_pos < scattered_pos


def test_write_running_info_nested_when_no_template(tmp_path):
    path = tmp_path / "running_info.yml"

    class Row:
        index = 0
        label = "foo_slug"
        test_recall = 0.77

    svc._write_running_info_yml(
        path,
        classes=["foo_slug", "bar_slug"],
        class_metrics_rows=[Row()],
        epoch=1.0,
        map_val=0.5,
        res_l_count=10,
        imgsz=640,
        accuracy_names=["foo_slug", "bar_slug"],
    )
    text = path.read_text(encoding="utf-8")
    assert "  - - foo_slug\n    - 0.77" in text
    assert "  - - bar_slug" in text


def test_write_running_info_no_double_dash_at_root(tmp_path):
    path = tmp_path / "running_info.yml"
    tpl = path.parent / "template.yml"
    tpl.write_text(
        "accuracy_list:\n- - foo\n- 0.1\n- - bar\n- 0.2\nepoch: 1\n",
        encoding="utf-8",
    )
    svc._write_running_info_yml(
        path,
        classes=["foo", "bar"],
        class_metrics_rows=[],
        epoch=50.0,
        map_val=0.4,
        res_l_count=100,
        imgsz=640,
        template_path=tpl,
    )
    text = path.read_text(encoding="utf-8")
    # 根级 `- - foo` 模板识别为嵌套结构，写出缩进项而非未缩进双横线
    assert "  - - foo\n    - 0.0" in text or "  - - foo\n    - 0" in text
    assert "\n- - foo\n" not in text


def test_metric_value_from_class_row_uses_test_recall_only():
    class Row:
        test_map50 = 0.88
        map50 = 0.1
        test_recall = 0.72
        recall = 0.95

    assert svc._metric_value_from_class_row(Row()) == 0.72
    assert svc._metric_value_from_class_row(None) == 0.0

    class NoTestRecall:
        recall = 0.95

    assert svc._metric_value_from_class_row(NoTestRecall()) == 0.0


def test_update_video_json_float_priorsbox(tmp_path):
    vp = tmp_path / "video.json"
    vp.write_text('{"rknn":{"Priorsbox":0.0,"NumResults":7}}', encoding="utf-8")
    svc._update_video_json(
        vp,
        imgsz=640,
        num_class=7,
        model_rel_path="./m.bmodel",
        chip_type="SOPHGO,BM1688,NPU",
    )
    import json

    data = json.loads(vp.read_text(encoding="utf-8"))
    assert data["rknn"]["Priorsbox"] == ""


def test_write_running_info_yml_flat_template(tmp_path):
    path = tmp_path / "running_info.yml"
    tpl = path.parent / "template.yml"
    tpl.write_text(
        "accuracy_list:\n- foo\n- 0.1\n- bar\n- 0.2\nepoch: 1\n",
        encoding="utf-8",
    )

    class Row:
        index = 0
        label = "foo"
        test_recall = 0.91

    svc._write_running_info_yml(
        path,
        classes=["foo", "bar"],
        class_metrics_rows=[Row()],
        epoch=100.0,
        map_val=0.84,
        res_l_count=500,
        imgsz=640,
        template_path=tpl,
    )
    text = path.read_text(encoding="utf-8")
    assert "accuracy_list:" in text
    assert "- foo\n- 0.91" in text
    assert "- bar" in text
    assert "epoch: 100.0" in text
    assert "resolution: 640x640" in text
    assert "  foo:" not in text


def test_resolve_labels_first_line_keeps_template_placeholder(tmp_path):
    tpl = tmp_path / "output_rknn" / "labels.txt"
    tpl.parent.mkdir(parents=True)
    tpl.write_text("?????\ngarbage_street_dump\n", encoding="utf-8")
    assert svc._resolve_labels_first_line(tpl) == "?????"
    tpl.write_text("??????\nfoo\n", encoding="utf-8")
    assert svc._resolve_labels_first_line(tpl) == "??????"


def test_resolve_labels_first_line_default_without_template(tmp_path):
    assert svc._resolve_labels_first_line(tmp_path / "missing.txt") == "?????"


def test_aicm_upload_label_lines_use_training_slugs_not_cn():
    """AICM 后台类目与 label_value 英文一致；勿用拉框预设中文 label_key。"""
    classes = [
        "garbage_street_dump",
        "garbage_street_pack",
        "garbage_street_scattered",
    ]
    lines = svc._aicm_upload_label_lines(classes)
    assert lines == classes
    assert "垃圾堆" not in lines


def test_resolve_publish_class_slugs_maps_cn_display_to_en(db, monkeypatch):
    from types import SimpleNamespace

    raw = ["骑行自行车的人", "自行车"]
    preset_items = [
        SimpleNamespace(
            label_key="骑行自行车的人",
            label_value="person_riding_bicycle",
            sort_order=0,
            id=1,
        ),
        SimpleNamespace(
            label_key="自行车",
            label_value="bicycle",
            sort_order=1,
            id=2,
        ),
    ]

    class _Preset:
        items = preset_items

    monkeypatch.setattr(
        svc.crud_annotation_taxonomy,
        "label_preset_get_by_slug",
        lambda _db, slug: _Preset() if slug == "nonMotorParking" else None,
    )
    task = SimpleNamespace(config='{"defaultRectPresetId":"nonMotorParking"}')
    slugs = svc._resolve_publish_class_slugs(
        db,
        raw_classes=raw,
        preset_slug="nonMotorParking",
        task=task,
    )
    assert slugs == ["person_riding_bicycle", "bicycle"]


def test_update_video_json_null_priors(tmp_path):
    vp = tmp_path / "video.json"
    vp.write_text(
        '{"rknn":{"InputSize":320,"Priorsbox":null,"NumResults":null}}',
        encoding="utf-8",
    )
    svc._update_video_json(
        vp,
        imgsz=640,
        num_class=6,
        model_rel_path="./test_int8.bmodel",
        chip_type="SOPHGO,BM1688,NPU",
    )
    import json

    data = json.loads(vp.read_text(encoding="utf-8"))
    assert data["rknn"]["Priorsbox"] == ""
    assert data["rknn"]["NumResults"] == 6


def test_update_video_json(tmp_path):
    vp = tmp_path / "video.json"
    vp.write_text('{"rknn":{"InputSize":320}}', encoding="utf-8")
    svc._update_video_json(
        vp,
        imgsz=640,
        num_class=6,
        model_rel_path="./test_int8.bmodel",
        chip_type="SOPHGO,BM1688,NPU",
    )
    import json

    data = json.loads(vp.read_text(encoding="utf-8"))
    assert data["rknn"]["InputSize"] == 640
    assert data["rknn"]["NumClass"] == 6
    assert data["rknn"]["ModelAlgType"] == "yoloV11"
    assert data["rknn"]["ModelChipType"] == "SOPHGO,BM1688,NPU"


def test_aicm_response_ok():
    assert svc._aicm_response_ok('{"data": {}, "error_code": 0, "error_info": ""}')
    assert not svc._aicm_response_ok('{"error_code": 1, "error_info": "x"}')
    assert not svc._aicm_response_ok("")


def test_run_publish_job_persists_model_set_id_across_sessions(
    db,
    testuser_token_headers,
    monkeypatch,
    tmp_path,
):
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    user_id = int(user.id)
    with begin_transaction(db):
        export_row = crud_yolo_bm1688_export.create(
            db,
            user_id=user_id,
            task_id=9876501,
            yolo_train_job_id=9876501,
            imgsz=640,
        )
        export_id = int(export_row.id)

    background_session = sessionmaker(autoflush=False, bind=db.get_bind())
    monkeypatch.setattr(db_module, "SessionLocal", background_session)
    monkeypatch.setattr(catalog_service.settings, "BASE_DATA_DIR", str(tmp_path))
    package_path = tmp_path / "publish-work" / "model-551.zip"
    package_path.parent.mkdir(parents=True)
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("model.bin", b"published-model-551")
    completed: dict[str, object] = {}

    class FakeTracker:
        def __init__(self, job_id):
            self.job_id = job_id
            self._data = {}

        def complete(self, result):
            completed.update(result)

        def fail(self, message, *, failed_step=None):
            raise AssertionError(message)

    monkeypatch.setattr(svc, "PublishJobTracker", FakeTracker)
    monkeypatch.setattr(
        svc,
        "publish_to_aicm",
        lambda *args, **kwargs: AicmPublishResultResponse(
            ok=True,
            export_id=export_id,
            model_id="551",
            model_set_id=551,
            package_path=str(package_path),
        ),
    )

    svc._run_publish_job_sync(
        "publish-persist-model-id",
        export_id,
        user_id,
    )

    assert completed["model_set_id"] == 551
    with background_session() as verify_db:
        persisted = crud_yolo_bm1688_export.get(verify_db, export_id)
        assert persisted is not None
        assert persisted.published_model_id == 551
        catalog = catalog_service.resolve_model(verify_db, "551")
        assert catalog.package_rel_path == "published_model_packages/551/model.zip"
        assert (tmp_path / catalog.package_rel_path).is_file()


def test_resolve_uploaded_model_set_id_from_response():
    resolved = svc._resolve_uploaded_model_set_id(
        '{"data":{"model_set":{"id":"551"}},"error_code":0}',
        correlated_ids={551},
    )
    assert resolved == (551, "upload_response")


@pytest.mark.parametrize("id_key", ["id", "_id"])
def test_resolve_uploaded_model_set_id_from_root_plain_id(id_key):
    upload_text = json.dumps({id_key: "551", "error_code": 0})

    assert svc._upload_response_model_set_ids(upload_text) == [551]
    assert svc._resolve_uploaded_model_set_id(
        upload_text,
        correlated_ids={551},
    ) == (551, "upload_response")


def test_resolve_uploaded_model_set_id_from_unique_correlation_name():
    resolved = svc._resolve_uploaded_model_set_id(
        '{"data":{},"error_code":0}',
        correlated_ids={551},
    )
    assert resolved == (551, "upload_correlation_name")


def test_locate_uploaded_model_set_id_for_publish_prefers_response_id(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_locate_uploaded_model_set_id",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("response id should avoid name lookup")
        ),
    )

    assert svc._locate_uploaded_model_set_id_for_publish(
        None,
        "http://example",
        '{"data":{"model_set_id":551},"error_code":0}',
        correlation_name="lu08_abcdef12345678",
        before_id=550,
        verify=False,
    ) == (551, "upload_response_model_set_id")


def test_locate_uploaded_model_set_id_for_publish_falls_back_to_new_numeric_id(
    monkeypatch,
):
    def missing(*args, **kwargs):
        raise svc.LabelUException(svc.ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR, message="临时名称未命中")

    monkeypatch.setattr(svc, "_locate_uploaded_model_set_id", missing)
    monkeypatch.setattr(
        svc,
        "_wait_for_new_numeric_model_set",
        lambda *args, **kwargs: {"id": 552, "name": "20261013001"},
    )

    assert svc._locate_uploaded_model_set_id_for_publish(
        None,
        "http://example",
        '{"data":{},"error_code":0}',
        correlation_name="lu08_abcdef12345678",
        before_id=550,
        verify=False,
    ) == (552, "upload_new_numeric_model_set")


def test_resolve_uploaded_model_set_id_rejects_ambiguous_correlation_name():
    with pytest.raises(svc.LabelUException) as exc_info:
        svc._resolve_uploaded_model_set_id(
            '{"data":{},"error_code":0}',
            correlated_ids={551, 552},
        )
    assert "匹配到多个模型集 id=[551, 552]" in exc_info.value.detail
    assert "已停止 edit_name" in exc_info.value.detail


def test_resolve_uploaded_model_set_id_rejects_missing_list_difference():
    with pytest.raises(svc.LabelUException) as exc_info:
        svc._resolve_uploaded_model_set_id(
            '{"data":{},"error_code":0}',
        )
    assert "未找到与本次上传临时关联名一致" in exc_info.value.detail
    assert "已停止 edit_name" in exc_info.value.detail


def test_resolve_uploaded_model_set_id_rejects_ambiguous_response_ids():
    with pytest.raises(svc.LabelUException) as exc_info:
        svc._resolve_uploaded_model_set_id(
            '{"data":{"model_set_id":551,"modelSetId":552},"error_code":0}',
        )
    assert "包含多个模型集 id" in exc_info.value.detail
    assert "已停止 edit_name" in exc_info.value.detail


def test_resolve_uploaded_model_set_id_requires_response_name_confirmation():
    with pytest.raises(svc.LabelUException) as exc_info:
        svc._resolve_uploaded_model_set_id(
            '{"data":{"model_set_id":550},"error_code":0}',
        )
    assert "未查询到 id=550 对应本次上传临时关联名" in exc_info.value.detail
    assert "已停止 edit_name" in exc_info.value.detail


def test_resolve_uploaded_model_set_id_rejects_response_name_mismatch():
    with pytest.raises(svc.LabelUException) as exc_info:
        svc._resolve_uploaded_model_set_id(
            '{"data":{"model_set_id":551},"error_code":0}',
            correlated_ids={552},
        )

    assert "临时关联名查询对应 id=[552]" in exc_info.value.detail
    assert "与上传响应 id=551 不一致" in exc_info.value.detail
    assert "已停止 edit_name" in exc_info.value.detail


@pytest.mark.parametrize(
    "upload_text",
    [
        '{"data":999,"error_code":0}',
        '{"data":"999","error_code":0}',
        '{"result":999,"error_code":0}',
    ],
)
def test_upload_response_ignores_ambiguous_plain_ids(upload_text):
    assert svc._upload_response_model_set_ids(upload_text) == []
    with pytest.raises(svc.LabelUException) as exc_info:
        svc._resolve_uploaded_model_set_id(
            upload_text,
        )
    assert "未找到与本次上传临时关联名一致的模型集" in exc_info.value.detail
    assert "已停止 edit_name" in exc_info.value.detail


def test_fetch_model_set_ids_by_exact_name_filters_partial_matches(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_search_model_sets",
        lambda sess, base, *, like_name, verify, limit: [
            {"id": 551, "name": like_name},
            {"id": 552, "name": f"{like_name}_other"},
            {"id": 0, "name": like_name},
        ],
    )

    assert svc._fetch_model_set_ids_by_exact_name(
        None,
        "http://example",
        name="labelu_tmp_e8_token",
        verify=False,
    ) == {551}


def test_wait_for_model_set_ids_by_exact_name_retries_index_delay(monkeypatch):
    query_results = iter([set(), set(), {551}])
    sleep_calls = []
    monkeypatch.setattr(
        svc,
        "_fetch_model_set_ids_by_exact_name",
        lambda *args, **kwargs: next(query_results),
    )
    monkeypatch.setattr(svc.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    result = svc._wait_for_model_set_ids_by_exact_name(
        None,
        "http://example",
        name="labelu_tmp_e8_token",
        verify=False,
        expected_id=551,
        attempts=3,
        delay_seconds=0.01,
    )

    assert result == {551}
    assert sleep_calls == [0.01, 0.01]


def test_get_download_url_reports_missing_model_set_id():
    class Response:
        status_code = 200
        text = '{"data":{},"error_code":1,"error_info":"not found"}'

        def json(self):
            return {
                "data": {},
                "error_code": 1,
                "error_info": "not found",
            }

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    with pytest.raises(svc.LabelUException) as exc_info:
        svc._get_download_url(
            Session(),
            "http://example",
            570,
            verify=False,
        )

    assert "id=570" in exc_info.value.detail
    assert "不存在或最终包尚未生成" in exc_info.value.detail


def test_wait_for_new_numeric_model_set_uses_newest_numeric_id(monkeypatch):
    query_results = iter(
        [
            [{"id": 555, "name": "existing"}, {"id": 536, "name": "20260706001"}],
            [{"id": 556, "name": "other upload"}, {"id": 555, "name": "existing"}],
            [
                {"id": 558, "name": "20260730002"},
                {"id": 557, "name": "20260730001"},
                {"id": 556, "name": "other upload"},
            ],
        ]
    )
    sleep_calls = []
    monkeypatch.setattr(
        svc,
        "_search_model_sets",
        lambda *args, **kwargs: next(query_results),
    )
    monkeypatch.setattr(svc.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    result = svc._wait_for_new_numeric_model_set(
        None,
        "http://example",
        before_id=555,
        verify=False,
        attempts=3,
        delay_seconds=0.01,
    )

    assert result == {"id": 558, "name": "20260730002"}
    assert sleep_calls == [0.01, 0.01]


def test_wait_for_new_numeric_model_set_does_not_reuse_history(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_search_model_sets",
        lambda *args, **kwargs: [
            {"id": 555, "name": "existing"},
            {"id": 536, "name": "20260706001"},
        ],
    )

    with pytest.raises(svc.LabelUException) as exc_info:
        svc._wait_for_new_numeric_model_set(
            None,
            "http://example",
            before_id=555,
            verify=False,
            attempts=1,
            delay_seconds=0,
        )

    assert "上传前最新 id=555" in exc_info.value.detail
    assert "当前最新 id=555" in exc_info.value.detail
    assert "已停止 edit_name" in exc_info.value.detail


def test_upload_model_package_sends_required_short_name(monkeypatch, tmp_path):
    zip_path = tmp_path / "model.zip"
    zip_path.write_bytes(b"zip")
    captured = {}

    class Response:
        status_code = 200
        text = '{"data":{},"error_code":0,"error_info":""}'

    class Cookies:
        def get_dict(self):
            return {"sessionid": "authenticated"}

    class Session:
        cookies = Cookies()

    class Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, url, *, data, files):
            filename, file_obj, content_type = files["model_file"]
            captured.update(
                url=url,
                data=data,
                filename=filename,
                content_type=content_type,
                body=file_obj.read(),
            )
            return Response()

    monkeypatch.setattr(svc.httpx, "Client", Client)

    result = svc._upload_model_package(
        Session(),
        "http://example",
        zip_path,
        name="lu08_abcdef12345678",
        chip_type="SOPHGO,BM1688,NPU",
        algorithm_type="Detection",
        verify=False,
    )

    assert '"error_code":0' in result
    assert list(captured["data"]) == [
        "_method",
        "chip_type",
        "algorithm_type",
        "name",
    ]
    assert captured["data"]["name"] == "lu08_abcdef12345678"
    assert captured["filename"] == "model.zip"
    assert captured["content_type"] == "application/zip"
    assert captured["body"] == b"zip"
    assert captured["client_kwargs"]["cookies"] == {"sessionid": "authenticated"}
    timeout = captured["client_kwargs"]["timeout"]
    assert timeout.connect == 20.0
    assert timeout.write == 600.0
    assert timeout.read == 600.0


def test_build_upload_correlation_name_stays_within_aicm_limit():
    name = svc._build_upload_correlation_name(123456789)

    assert 1 <= len(name) <= svc.AICM_MODEL_NAME_MAX_LENGTH


def test_display_name_stays_within_aicm_limit():
    name, version = svc._display_name_for_publish_seq(
        "这是一个非常长的标签契约名称abcdefghijklmnop", 13
    )

    assert len(name) <= svc.AICM_MODEL_NAME_MAX_LENGTH
    assert name.endswith(f"_bm1688_{version}")


def test_format_aicm_upload_write_timeout_reports_receiver_problem():
    message = svc._format_aicm_network_error(
        svc.httpx.WriteTimeout("timed out"),
        "http://aicm.local:8102",
        stage="upload",
    )

    assert "模型包上传发送超时" in message
    assert "已成功连接并登录 AICM" in message
    assert "600 秒" in message
    assert "根地址" not in message


def test_locate_uploaded_model_set_id_always_verifies_explicit_id_name(monkeypatch):
    lookup = {}

    def fake_wait(sess, base, *, name, verify, expected_id, **kwargs):
        lookup.update(name=name, expected_id=expected_id)
        return {551}

    monkeypatch.setattr(svc, "_wait_for_model_set_ids_by_exact_name", fake_wait)

    resolved = svc._locate_uploaded_model_set_id(
        None,
        "http://example",
        '{"data":{"model_set_id":551},"error_code":0}',
        correlation_name="labelu_tmp_e8_token",
        verify=False,
    )

    assert resolved == (551, "upload_response")
    assert lookup == {"name": "labelu_tmp_e8_token", "expected_id": 551}


def test_locate_uploaded_model_set_id_rejects_concurrent_name_mismatch(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_wait_for_model_set_ids_by_exact_name",
        lambda *args, **kwargs: {552},
    )

    with pytest.raises(svc.LabelUException) as exc_info:
        svc._locate_uploaded_model_set_id(
            None,
            "http://example",
            '{"id":551,"error_code":0}',
            correlation_name="labelu_tmp_e8_token",
            verify=False,
        )

    assert "与上传响应 id=551 不一致" in exc_info.value.detail
    assert "已停止 edit_name" in exc_info.value.detail


def test_template_model_basename(tmp_path):
    out = tmp_path / "output_rknn"
    out.mkdir()
    (out / "video.json").write_text(
        '{"rknn":{"ModelPath":"./ljbl_int8_1b_1core.bmodel","NumClass":7}}',
        encoding="utf-8",
    )
    assert svc._template_model_basename(out) == "ljbl_int8_1b_1core.bmodel"


def test_published_bmodel_filename_uses_compiler_name_not_template():
    assert (
        svc._published_bmodel_filename("BM1688/yolov11n_int8_1b.bmodel")
        == "yolov11n_int8_1b.bmodel"
    )
    assert (
        svc._published_bmodel_filename("BM1688/yolov11n_fp16_1b.bmodel")
        == "yolov11n_fp16_1b.bmodel"
    )


def test_find_package_root(tmp_path):
    pkg = tmp_path / "model-20260319"
    pkg.mkdir()
    (pkg / "running_info.yml").write_text("epoch: 1\n", encoding="utf-8")
    assert svc._find_package_root(tmp_path) == pkg


def test_normalize_model_alg_type_for_name():
    assert svc._normalize_model_alg_type_for_name("yoloV11") == "yolov11"
    assert svc._normalize_model_alg_type_for_name("YOLO v11") == "yolov11"


def test_model_version_segment():
    assert svc._model_version_segment(1) == "v1"
    assert svc._model_version_segment(13) == "v13"


def test_build_aicm_display_name(tmp_path, monkeypatch):
    cache = tmp_path / "cache" / "垃圾暴露"
    cache.mkdir(parents=True)
    monkeypatch.setattr(svc, "_templates_root", lambda: tmp_path / "cache")
    monkeypatch.setattr(svc, "_safe_dir_name", lambda n: n)
    name, seq, ver = svc._build_aicm_display_name(preset_name="垃圾暴露")
    assert seq == 1
    assert ver == "v1"
    assert name == "垃圾暴露_bm1688_v1"
    name2, seq2, ver2 = svc._build_aicm_display_name(preset_name="垃圾暴露")
    assert seq2 == 2
    assert ver2 == "v2"
    assert name2 == "垃圾暴露_bm1688_v2"


def test_aicm_name_unique_conflict():
    body = (
        '{"data": {}, "error_code": 1, '
        '"error_info": "{\\"name\\": [{\\"message\\": \\"具有 名称 的 模型 已存在。\\", '
        '\\"code\\": \\"unique\\"}]}"}'
    )
    assert svc._aicm_name_unique_conflict(body) is True
    assert (
        svc._aicm_name_unique_conflict('{"error_code": 0, "error_info": ""}') is False
    )


def test_display_name_for_publish_seq():
    name, ver = svc._display_name_for_publish_seq("占道经营", 3)
    assert name == "占道经营_bm1688_v3"
    assert ver == "v3"


def test_rename_model_set_with_unique_retry(monkeypatch):
    calls: list[str] = []

    def fake_edit(sess, base, model_set_id, name, *, verify):
        calls.append(name)
        if name.endswith("_v3"):
            return (
                '{"data": {}, "error_code": 1, '
                '"error_info": "{\\"name\\": [{\\"message\\": \\"具有 名称 的 模型 已存在。\\", '
                '\\"code\\": \\"unique\\"}]}"}'
            )
        return '{"data": {}, "error_code": 0, "error_info": ""}'

    monkeypatch.setattr(svc, "_edit_model_set_name", fake_edit)
    monkeypatch.setattr(svc, "_bump_publish_version_seq", lambda preset: 3)
    monkeypatch.setattr(svc, "_write_publish_version_seq", lambda preset, seq: None)

    display_name, seq, ver, text = svc._rename_model_set_with_unique_retry(
        sess=None,
        base="http://example",
        model_set_id=517,
        preset_name="占道经营",
        verify=False,
    )
    assert calls == ["占道经营_bm1688_v3", "占道经营_bm1688_v4"]
    assert display_name == "占道经营_bm1688_v4"
    assert seq == 4
    assert ver == "v4"
    assert svc._aicm_response_ok(text)


def test_rename_model_set_with_unique_retry_uses_task_name_and_limit(monkeypatch):
    calls: list[str] = []

    def fake_edit(sess, base, model_set_id, name, *, verify):
        calls.append(name)
        return '{"data": {}, "error_code": 0, "error_info": ""}'

    monkeypatch.setattr(svc, "_edit_model_set_name", fake_edit)

    display_name, _, _, text = svc._rename_model_set_with_unique_retry(
        sess=None,
        base="http://example",
        model_set_id=517,
        preset_name="ignored",
        target_name="安全帽检测第一批这是超长任务名称",
        verify=False,
    )

    assert display_name == calls[0]
    assert len(display_name) <= svc.AICM_MODEL_NAME_MAX_LENGTH
    assert display_name.startswith("安全帽检测第一批")
    assert svc._aicm_response_ok(text)


def test_publish_history_uses_uploaded_latest_model_set_id_from_logs(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_strategy_task_marker_for_export",
        lambda _db, _export_id: {
            "task_type": "model_capability",
            "task_type_label": "automated_model_optimization",
            "external_task_id": "third-party-model-1",
        },
    )
    monkeypatch.setattr(
        svc.crud_yolo_bm1688_export,
        "get",
        lambda _db, _export_id: type(
            "ExportRow",
            (),
            {
                "business_thresholds_json": '{"effective":{"edge_low_threshold":0.41}}',
                "validation_thresholds_json": '{"effective":{"conf_thres":0.41}}',
                "threshold_binding_json": '{"published_model_id":537}',
            },
        )(),
    )
    row = svc._job_to_history_row(
        None,
        {
            "job_id": "publish-1",
            "export_id": 3,
            "status": "COMPLETED",
            "result": {
                "ok": True,
                "export_id": 3,
                "model_set_id": 530,
                "upload_name": "非机动车违停_bm1688_v1",
            },
            "log_lines": [
                "已选用 yoloV11 模板 model_set_id=530",
                "最新模型集 id=537，调用 edit_name → 非机动车违停_bm1688_v1",
            ],
        },
    )

    assert row.model_set_id == 537
    assert row.model_id == "537"
    assert row.upload_name == "非机动车违停_bm1688_v1"

    assert row.business_thresholds["effective"]["edge_low_threshold"] == 0.41
    assert row.validation_thresholds["effective"]["conf_thres"] == 0.41
    assert row.threshold_binding["published_model_id"] == 537
    assert row.strategy_task is not None
    assert row.strategy_task.external_task_id == "third-party-model-1"


def test_publish_result_artifact_metadata_survives_job_round_trip(monkeypatch):
    monkeypatch.setattr(
        svc,
        "_load_export_threshold_payloads",
        lambda _db, *, export_id: (None, None, None),
    )
    monkeypatch.setattr(
        svc,
        "_strategy_task_marker_for_export",
        lambda _db, _export_id: {
            "task_type": "threshold_experiment",
            "task_type_label": "automated_threshold_experiment",
            "external_task_id": "third-party-threshold-1",
        },
    )
    persisted_result = svc.AicmPublishResultResponse(
        ok=True,
        export_id=8,
        precision="int8",
        bmodel_rel_path="BM1688/yolov11n_int8_1b.bmodel",
        model_id="551",
        model_set_id=551,
    ).dict()

    response = svc._job_to_response(
        None,
        {
            "job_id": "publish-artifact-metadata",
            "export_id": 8,
            "status": "COMPLETED",
            "result": persisted_result,
        },
    )

    assert response.result is not None
    assert response.result.precision == "int8"
    assert response.result.bmodel_rel_path == "BM1688/yolov11n_int8_1b.bmodel"
    assert response.strategy_task is not None
    assert response.strategy_task.external_task_id == "third-party-threshold-1"


def test_patch_labels_txt_keeps_first_line_and_slots(tmp_path):
    lp = tmp_path / "labels.txt"
    lp.write_text("?????\nclass_a\nclass_b\n", encoding="utf-8")
    svc._patch_labels_txt_in_place(lp, ["foo_slug", "bar_slug"])
    lines = lp.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "?????"
    assert lines[1:] == ["foo_slug", "bar_slug"]


def test_patch_labels_txt_keeps_six_question_marks(tmp_path):
    """AICM 部分模板首行为 ??????（6 个 ?），发布不得变成 ?????（5 个）。"""
    lp = tmp_path / "labels.txt"
    lp.write_text("??????\nclass_a\nclass_b\n", encoding="utf-8")
    before = svc._labels_first_line_preserve(lp.read_text(encoding="utf-8"))
    assert before == "??????"
    assert len(before) == 6
    svc._patch_labels_txt_in_place(lp, ["foo_slug", "bar_slug"])
    after = svc._labels_first_line_preserve(lp.read_text(encoding="utf-8"))
    assert after == before
    assert len(after) == 6


def test_patch_labels_txt_preserves_crlf_and_custom_first_line(tmp_path):
    lp = tmp_path / "labels.txt"
    lp.write_bytes("垃圾暴露模型\r\nold_a\r\nold_b\r\n".encode("utf-8"))
    svc._patch_labels_txt_in_place(lp, ["foo_slug", "bar_slug"])
    raw = lp.read_bytes().decode("utf-8")
    assert raw.startswith("垃圾暴露模型\r\n")
    assert "foo_slug\r\nbar_slug\r\n" in raw


def test_patch_labels_txt_expands_from_first_line_only_template(tmp_path):
    lp = tmp_path / "labels.txt"
    lp.write_text("?????\n", encoding="utf-8")
    svc._patch_labels_txt_in_place(lp, ["foo_slug", "bar_slug"])
    lines = lp.read_text(encoding="utf-8").splitlines()
    assert lines == ["?????", "foo_slug", "bar_slug"]


def test_patch_labels_txt_expands_when_more_classes_than_template_slots(tmp_path):
    """训练 7 类、模板 6 槽时自动扩展 labels.txt，不要求重下模板。"""
    lp = tmp_path / "labels.txt"
    lp.write_text("?????\nslot1\nslot2\nslot3\nslot4\nslot5\nslot6\n", encoding="utf-8")
    classes = [f"class_{i}" for i in range(1, 8)]
    svc._patch_labels_txt_in_place(lp, classes)
    lines = lp.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "?????"
    assert lines[1:] == classes


def test_patch_labels_txt_truncates_when_fewer_classes_than_template_slots(tmp_path):
    """训练 2 类、模板 4 槽时只保留首行 + 训练类，不保留模板旧类别行。"""
    lp = tmp_path / "labels.txt"
    lp.write_text(
        "?????\ntemplate_a\ntemplate_b\ntemplate_c\ntemplate_d\n",
        encoding="utf-8",
    )
    classes = ["train_a", "train_b"]
    svc._patch_labels_txt_in_place(lp, classes)
    lines = lp.read_text(encoding="utf-8").splitlines()
    assert lines == ["?????", "train_a", "train_b"]


def test_patch_video_json_only_existing_keys(tmp_path):
    vp = tmp_path / "video.json"
    raw = (
        '{"rknn":{"InputSize":320,"NumClass":6,"ModelPath":"./old.bmodel",'
        '"ModelChipType":"OLD","ExtraField":1}}'
    )
    vp.write_text(raw, encoding="utf-8")
    svc._patch_video_json_in_place(
        vp,
        imgsz=640,
        num_class=6,
        chip_type="SOPHGO,BM1688,NPU",
    )
    text = vp.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["rknn"]["ExtraField"] == 1
    assert data["rknn"]["ModelPath"] == "./old.bmodel"
    assert data["rknn"]["InputSize"] == 640
    assert data["rknn"]["ModelChipType"] == "OLD"
    assert "ModelFunction" not in data["rknn"]
    assert '"ExtraField":1' in text.replace(" ", "")


def test_patch_video_json_keeps_num_results_zero_and_null_priors(tmp_path):
    """NumResults=0、Priorsbox=null、ModelPath 为模板占位，发布不得擅自改写。"""
    vp = tmp_path / "video.json"
    raw = (
        '{"rknn":{"InputSize":640,"NumClass":7,"NumResults":0,"Priorsbox":null,'
        '"Labelsmap":"./labels.txt","ModelPath":"./ljbl_fp16.bmodel","ModelChipType":"OLD"}}'
    )
    vp.write_text(raw, encoding="utf-8")
    svc._patch_video_json_in_place(
        vp,
        imgsz=640,
        num_class=7,
        chip_type="SOPHGO,BM1688,NPU",
    )
    text = vp.read_text(encoding="utf-8")
    data = json.loads(text)
    rknn = data["rknn"]
    assert rknn["NumResults"] == 0
    assert rknn["Priorsbox"] is None
    assert rknn["Labelsmap"] == "./labels.txt"
    assert rknn["ModelPath"] == "./ljbl_fp16.bmodel"
    assert rknn["ModelChipType"] == "OLD"
    assert text == raw


def test_initialize_direct_yolov11_package_matches_reference_contract(tmp_path):
    pkg = tmp_path / "model-20260714085442"

    out = svc._initialize_direct_yolov11_package(
        pkg,
        input_size=640,
        num_class=1,
    )

    files = {
        path.relative_to(pkg).as_posix() for path in pkg.rglob("*") if path.is_file()
    }
    assert files == {
        "running_info.yml",
        "output_rknn/labels.txt",
        "output_rknn/video.json",
        "output_rknn/yolov11n_int8_1b.bmodel",
    }
    assert not list(pkg.rglob("*.ini"))
    assert (out / "labels.txt").read_text(encoding="utf-8").splitlines() == ["??????"]
    video = json.loads((out / "video.json").read_text(encoding="utf-8"))["rknn"]
    assert video == {
        "InputSize": 640,
        "NumResults": 0,
        "NumClass": 1,
        "CropType": 3,
        "MaxBatchSize": 1,
        "ImgCollect": 1,
        "ModelFunction": "targetDetetct",
        "ModelAlgType": "yoloV11",
        "ModelPath": "./yolov11n_int8_1b.bmodel",
        "Priorsbox": "",
        "Labelsmap": "./labels.txt",
        "ModelChipType": "SOPHGO,BM1688,NPU",
    }


def test_build_direct_publish_zip_preserves_selected_fp16_filename(
    tmp_path, monkeypatch
):
    source_bmodel = tmp_path / "compiled_fp16.bmodel"
    source_bmodel.write_bytes(b"bm1688-fp16")
    job = type(
        "Job",
        (),
        {
            "id": 26,
            "job_type": "detection",
            "imgsz": 1280,
            "weights_path": "best.pt",
            "metrics_summary": None,
            "epochs": 37,
            "val_sample_count": 302,
            "train_sample_count": 1000,
        },
    )()
    export_row = type("Export", (), {"imgsz": 640})()
    task = type("Task", (), {"id": 144, "name": "非机动车乱停"})()

    monkeypatch.setattr(svc, "_publish_work_root", lambda: tmp_path / "work")
    monkeypatch.setattr(
        svc.crud_yolo_bm1688_export, "get", lambda db, export_id: export_row
    )
    monkeypatch.setattr(
        svc,
        "_resolve_bmodel",
        lambda *args, **kwargs: (
            source_bmodel,
            "BM1688/yolov11n_fp16_1b.bmodel",
        ),
    )
    monkeypatch.setattr(
        svc,
        "_class_names_with_source_for_publish",
        lambda value: (["nonvehicle_electricbike"], "training"),
    )
    monkeypatch.setattr(
        svc,
        "_resolve_publish_class_slugs",
        lambda *args, **kwargs: pytest.fail(
            "direct YOLOv11 publishing must preserve training class names"
        ),
    )
    monkeypatch.setattr(svc, "_aicm_upload_label_lines", lambda values: list(values))
    monkeypatch.setattr(svc, "_training_val_metrics_for_job", lambda value: None)
    monkeypatch.setattr(svc, "_aggregate_map_for_publish", lambda *args: 0.722)
    monkeypatch.setattr(svc, "actual_epochs_for_train_job", lambda **kwargs: 37)
    monkeypatch.setattr(
        svc,
        "_sample_counts_for_job",
        lambda value: {"train": 1000, "val": 302, "test": 113, "total": 1415},
    )
    metric_row = type(
        "MetricRow",
        (),
        {"label": "nonvehicle_electricbike", "index": 0, "test_recall": 0.952},
    )()
    monkeypatch.setattr(
        svc,
        "_class_metrics_for_job",
        lambda value: type("ClassMetrics", (), {"rows": [metric_row]})(),
    )

    package_zip = svc._build_publish_package(
        None,
        export_id=16,
        job=job,
        db=object(),
        task=task,
        preset_name="非机动车乱停",
        preset_slug="illegalParking",
        steps=[],
        chip_type="SHOULD_NOT_REWRITE_VIDEO_JSON",
        bmodel_precision="fp16",
    )

    with zipfile.ZipFile(package_zip, "r") as archive:
        names = {name for name in archive.namelist() if not name.endswith("/")}
        assert names == {
            "running_info.yml",
            "output_rknn/labels.txt",
            "output_rknn/video.json",
            "output_rknn/yolov11n_fp16_1b.bmodel",
        }
        assert archive.read("output_rknn/yolov11n_fp16_1b.bmodel") == b"bm1688-fp16"
        labels = archive.read("output_rknn/labels.txt").decode("utf-8").splitlines()
        assert labels == ["??????", "nonvehicle_electricbike"]
        video = json.loads(archive.read("output_rknn/video.json"))["rknn"]
        assert video["InputSize"] == 640
        assert video["NumClass"] == 1
        assert video["ModelPath"] == "./yolov11n_fp16_1b.bmodel"
        assert video["ModelAlgType"] == "yoloV11"
        assert video["Priorsbox"] == ""
        assert video["ModelChipType"] == "SOPHGO,BM1688,NPU"
        running_info = archive.read("running_info.yml").decode("utf-8")
        assert running_info == (
            "accuracy_list:\n"
            "  - - nonvehicle_electricbike\n"
            "    - 0.952\n"
            "epoch: 37.0\n"
            "res_l_count: 1415\n"
            "mAP: 0.722\n"
            "resolution: 1280x1280\n"
        )
        assert not any(name.endswith(".ini") for name in names)


def test_create_publish_work_dir_never_deletes_existing_package(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc, "_publish_work_root", lambda: tmp_path / "work")

    first = svc._create_publish_work_dir(20)
    retained_package = first / "model-retained.zip"
    retained_package.write_bytes(b"published-model-package")

    second = svc._create_publish_work_dir(20)

    assert second != first
    assert retained_package.read_bytes() == b"published-model-package"


def test_resolve_template_bmodel_dest_uses_model_path(tmp_path):
    out = tmp_path / "output_rknn"
    out.mkdir()
    (out / "ljbl_fp16.bmodel").write_bytes(b"old")
    (out / "video.json").write_text(
        '{"rknn":{"ModelPath":"./ljbl_fp16.bmodel"}}', encoding="utf-8"
    )
    dest = svc._resolve_template_bmodel_dest(out)
    assert dest == out / "ljbl_fp16.bmodel"


def test_deploy_publish_bmodel_renames_fp16_and_patches_model_path(tmp_path):
    out = tmp_path / "output_rknn"
    out.mkdir()
    template_bmodel = out / "zdjy_int8_1b_1core.bmodel"
    template_bmodel.write_bytes(b"template-int8")
    (out / "video.json").write_text(
        '{"rknn":{"ModelPath":"./zdjy_int8_1b_1core.bmodel","InputSize":640,"NumClass":1}}',
        encoding="utf-8",
    )
    src = tmp_path / "yolov11n_fp16_1b.bmodel"
    src.write_bytes(b"compiler-fp16")
    dest, model_path = svc._deploy_publish_bmodel(
        out, src, "BM1688/yolov11n_fp16_1b.bmodel"
    )
    assert dest == out / "yolov11n_fp16_1b.bmodel"
    assert dest.read_bytes() == b"compiler-fp16"
    assert not template_bmodel.is_file()
    assert model_path == "./yolov11n_fp16_1b.bmodel"


def test_deploy_publish_bmodel_inplace_when_names_match(tmp_path):
    out = tmp_path / "output_rknn"
    out.mkdir()
    bmodel = out / "yolov11n_int8_1b.bmodel"
    bmodel.write_bytes(b"old")
    (out / "video.json").write_text(
        '{"rknn":{"ModelPath":"./yolov11n_int8_1b.bmodel"}}', encoding="utf-8"
    )
    src = tmp_path / "src.bmodel"
    src.write_bytes(b"new-int8")
    dest, model_path = svc._deploy_publish_bmodel(
        out, src, "BM1688/yolov11n_int8_1b.bmodel"
    )
    assert dest == bmodel
    assert bmodel.read_bytes() == b"new-int8"
    assert model_path is None


def test_patch_video_json_updates_model_path(tmp_path):
    vp = tmp_path / "video.json"
    raw = (
        '{"rknn":{"InputSize":640,"NumClass":7,"ModelPath":"./zdjy_int8_1b_1core.bmodel",'
        '"ModelChipType":"OLD"}}'
    )
    vp.write_text(raw, encoding="utf-8")
    svc._patch_video_json_in_place(
        vp,
        imgsz=640,
        num_class=7,
        chip_type="SOPHGO,BM1688,NPU",
        model_rel_path="./yolov11n_fp16_1b.bmodel",
    )
    data = json.loads(vp.read_text(encoding="utf-8"))
    assert data["rknn"]["ModelPath"] == "./yolov11n_fp16_1b.bmodel"


def test_assert_package_files_unchanged_allows_bmodel_swap():
    before = frozenset(
        {
            "output_rknn/video.json",
            "output_rknn/zdjy_int8_1b_1core.bmodel",
            "running_info.yml",
        }
    )
    after = frozenset(
        {
            "output_rknn/video.json",
            "output_rknn/yolov11n_fp16_1b.bmodel",
            "running_info.yml",
        }
    )
    svc._assert_package_files_unchanged(before, after)


def test_inplace_bmodel_copy_keeps_package_file_list(tmp_path):
    pkg = tmp_path / "pkg"
    out = pkg / "output_rknn"
    out.mkdir(parents=True)
    (pkg / "running_info.yml").write_text("epoch: 1\n", encoding="utf-8")
    bmodel = out / "ljbl_fp16.bmodel"
    bmodel.write_bytes(b"template")
    src = tmp_path / "export.bmodel"
    src.write_bytes(b"compiler")
    before = svc._snapshot_package_files(pkg)
    shutil.copy2(src, bmodel)
    after = svc._snapshot_package_files(pkg)
    svc._assert_package_files_unchanged(before, after)
    assert bmodel.read_bytes() == b"compiler"


def test_patch_running_info_writes_publish_slugs_not_template_labels(tmp_path):
    path = tmp_path / "running_info.yml"
    path.write_text(
        "accuracy_list:\n"
        "  - - tpl_label_a\n"
        "    - 0.111\n"
        "  - - tpl_label_b\n"
        "    - 0.222\n"
        "epoch: 1.0\n"
        "mAP: 0.1\n"
        "res_l_count: 10\n"
        "resolution: 320x320\n",
        encoding="utf-8",
    )

    class Row:
        def __init__(self, index, label, test_recall):
            self.index = index
            self.label = label
            self.test_recall = test_recall

    rows = [Row(0, "train_a", 0.91), Row(1, "train_b", 0.82)]
    svc._patch_running_info_yml_in_place(
        path,
        classes=["train_a", "train_b"],
        class_metrics_rows=rows,
        epoch=50.0,
        map_val=0.44,
        res_l_count=100,
        imgsz=640,
    )
    text = path.read_text(encoding="utf-8")
    assert "train_a" in text
    assert "train_b" in text
    assert "tpl_label_a" not in text
    assert "  - 0.91" in text
    assert "epoch: 50.0" in text
    assert "mAP: 0.44" in text
    assert "resolution: 640x640" in text


def test_build_bmodel_options_orders_int8_first():
    names = [
        "BM1688/yolov11n_fp32_1b.bmodel",
        "BM1688/yolov11n_int8_1b.bmodel",
        "BM1688/yolov11n_fp16_1b.bmodel",
    ]
    opts = svc._build_bmodel_options(names)
    assert [o.precision for o in opts] == ["int8", "fp16", "fp32"]
    assert opts[0].recommended is True
    assert opts[0].eval_gate_required is False
    assert opts[1].eval_gate_required is False


def test_publish_bmodel_options_for_standard_yolov11_exposes_all_precisions():
    job = type("Job", (), {"job_type": "yolo_detection"})()
    names = [
        "BM1688/yolov11n_fp16_1b.bmodel",
        "BM1688/yolov11n_int8_1b.bmodel",
        "BM1688/yolov11n_fp32_1b.bmodel",
    ]

    options = svc._publish_bmodel_options_for_job(job, names)

    assert [option.filename for option in options] == [
        "yolov11n_int8_1b.bmodel",
        "yolov11n_fp16_1b.bmodel",
        "yolov11n_fp32_1b.bmodel",
    ]


def test_publish_bmodel_options_fall_back_to_validated_fp16(tmp_path, monkeypatch):
    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", str(tmp_path))
    export_id = 91
    export_root = tmp_path / svc.settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id)
    server_eval = export_root / "server_eval"
    server_eval.mkdir(parents=True)
    (server_eval / "sop_eval_summary.json").write_text(
        json.dumps(
            {
                "pt": {"metrics": {"map50": 0.747}},
                "comparison_pt_bmodel": {
                    "pt_map50": 0.747,
                    "bmodel_map50": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )
    manual_results = (
        export_root
        / "manual_edge_validation"
        / "job-35"
        / "server_eval"
        / "results_bmodel"
    )
    manual_results.mkdir(parents=True)
    (manual_results / "eval_summary.json").write_text(
        json.dumps({"map50": 0.0}), encoding="utf-8"
    )
    for precision, map50 in (("fp16", 0.7445), ("fp32", 0.70)):
        variant_dir = manual_results / "variants" / precision
        variant_dir.mkdir(parents=True)
        (variant_dir / "eval_summary.json").write_text(
            json.dumps({"map50": map50}), encoding="utf-8"
        )

    job = type("Job", (), {"job_type": "yolo_detection"})()
    options = svc._publish_bmodel_options_for_job(
        job,
        [
            "BM1688/yolov11n_int8_1b.bmodel",
            "BM1688/yolov11n_fp16_1b.bmodel",
            "BM1688/yolov11n_fp32_1b.bmodel",
        ],
    )
    prepared = svc._prepare_publish_bmodel_options(
        job,
        options,
        export_id=export_id,
        quality_policy_snapshot={
            "edge_usability": {"max_pt_to_bmodel_map50_drop": 0.02}
        },
    )

    by_precision = {option.precision: option for option in prepared}
    assert by_precision["int8"].validation_status == "exceeded"
    assert by_precision["int8"].publishable is False
    assert by_precision["fp16"].validation_status == "pass"
    assert by_precision["fp16"].publishable is True
    assert by_precision["fp16"].recommended is True
    assert by_precision["fp16"].drop_map50 == pytest.approx(0.0025)
    assert by_precision["fp32"].validation_status == "exceeded"
    assert by_precision["fp32"].publishable is False


def test_manual_publish_bmodel_options_block_quality_risks(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", str(tmp_path))
    export_id = 92
    server_eval = (
        tmp_path
        / svc.settings.YOLO_BM1688_EXPORT_SUBDIR
        / str(export_id)
        / "server_eval"
    )
    server_eval.mkdir(parents=True)
    (server_eval / "sop_eval_summary.json").write_text(
        json.dumps(
            {
                "comparison_pt_bmodel": {
                    "pt_map50": 0.80,
                    "bmodel_map50": 0.70,
                }
            }
        ),
        encoding="utf-8",
    )
    job = type("Job", (), {"job_type": "yolo_detection"})()
    options = svc._publish_bmodel_options_for_job(
        job, ["BM1688/yolov11n_int8_1b.bmodel"]
    )

    prepared = svc._prepare_publish_bmodel_options(
        job,
        options,
        export_id=export_id,
        quality_policy_snapshot={
            "edge_usability": {"max_pt_to_bmodel_map50_drop": 0.02}
        },
        allow_quality_risk=True,
    )

    assert prepared[0].validation_status == "exceeded"
    assert prepared[0].publishable is False
    assert prepared[0].recommended is False
    assert prepared[0].blocked_reason
    assert "0.0200" in prepared[0].quality_warning


def test_manual_publish_recommends_passed_fp16_over_review_int8(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", str(tmp_path))
    job = type("Job", (), {"job_type": "yolo_detection"})()
    options = svc._publish_bmodel_options_for_job(
        job,
        [
            "BM1688/yolov11n_int8_1b.bmodel",
            "BM1688/yolov11n_fp16_1b.bmodel",
            "BM1688/yolov11n_fp32_1b.bmodel",
        ],
    )
    evidence = {
        "by_precision": {
            "int8": {"pt_map50": 0.884, "bmodel_map50": 0.870},
            "fp16": {"pt_map50": 0.884, "bmodel_map50": 0.8842},
            "fp32": {"pt_map50": 0.884, "bmodel_map50": 0.8842},
        }
    }

    prepared = svc._prepare_publish_bmodel_options(
        job,
        options,
        export_id=175,
        quality_policy_snapshot={
            "edge_usability": {
                "max_pt_to_bmodel_map50_drop": 0.02,
            }
        },
        evidence_comparison=evidence,
        allow_quality_risk=True,
    )

    by_precision = {option.precision: option for option in prepared}
    assert by_precision["int8"].validation_status == "review"
    assert by_precision["int8"].publishable is True
    assert by_precision["int8"].recommended is False
    assert by_precision["int8"].blocked_reason is None
    assert by_precision["int8"].quality_warning
    assert by_precision["fp16"].validation_status == "pass"
    assert by_precision["fp16"].recommended is True
    assert by_precision["fp32"].validation_status == "pass"
    assert by_precision["fp32"].recommended is False


def test_edge_precision_evidence_overrides_stale_server_comparison(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", str(tmp_path))
    export_id = 93
    server_eval = (
        tmp_path
        / svc.settings.YOLO_BM1688_EXPORT_SUBDIR
        / str(export_id)
        / "server_eval"
    )
    server_eval.mkdir(parents=True)
    (server_eval / "sop_eval_summary.json").write_text(
        json.dumps(
            {
                "comparison_pt_bmodel": {
                    "pt_map50": 0.8492,
                    "bmodel_map50": 0.87,
                }
            }
        ),
        encoding="utf-8",
    )

    state, comparison, _ = svc._bmodel_eval_gate_result(
        export_id,
        precision="int8",
        evidence_comparison={
            "by_precision": {
                "int8": {
                    "pt_map50": 0.884,
                    "bmodel_map50": 0.87,
                }
            }
        },
    )

    assert state == "review"
    assert comparison["pt_map50"] == pytest.approx(0.884)
    assert comparison["bmodel_map50"] == pytest.approx(0.87)
    assert comparison["drop_map50"] == pytest.approx(0.014)


def test_publish_bmodel_options_read_direct_manual_fp16_result_for_plate_detection(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", str(tmp_path))
    export_id = 174
    export_root = tmp_path / svc.settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id)
    (export_root / "server_eval").mkdir(parents=True)
    (export_root / "server_eval" / "sop_eval_summary.json").write_text(
        json.dumps({"pt": {"metrics": {"map50": 0.9987}}}),
        encoding="utf-8",
    )
    model_dir = export_root / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    (model_dir / "plate_detection_fp16_1b.bmodel").write_bytes(b"bmodel")
    manual_results = (
        export_root
        / "manual_edge_validation"
        / "job-12619"
        / "server_eval"
        / "results_bmodel"
    )
    manual_results.mkdir(parents=True)
    (manual_results / "eval_summary.json").write_text(
        json.dumps(
            {
                "map50": 0.999,
            }
        ),
        encoding="utf-8",
    )

    job = type("Job", (), {"job_type": "plate_detection"})()
    options = svc._publish_bmodel_options_for_job(
        job, ["BM1688/plate_detection_fp16_1b.bmodel"]
    )
    prepared = svc._prepare_publish_bmodel_options(
        job,
        options,
        export_id=export_id,
        quality_policy_snapshot={
            "edge_usability": {"max_pt_to_bmodel_map50_drop": 0.02}
        },
    )

    assert prepared[0].validation_status == "pass"
    assert prepared[0].publishable is True
    assert prepared[0].pt_map50 == pytest.approx(0.9987)
    assert prepared[0].bmodel_map50 == pytest.approx(0.999)


def test_publish_bmodel_options_use_completed_edge_comparison_when_server_pt_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", str(tmp_path))
    export_id = 174
    export_root = tmp_path / svc.settings.YOLO_BM1688_EXPORT_SUBDIR / str(export_id)
    server_eval = export_root / "server_eval"
    server_eval.mkdir(parents=True)
    (server_eval / "sop_eval_summary.json").write_text(
        json.dumps({"pt": {}, "bmodels": {}}),
        encoding="utf-8",
    )

    job = type("Job", (), {"job_type": "plate_detection"})()
    options = svc._publish_bmodel_options_for_job(
        job, ["BM1688/plate_detection_fp16_1b.bmodel"]
    )
    prepared = svc._prepare_publish_bmodel_options(
        job,
        options,
        export_id=export_id,
        quality_policy_snapshot={
            "edge_usability": {"max_pt_to_bmodel_map50_drop": 0.02}
        },
        evidence_comparison={
            "precision": "fp16",
            "pt_map50": 0.9987,
            "bmodel_map50": 0.999,
            "drop_map50": 0.0,
            "status": "ok",
            "verdict": "pass",
        },
    )

    assert prepared[0].validation_status == "pass"
    assert prepared[0].publishable is True
    assert prepared[0].pt_map50 == pytest.approx(0.9987)
    assert prepared[0].bmodel_map50 == pytest.approx(0.999)
    assert prepared[0].drop_map50 == pytest.approx(0.0)


def test_publish_bmodel_options_for_plate_keeps_only_combined_fp16_model():
    job = type("Job", (), {"job_type": "plate_recognition"})()
    names = [
        "BM1688/plate_color_fp16_1b.bmodel",
        "BM1688/plate_recognition_fp16_1b.bmodel",
        "BM1688/yolov11n_int8_1b.bmodel",
    ]

    options = svc._publish_bmodel_options_for_job(job, names)

    assert [option.filename for option in options] == [
        "plate_recognition_fp16_1b.bmodel"
    ]


def test_publish_bmodel_options_for_plate_detection_keeps_only_pose_fp16_model():
    job = type("Job", (), {"job_type": "plate_detection"})()
    names = [
        "BM1688/plate_detection_fp16_1b.bmodel",
        "BM1688/plate_recognition_fp16_1b.bmodel",
        "BM1688/yolov11n_int8_1b.bmodel",
    ]

    options = svc._publish_bmodel_options_for_job(job, names)

    assert [option.filename for option in options] == [
        "plate_detection_fp16_1b.bmodel"
    ]
    assert svc._normalize_bmodel_selection(
        names,
        precision="fp16",
        required_filename="plate_detection_fp16_1b.bmodel",
    ) == "BM1688/plate_detection_fp16_1b.bmodel"
    with pytest.raises(Exception, match="只能选择 plate_detection_fp16_1b.bmodel"):
        svc._normalize_bmodel_selection(
            names,
            rel_path="BM1688/plate_recognition_fp16_1b.bmodel",
            required_filename="plate_detection_fp16_1b.bmodel",
        )


def test_normalize_bmodel_selection_by_precision():
    names = [
        "BM1688/yolov11n_fp32_1b.bmodel",
        "BM1688/yolov11n_int8_1b.bmodel",
    ]
    assert (
        svc._normalize_bmodel_selection(names, precision="int8")
        == "BM1688/yolov11n_int8_1b.bmodel"
    )


def test_normalize_bmodel_selection_prefers_combined_plate_model():
    names = [
        "BM1688/plate_color_fp16_1b.bmodel",
        "BM1688/plate_ocr_fp16_1b.bmodel",
        "BM1688/plate_recognition_fp16_1b.bmodel",
        "BM1688/plate_type_fp16_1b.bmodel",
    ]

    assert (
        svc._normalize_bmodel_selection(names, precision="fp16")
        == "BM1688/plate_recognition_fp16_1b.bmodel"
    )


def test_normalize_bmodel_selection_by_rel_path_compat():
    names = ["BM1688/yolov11n_fp16_1b.bmodel"]
    assert (
        svc._normalize_bmodel_selection(names, rel_path="yolov11n_fp16_1b.bmodel")
        == "BM1688/yolov11n_fp16_1b.bmodel"
    )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside/yolov11n_int8_1b.bmodel",
        "/outside/yolov11n_int8_1b.bmodel",
        "C:/models/yolov11n_int8_1b.bmodel",
    ],
)
def test_normalize_bmodel_selection_rejects_unsafe_discovered_paths(unsafe_path):
    with pytest.raises(svc.LabelUException) as exc_info:
        svc._normalize_bmodel_selection([unsafe_path], precision="int8")

    assert "安全相对路径" in str(exc_info.value.msg or exc_info.value)


def test_pick_newest_model_set_by_id():
    rows = [
        {"id": 10, "created_at": "2026-06-01"},
        {"id": 473, "created_at": "2026-01-01"},
        {"id": 20, "created_at": "2026-06-02"},
    ]
    picked = svc._pick_newest_model_set(rows)
    assert picked["id"] == 473


def test_publish_block_reason_from_edge_validation_completed_is_none():
    edge_validation = type("EdgeValidation", (), {"status": "COMPLETED"})()
    assert svc._publish_block_reason_from_edge_validation(edge_validation) is None


def test_publish_block_reason_from_edge_validation_processing():
    edge_validation = type("EdgeValidation", (), {"status": "PROCESSING"})()
    reason = svc._publish_block_reason_from_edge_validation(edge_validation)
    assert "PROCESSING" in reason


def _write_template_zip(path: Path, *, alg: str, with_ini: bool = False) -> None:
    out = path.parent / "_out"
    if out.exists():
        shutil.rmtree(out)
    (out / "output_rknn").mkdir(parents=True)
    (out / "output_rknn" / "video.json").write_text(
        json.dumps(
            {
                "rknn": {
                    "ModelAlgType": alg,
                    "ModelPath": "./test.bmodel",
                    "InputSize": 640,
                }
            }
        ),
        encoding="utf-8",
    )
    (out / "output_rknn" / "labels.txt").write_text("test\na\n", encoding="utf-8")
    (out / "running_info.yml").write_text("accuracy_list: []\n", encoding="utf-8")
    if with_ini:
        (out / "output_rknn" / "Config416.ini").write_text(
            "[modelInfo]\nnet_width=416\n", encoding="utf-8"
        )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in out.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(out).as_posix())
    shutil.rmtree(out)


def test_template_zip_is_yolov11_format_true(tmp_path):
    zp = tmp_path / "y11.zip"
    _write_template_zip(zp, alg="yoloV11")
    assert svc._template_zip_is_yolov11_format(zp) is True


def test_template_zip_is_yolov11_format_rejects_yolov4_ini(tmp_path):
    zp = tmp_path / "y4.zip"
    _write_template_zip(zp, alg="yolo", with_ini=True)
    assert svc._template_zip_is_yolov11_format(zp) is False


def test_model_name_matches_preset_exact_and_typo():
    assert svc._model_name_matches_preset("占道经营_bm1688_v7", "占道经营") is True
    assert svc._model_name_matches_preset("占到经营_bm1688_v13", "占道经营") is True
    assert svc._model_name_matches_preset("垃圾暴露_bm1688_v18", "占道经营") is False


def test_merge_model_set_rows_dedupes_and_sorts():
    rows = svc._merge_model_set_rows(
        [{"id": 422, "name": "a"}, {"id": 421, "name": "b"}],
        [{"id": 522, "name": "c"}, {"id": 422, "name": "dup"}],
    )
    assert [r["id"] for r in rows] == [522, 422, 421]


def test_plate_runtime_template_requires_profile_and_four_networks(tmp_path):
    pkg_root = tmp_path / "package"
    out_rknn = pkg_root / "output_rknn"
    out_rknn.mkdir(parents=True)
    contract = out_rknn / "plate_recognition_contract.json"
    contract.write_text(
        json.dumps(
            {
                "format": "labelu_plate_recognition_v1",
                "networks": {
                    "plate_pose": {},
                    "plate_ocr": {},
                    "plate_color": {},
                    "plate_type": {},
                },
            }
        ),
        encoding="utf-8",
    )

    assert svc._plate_runtime_contract_path(pkg_root, out_rknn) == contract

    contract.write_text(
        json.dumps({"format": "labelu_plate_recognition_v1", "networks": {}}),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="不支持复合车牌运行时"):
        svc._plate_runtime_contract_path(pkg_root, out_rknn)


def test_plate_detection_runtime_contract_requires_single_pose_and_four_points(
    tmp_path,
):
    pkg_root = tmp_path / "package"
    out_rknn = pkg_root / "output_rknn"
    out_rknn.mkdir(parents=True)
    contract = out_rknn / "plate_detection_contract.json"
    contract.write_text(
        json.dumps(
            {
                "format": "labelu_plate_detection_bm1688_v1",
                "job_type": "plate_detection",
                "network": {
                    "name": "plate_detection",
                    "keypoint_order": [
                        "top_left",
                        "top_right",
                        "bottom_right",
                        "bottom_left",
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    assert (
        svc._plate_runtime_contract_path(
            pkg_root, out_rknn, "plate_detection"
        )
        == contract
    )
    assert svc._validate_plate_runtime_contract(contract, "plate_detection")
    contract.write_text(
        json.dumps(
            {
                "format": "labelu_plate_detection_bm1688_v1",
                "job_type": "plate_detection",
                "network": {
                    "name": "plate_detection",
                    "keypoint_order": ["top_left", "top_right"],
                },
            }
        ),
        encoding="utf-8",
    )
    assert not svc._validate_plate_runtime_contract(contract, "plate_detection")


def test_patch_plate_publish_contracts(tmp_path):
    video = tmp_path / "video.json"
    video.write_text(
        json.dumps({"rknn": {"ModelPath": "./model.bmodel", "NumClass": 9}}),
        encoding="utf-8",
    )
    running_info = tmp_path / "running_info.yml"
    running_info.write_text("accuracy_list:\n  - doubleEP\n  - 0.0\n", encoding="utf-8")

    svc._patch_plate_video_json(video, model_rel_path="./plate.bmodel")
    svc._patch_plate_running_info(running_info)

    payload = json.loads(video.read_text(encoding="utf-8"))
    assert payload["rknn"]["ModelAlgType"] == "plateRecognitionV1"
    assert payload["rknn"]["ModelPath"] == "./plate.bmodel"
    assert [item["name"] for item in payload["rknn"]["Networks"]] == [
        "plate_pose",
        "plate_ocr",
        "plate_color",
        "plate_type",
    ]
    text = running_info.read_text(encoding="utf-8")
    assert "training_profile: plate_recognition_v1" in text
    assert "  - plate_ocr" in text


def test_patch_plate_detection_publish_contracts_are_pose_only(tmp_path):
    video = tmp_path / "video.json"
    video.write_text(
        json.dumps(
            {
                "rknn": {
                    "ModelPath": "./model.bmodel",
                    "NumClass": 9,
                    "Networks": [{"name": "plate_ocr"}],
                }
            }
        ),
        encoding="utf-8",
    )
    running_info = tmp_path / "running_info.yml"
    running_info.write_text(
        "accuracy_list:\n  - doubleEP\n  - 0.0\n"
        "# labelu_plate_recognition_begin\n"
        "runtime_networks:\n  - plate_ocr\n"
        "# labelu_plate_recognition_end\n",
        encoding="utf-8",
    )

    svc._patch_plate_detection_video_json(video, model_rel_path="./plate.bmodel")
    svc._patch_plate_detection_running_info(running_info)

    payload = json.loads(video.read_text(encoding="utf-8"))
    assert payload["rknn"]["ModelFunction"] == "plateDetectionV1"
    assert payload["rknn"]["ModelAlgType"] == "plateDetectionV1"
    assert payload["rknn"]["ModelPath"] == "./plate.bmodel"
    assert [item["name"] for item in payload["rknn"]["Networks"]] == [
        "plate_detection"
    ]
    assert payload["rknn"]["Pipeline"] == ["plate_detection"]
    text = running_info.read_text(encoding="utf-8")
    assert "training_profile: plate_detection_v1" in text
    assert "  - plate_detection" in text
    assert "plate_ocr" not in text
    assert "labelu_plate_recognition_begin" not in text


def test_build_plate_detection_publish_package_replaces_composite_contract(
    tmp_path, monkeypatch
):
    media_root = tmp_path / "media"
    export_root = media_root / svc.settings.YOLO_BM1688_EXPORT_SUBDIR / "77"
    model_dir = export_root / "models" / "BM1688"
    model_dir.mkdir(parents=True)
    (model_dir / "plate_detection_fp16_1b.bmodel").write_bytes(b"plate-model")
    (export_root / "plate_detection_contract.json").write_text(
        json.dumps(
            {
                "format": "labelu_plate_detection_bm1688_v1",
                "job_type": "plate_detection",
                "network": {
                    "name": "plate_detection",
                    "keypoint_order": [
                        "top_left",
                        "top_right",
                        "bottom_right",
                        "bottom_left",
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    template_dir = tmp_path / "template" / "plate-runtime"
    out_rknn = template_dir / "output_rknn"
    out_rknn.mkdir(parents=True)
    (template_dir / "running_info.yml").write_text(
        "accuracy_list:\n  - doubleEP\n  - 0.1\n",
        encoding="utf-8",
    )
    (out_rknn / "labels.txt").write_text("?????\ndoubleEP\n", encoding="utf-8")
    (out_rknn / "video.json").write_text(
        json.dumps(
            {
                "rknn": {
                    "InputSize": 640,
                    "NumClass": 1,
                    "ModelPath": "./template.bmodel",
                    "ModelChipType": "SOPHGO,BM1688,NPU",
                }
            }
        ),
        encoding="utf-8",
    )
    (out_rknn / "template.bmodel").write_bytes(b"template")
    (out_rknn / "plate_recognition_contract.json").write_text(
        json.dumps(
            {
                "format": "labelu_plate_recognition_v1",
                "networks": {
                    "plate_pose": {},
                    "plate_ocr": {},
                    "plate_color": {},
                    "plate_type": {},
                },
            }
        ),
        encoding="utf-8",
    )
    template_zip = tmp_path / "template.zip"
    with zipfile.ZipFile(template_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in template_dir.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(template_dir.parent).as_posix())

    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", str(media_root))
    monkeypatch.setattr(
        svc,
        "_create_publish_work_dir",
        lambda _export_id: tmp_path / "publish-work",
    )
    monkeypatch.setattr(
        svc.crud_yolo_bm1688_export,
        "get",
        lambda _db, _export_id: type("Export", (), {"imgsz": 640})(),
    )
    monkeypatch.setattr(
        svc.bm_export_service,
        "_list_bmodel_names",
        lambda _export_id: ["BM1688/plate_detection_fp16_1b.bmodel"],
    )
    monkeypatch.setattr(
        svc.bm_export_service,
        "_export_dir_for_row",
        lambda _export_id: export_root,
    )
    monkeypatch.setattr(
        svc,
        "_resolve_publish_class_slugs",
        lambda _db, *, raw_classes, preset_slug, task: list(raw_classes),
    )
    monkeypatch.setattr(svc, "_aggregate_map_for_publish", lambda _job, _tm: 0.0)
    monkeypatch.setattr(svc, "_sample_counts_for_job", lambda _job: {"total": 1})
    monkeypatch.setattr(svc, "_class_metrics_for_job", lambda _job: None)
    monkeypatch.setattr(svc, "actual_epochs_for_train_job", lambda **_kwargs: 1)
    job = type(
        "Job",
        (),
        {
            "id": 77,
            "job_type": "plate_detection",
            "imgsz": 640,
            "epochs": 1,
            "weights_path": None,
            "metrics_summary": None,
        },
    )()
    package = svc._build_publish_package(
        template_zip,
        export_id=77,
        job=job,
        db=object(),
        task=type("Task", (), {"name": "plate"})(),
        preset_name="plate_detection_v1",
        preset_slug="",
        steps=[],
        chip_type="SOPHGO,BM1688,NPU",
    )

    with zipfile.ZipFile(package, "r") as archive:
        names = set(archive.namelist())
        video = json.loads(archive.read("output_rknn/video.json"))
        assert "output_rknn/plate_detection_contract.json" in names
        assert "output_rknn/plate_recognition_contract.json" not in names
        assert video["rknn"]["ModelAlgType"] == "plateDetectionV1"
        assert [item["name"] for item in video["rknn"]["Networks"]] == [
            "plate_detection"
        ]
        assert "output_rknn/plate_detection_fp16_1b.bmodel" in names


def test_plate_publish_class_is_double_ep():
    job = type("Job", (), {"job_type": "plate_recognition"})()
    assert svc._class_names_with_source_for_publish(job) == (
        ["doubleEP"],
        "plate_recognition_v1",
    )


def test_plate_detection_publish_class_is_double_ep():
    job = type("Job", (), {"job_type": "plate_detection"})()
    assert svc._class_names_with_source_for_publish(job) == (
        ["doubleEP"],
        "plate_detection_v1",
    )


def test_publish_classes_prefer_frozen_contract_and_verify_yaml(monkeypatch, tmp_path):
    job = type(
        "Job",
        (),
        {
            "id": 701,
            "job_type": "yolo_detection",
            "class_names_json": json.dumps(["person", "car"]),
            "class_order_signature": class_order_signature(["person", "car"]),
            "metrics_summary": None,
        },
    )()
    monkeypatch.setattr(svc, "_job_work_root", lambda _job_id: tmp_path)
    monkeypatch.setattr(
        svc,
        "read_class_names_from_data_yaml",
        lambda _path: ["person", "car"],
    )

    assert svc._class_names_with_source_for_publish(job) == (
        ["person", "car"],
        "yolo_train_job.class_names_json",
    )

    monkeypatch.setattr(
        svc,
        "read_class_names_from_data_yaml",
        lambda _path: ["car", "person"],
    )
    with pytest.raises(Exception, match="data.yaml 类别顺序"):
        svc._class_names_with_source_for_publish(job)


def test_publish_classes_allow_history_fallback_without_frozen_contract(
    monkeypatch, tmp_path
):
    job = type(
        "Job",
        (),
        {
            "id": 702,
            "job_type": "yolo_detection",
            "class_names_json": None,
            "class_order_signature": None,
            "metrics_summary": json.dumps({"names": ["legacy_person", "legacy_car"]}),
        },
    )()
    monkeypatch.setattr(svc, "_job_work_root", lambda _job_id: tmp_path)
    monkeypatch.setattr(svc, "read_class_names_from_data_yaml", lambda _path: [])
    monkeypatch.setattr(
        svc,
        "read_classes_from_metrics_summary",
        lambda _summary: ["legacy_person", "legacy_car"],
    )

    assert svc._class_names_with_source_for_publish(job) == (
        ["legacy_person", "legacy_car"],
        "metrics_summary",
    )


def test_runtime_template_resolves_exact_environment_before_default(db):
    with begin_transaction(db):
        db.add_all(
            [
                RuntimePublishTemplate(
                    template_key="runtime-default",
                    name="Default runtime",
                    environment_id="default",
                    runtime_profile="yolov11_detection",
                    interface_version=3,
                    aicm_model_set_id="501",
                    capability_contract_json="{}",
                    is_default=True,
                    status="active",
                ),
                RuntimePublishTemplate(
                    template_key="runtime-prod",
                    name="Production runtime",
                    environment_id="prod-aicm",
                    runtime_profile="yolov11_detection",
                    interface_version=3,
                    aicm_model_set_id="777",
                    capability_contract_json="{}",
                    is_default=False,
                    status="active",
                ),
            ]
        )
    job = type(
        "Job",
        (),
        {"runtime_profile": "yolov11_detection", "runtime_interface_version": 3},
    )()

    selected = svc._runtime_template_for_job(db, job=job, environment_id="prod-aicm")
    assert selected.template_key == "runtime-prod"
    assert selected.aicm_model_set_id == "777"
    with pytest.raises(Exception, match="没有新版运行契约"):
        svc._runtime_template_for_job(
            db,
            job=type(
                "LegacyJob",
                (),
                {"runtime_profile": None, "runtime_interface_version": None},
            )(),
            environment_id="prod-aicm",
        )


def test_runtime_template_candidates_use_selected_environment_without_legacy_lookup(
    monkeypatch,
):
    class Session:
        closed = False

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(svc, "find_environment_record", lambda env_id: {"id": env_id})
    monkeypatch.setattr(
        svc,
        "load_aicm_publish_config",
        lambda environment_id: {
            "base_url": "http://aicm.local:8102",
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
        },
    )
    monkeypatch.setattr(svc, "_aicm_session", lambda _cfg: session)
    monkeypatch.setattr(
        svc,
        "_search_model_sets",
        lambda _session, _base, **_kwargs: [
            {
                "id": 530,
                "name": "YOLOv11 detection runtime",
                "model_alg_type": "yoloV11",
                "status": "ready",
                "created_at": "2026-08-06T10:00:00",
            }
        ],
    )

    rows = svc.list_runtime_template_candidates(
        environment_id="env-new",
        q="YOLOv11",
        page=1,
        size=50,
    )

    assert rows[0].model_set_id == 530
    assert rows[0].model_alg_type == "yoloV11"
    assert session.closed is True


def test_runtime_template_candidates_preserve_remote_filtered_total(monkeypatch):
    class Session:
        closed = False

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(svc, "find_environment_record", lambda env_id: {"id": env_id})
    monkeypatch.setattr(
        svc,
        "load_aicm_publish_config",
        lambda environment_id: {
            "base_url": "http://aicm.local:8102",
            "username": "admin",
            "password": "secret",
            "verify_ssl": False,
        },
    )
    monkeypatch.setattr(svc, "_aicm_session", lambda _cfg: session)
    monkeypatch.setattr(
        svc,
        "_search_model_sets",
        lambda *_args, **_kwargs: svc._ModelSetRows(
            [{"id": 530, "name": "page one"}], total=326
        ),
    )

    rows = svc.list_runtime_template_candidates(
        environment_id="env-new", q="page", page=2, size=1
    )

    assert len(rows) == 1
    assert rows.total == 326
    assert session.closed is True


def test_runtime_template_capability_rejects_unsupported_job_type_and_class_count():
    row = RuntimePublishTemplate(
        template_key="limited-runtime",
        name="Limited runtime",
        environment_id="default",
        runtime_profile="yolov11_detection",
        interface_version=1,
        aicm_model_set_id="901",
        capability_contract_json=json.dumps(
            {"supported_job_types": ["yolo_detection"], "max_classes": 1}
        ),
    )
    with pytest.raises(Exception, match="不支持训练作业类型"):
        svc._validate_runtime_template_capability(
            row,
            type("Job", (), {"job_type": "plate_recognition"})(),
            ["doubleEP"],
        )
    with pytest.raises(Exception, match="最多支持 1 个类别"):
        svc._validate_runtime_template_capability(
            row,
            type("Job", (), {"job_type": "yolo_detection"})(),
            ["person", "car"],
        )


def test_runtime_template_download_rejects_sha256_mismatch(monkeypatch, tmp_path):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "video.json", json.dumps({"rknn": {"ModelAlgType": "yoloV11"}})
        )
    content = payload.getvalue()

    class Response:
        status_code = 200

        def iter_content(self, chunk_size):
            yield content

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    row = RuntimePublishTemplate(
        id=902,
        template_key="sha-runtime",
        name="SHA runtime",
        environment_id="default",
        runtime_profile="yolov11_detection",
        interface_version=1,
        aicm_model_set_id="902",
        capability_contract_json="{}",
        template_sha256="0" * 64,
    )
    monkeypatch.setattr(svc, "_templates_root", lambda: tmp_path)
    monkeypatch.setattr(
        svc,
        "_get_download_url",
        lambda *_args, **_kwargs: "http://example/template.zip",
    )

    assert hashlib.sha256(content).hexdigest() != row.template_sha256
    with pytest.raises(Exception, match="SHA256 不一致"):
        svc._ensure_runtime_template_zip(
            Session(),
            "http://example",
            row,
            verify=False,
            steps=[],
        )
