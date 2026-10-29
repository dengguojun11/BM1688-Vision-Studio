import threading
import time
from types import SimpleNamespace

from labelu.internal.application.response.yolo_train import (
    YoloBm1688ExportCatalogSummary,
)
from labelu.internal.application.service import (
    yolo_aicm_publish,
    yolo_bm1688_export,
    yolo_train,
)


def test_edge_validation_catalog_uses_batch_queries(monkeypatch):
    exports = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    board_status = SimpleNamespace(status="COMPLETED", completed_at=None, report=None)
    calls = []

    def latest_board_rows(db, *, export_ids):
        calls.append(("board", list(export_ids)))
        return {1: SimpleNamespace(response=board_status)}

    def latest_manual_rows(db, *, export_ids):
        calls.append(("manual", list(export_ids)))
        return {}

    monkeypatch.setattr(
        yolo_aicm_publish.crud_yolo_board_validation,
        "latest_by_export_ids",
        latest_board_rows,
    )
    monkeypatch.setattr(
        yolo_aicm_publish.crud_yolo_edge_validation_job,
        "latest_by_export_ids",
        latest_manual_rows,
    )
    monkeypatch.setattr(
        yolo_aicm_publish.board_agent,
        "_row_to_status_response",
        lambda row, **kwargs: row.response,
    )
    monkeypatch.setattr(
        yolo_aicm_publish.board_agent,
        "build_board_threshold_contract",
        lambda row: {},
    )

    result = yolo_aicm_publish.edge_validation_statuses_for_exports(None, exports)

    assert result == {1: board_status}
    assert calls == [("board", [1, 2]), ("manual", [1, 2])]


def test_model_catalog_enriches_only_selected_page(monkeypatch):
    sources = [
        (SimpleNamespace(id=1), SimpleNamespace(name="one", config="{}")),
        (SimpleNamespace(id=2), SimpleNamespace(name="two", config="{}")),
        (SimpleNamespace(id=3), SimpleNamespace(name="three", config="{}")),
    ]
    entries = {
        1: {
            "training_status": "failed",
            "test_map50_95": 0.1,
            "generalization_delta": 0.3,
            "sample_count": 10,
            "updated_timestamp": 30.0,
        },
        2: {
            "training_status": "passed",
            "test_map50_95": 0.8,
            "generalization_delta": 0.1,
            "sample_count": 20,
            "updated_timestamp": 20.0,
        },
        3: {
            "training_status": "review_required",
            "test_map50_95": 0.5,
            "generalization_delta": 0.2,
            "sample_count": 30,
            "updated_timestamp": 10.0,
        },
    }
    monkeypatch.setattr(
        yolo_train,
        "_collect_trained_model_sources_global",
        lambda *args, **kwargs: sources,
    )
    monkeypatch.setattr(
        yolo_train, "_quality_policy_snapshots_for_tasks", lambda rows: {}
    )

    def source_entry(job, task, **kwargs):
        return {"job": job, "task": task, **entries[job.id]}

    monkeypatch.setattr(yolo_train, "_trained_model_source_entry", source_entry)
    enriched = []

    def build_responses(db, current_user, rows, **kwargs):
        enriched.extend(job.id for job, _ in rows)
        return [job.id for job, _ in rows]

    monkeypatch.setattr(yolo_train, "_build_trained_model_responses", build_responses)

    data, total, summary = yolo_train.list_trained_model_catalog(
        db=None,
        current_user=SimpleNamespace(id=9),
        page=0,
        size=1,
    )

    assert data == [2]
    assert enriched == [2]
    assert total == 3
    assert summary.training_passed == 1
    assert summary.training_review_required == 1
    assert summary.training_failed == 1


