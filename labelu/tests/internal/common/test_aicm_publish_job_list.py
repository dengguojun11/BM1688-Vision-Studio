"""发布任务列表。"""
from labelu.internal.common.aicm_publish_job import create_job, list_jobs


def test_list_jobs_filter_by_user(tmp_path, monkeypatch):
    from labelu.internal.common import aicm_publish_job as mod

    monkeypatch.setattr(mod, "jobs_dir", lambda: tmp_path / "jobs")
    (tmp_path / "jobs").mkdir(parents=True, exist_ok=True)
    create_job(export_id=1, user_id=10, preset_name="a", publisher_username="u10")
    create_job(export_id=2, user_id=20, preset_name="b", publisher_username="u20")

    all_rows, total = list_jobs(page=0, size=10)
    assert total == 2
    u10, _ = list_jobs(user_id=10, page=0, size=10)
    assert len(u10) == 1
    assert u10[0]["user_id"] == 10
