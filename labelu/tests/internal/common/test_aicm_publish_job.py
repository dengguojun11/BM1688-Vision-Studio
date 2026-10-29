"""AICM 发布任务 JSON 存储与进度跟踪。"""
import json

from labelu.internal.common.aicm_publish_job import (
    PublishJobTracker,
    create_job,
    load_job,
    save_job,
)


def test_create_and_load_job(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    monkeypatch.setattr(mod, "jobs_dir", lambda: tmp_path / "jobs")

    job_id = create_job(export_id=42, user_id=7, preset_name="测试预设")
    raw = load_job(job_id)
    assert raw is not None
    assert raw["export_id"] == 42
    assert raw["user_id"] == 7
    assert raw["status"] == "PROCESSING"
    assert len(raw["steps"]) == 9
    assert raw["steps"][0]["key"] == "validate"


def test_publish_job_persists_runtime_template_audit_snapshot(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    monkeypatch.setattr(mod, "jobs_dir", lambda: tmp_path / "jobs")
    job_id = create_job(
        export_id=43,
        user_id=7,
        class_names=["person", "vehicle"],
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
        publish_template_key="yolov11_detection",
        publish_template_id=17,
        publish_template_version=3,
        publish_template_sha256="c" * 64,
        publish_template_model_set_id=701,
    )

    raw = load_job(job_id)
    assert raw["class_names"] == ["person", "vehicle"]
    assert raw["publish_template_key"] == "yolov11_detection"
    assert raw["publish_template_id"] == 17
    assert raw["publish_template_version"] == 3
    assert raw["publish_template_sha256"] == "c" * 64
    assert raw["publish_template_model_set_id"] == 701

    tracker = PublishJobTracker(job_id)
    tracker.update_metadata(publish_template_sha256="d" * 64)
    assert load_job(job_id)["publish_template_sha256"] == "d" * 64


def test_publish_job_persists_worker_execution_payload(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    monkeypatch.setattr(mod, "jobs_dir", lambda: tmp_path / "jobs")
    job_id = create_job(
        export_id=44,
        user_id=7,
        keep_template_cache=True,
        bmodel_precision="INT8",
        bmodel_rel_path="exports/int8.bmodel",
        publish_lease_owner="web:request",
        publish_fencing_token=None,
    )
    raw = load_job(job_id)
    assert raw["keep_template_cache"] is True
    assert raw["bmodel_precision"] == "int8"
    assert raw["bmodel_rel_path"] == "exports/int8.bmodel"
    assert raw["publish_lease_owner"] == "web:request"


def test_publish_job_tracker_lifecycle(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    monkeypatch.setattr(mod, "jobs_dir", lambda: tmp_path / "jobs")
    job_id = create_job(export_id=1, user_id=2, preset_name="preset")

    tracker = PublishJobTracker(job_id)
    tracker.start("validate", "checking")
    tracker.ok("validate", "ok")
    tracker.start("login", "http://example")
    tracker.ok("login")
    tracker.complete({"ok": True, "export_id": 1, "steps": [], "rect_preset_name": "p"})

    raw = load_job(job_id)
    assert raw["status"] == "COMPLETED"
    assert raw["result"]["ok"] is True
    finish = [s for s in raw["steps"] if s["key"] == "finish"][0]
    assert finish["status"] == "ok"


def test_publish_job_tracker_fail(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    monkeypatch.setattr(mod, "jobs_dir", lambda: tmp_path / "jobs")
    job_id = create_job(export_id=3, user_id=1, preset_name="x")
    tracker = PublishJobTracker(job_id)
    tracker.start("login")
    tracker.fail("登录失败", failed_step="login")

    raw = load_job(job_id)
    assert raw["status"] == "FAILED"
    login = [s for s in raw["steps"] if s["key"] == "login"][0]
    assert login["status"] == "error"


def test_save_job_persists(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    d = tmp_path / "jobs"
    monkeypatch.setattr(mod, "jobs_dir", lambda: d)
    job_id = "abc123"
    save_job(job_id, {"job_id": job_id, "status": "PROCESSING", "steps": []})
    p = d / "abc123" / "job.json"
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["job_id"] == job_id


def test_load_legacy_flat_json_migrates_to_job_dir(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    d = tmp_path / "jobs"
    d.mkdir(parents=True)
    monkeypatch.setattr(mod, "jobs_dir", lambda: d)
    legacy = d / "legacy01.json"
    legacy.write_text(
        json.dumps({"job_id": "legacy01", "status": "COMPLETED", "user_id": 1}),
        encoding="utf-8",
    )
    raw = load_job("legacy01")
    assert raw is not None
    assert raw["job_id"] == "legacy01"
    assert (d / "legacy01" / "job.json").is_file()
    assert not legacy.is_file()


def test_publish_tracker_writes_process_log_in_job_dir(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    monkeypatch.setattr(mod, "jobs_dir", lambda: tmp_path / "jobs")
    job_id = create_job(export_id=9, user_id=1, preset_name="p")
    tracker = PublishJobTracker(job_id)
    tracker.log("hello from publish")
    tracker._close_file_log()

    log_path = tmp_path / "jobs" / job_id / "process.log"
    assert log_path.is_file()
    assert "hello from publish" in log_path.read_text(encoding="utf-8")
    raw = load_job(job_id)
    assert raw["process_log_path"] == str(log_path.resolve())