def test_bm1688_catalog_does_not_enrich_rows_used_only_for_summary(monkeypatch):
    exports = [
        SimpleNamespace(id=index, yolo_train_job_id=100 + index)
        for index in range(1, 4)
    ]
    tasks = [SimpleNamespace(name=f"task-{index}") for index in range(1, 4)]
    page_rows = [(exports[0], tasks[0])]
    all_rows = list(zip(exports, tasks))
    calls = []

    def list_for_user(db, user_id, *, page, size, **kwargs):
        calls.append(size)
        return (page_rows if size == 1 else all_rows), 3

    monkeypatch.setattr(
        yolo_bm1688_export.crud_yolo_bm1688_export,
        "list_for_user",
        list_for_user,
    )
    monkeypatch.setattr(
        yolo_bm1688_export.yolo_train_service,
        "_task_ids_for_rect_preset_filter",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(yolo_bm1688_export, "is_task_superuser", lambda user: False)
    monkeypatch.setattr(
        yolo_bm1688_export,
        "_export_catalog_context",
        lambda db, rows: ({}, {}),
    )
    monkeypatch.setattr(
        yolo_bm1688_export,
        "_quality_policy_snapshots_for_export_rows",
        lambda rows: {},
    )
    related_export_ids = []
    monkeypatch.setattr(
        yolo_bm1688_export.crud_strategy_task_integration,
        "get_by_related_export_ids",
        lambda db, *, export_ids: related_export_ids.extend(export_ids) or {},
    )
    monkeypatch.setattr(
        yolo_bm1688_export.cfg_io,
        "load_bm1688_compiler_config",
        lambda: {"bmodel_basename": "model", "compile_platforms": ["bm1688"]},
    )
    enriched = []

    def row_to_response(row, task_name, db, **kwargs):
        enriched.append(row.id)
        return row.id

    monkeypatch.setattr(yolo_bm1688_export, "_export_row_to_response", row_to_response)
    summary = YoloBm1688ExportCatalogSummary(total=3)
    monkeypatch.setattr(
        yolo_bm1688_export,
        "_summarize_export_catalog_rows",
        lambda *args, **kwargs: summary,
    )

    data, total, result_summary = yolo_bm1688_export.list_exports_global(
        db=None,
        current_user=SimpleNamespace(id=9),
        page=0,
        size=1,
    )

    assert calls == [1, 3]
    assert related_export_ids == [1]
    assert data == [1]
    assert enriched == [1]
    assert total == 3
    assert result_summary is summary


def test_bm1688_catalog_summary_merges_same_concurrent_computation():
    started = threading.Event()
    release = threading.Event()
    calls = []
    result = object()

    def compute():
        calls.append(threading.get_ident())
        started.set()
        assert release.wait(timeout=2)
        return result

    outputs = []

    def run():
        outputs.append(
            yolo_bm1688_export._run_export_summary_singleflight(
                ("same-filter",), compute
            )
        )

    first = threading.Thread(target=run)
    second = threading.Thread(target=run)
    first.start()
    assert started.wait(timeout=2)
    second.start()
    time.sleep(0.02)
    release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(calls) == 1
    assert outputs == [result, result]


def test_bm1688_catalog_list_can_skip_full_summary(monkeypatch):
    export = SimpleNamespace(id=11, yolo_train_job_id=111)
    task = SimpleNamespace(name="task", config="{}")
    calls = []

    def list_for_user(db, user_id, *, page, size, **kwargs):
        calls.append((page, size, kwargs.get("summary_projection")))
        return ([(export, task)] if size == 1 else []), 1

    monkeypatch.setattr(
        yolo_bm1688_export.crud_yolo_bm1688_export,
        "list_for_user",
        list_for_user,
    )
    monkeypatch.setattr(
        yolo_bm1688_export.yolo_train_service,
        "_task_ids_for_rect_preset_filter",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(yolo_bm1688_export, "is_task_superuser", lambda user: False)
    monkeypatch.setattr(
        yolo_bm1688_export,
        "_export_catalog_context",
        lambda db, rows: ({}, {}),
    )
    monkeypatch.setattr(
        yolo_bm1688_export,
        "_quality_policy_snapshots_for_export_rows",
        lambda rows: {},
    )
    monkeypatch.setattr(
        yolo_bm1688_export.crud_strategy_task_integration,
        "get_by_related_export_ids",
        lambda db, *, export_ids: {},
    )
    monkeypatch.setattr(
        yolo_bm1688_export.cfg_io,
        "load_bm1688_compiler_config",
        lambda: {"bmodel_basename": "model", "compile_platforms": ["bm1688"]},
    )
    monkeypatch.setattr(
        yolo_bm1688_export,
        "_export_row_to_response",
        lambda row, task_name, db, **kwargs: row.id,
    )
    monkeypatch.setattr(
        yolo_bm1688_export,
        "get_exports_summary_global",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("列表请求不应计算全量摘要")
        ),
    )

    data, total, summary = yolo_bm1688_export.list_exports_global(
        db=None,
        current_user=SimpleNamespace(id=9),
        page=0,
        size=1,
        include_summary=False,
    )

    assert data == [11]
    assert total == 1
    assert summary is None
    assert calls == [(0, 1, None)]


def test_bm1688_catalog_summary_scans_in_bounded_chunks(monkeypatch):
    rows = [
        (SimpleNamespace(id=index, yolo_train_job_id=1000 + index), SimpleNamespace(config="{}"))
        for index in range(1001)
    ]
    calls = []

    def list_for_user(db, user_id, *, page, size, **kwargs):
        calls.append((page, size, kwargs.get("include_total"), kwargs.get("summary_projection")))
        if kwargs.get("include_total", True):
            return rows[:1], len(rows)
        start = page * size
        return rows[start : start + size], 0

    monkeypatch.setattr(
        yolo_bm1688_export.crud_yolo_bm1688_export,
        "list_for_user",
        list_for_user,
    )
    monkeypatch.setattr(
        yolo_bm1688_export.yolo_train_service,
        "_task_ids_for_rect_preset_filter",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(yolo_bm1688_export, "is_task_superuser", lambda user: False)
    monkeypatch.setattr(
        yolo_bm1688_export,
        "_export_catalog_context",
        lambda db, chunk: ({}, {}),
    )
    monkeypatch.setattr(
        yolo_bm1688_export,
        "_quality_policy_snapshots_for_export_rows",
        lambda chunk: {},
    )

    def summarize(chunk, total, **kwargs):
        return YoloBm1688ExportCatalogSummary(
            total=total,
            running=len(chunk),
        )

    monkeypatch.setattr(yolo_bm1688_export, "_summarize_export_catalog_rows", summarize)
    summary = yolo_bm1688_export.get_exports_summary_global(
        db=None,
        current_user=SimpleNamespace(id=9),
    )

    assert summary.total == 1001
    assert summary.running == 1001
    assert calls == [
        (0, 1, None, None),
        (0, 500, False, True),
        (1, 500, False, True),
        (2, 500, False, True),
    ]
