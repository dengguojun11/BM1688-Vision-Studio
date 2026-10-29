from loguru import logger

from labelu.internal.common import business_log
from labelu.internal.common.logger import _COMMON_EXTRA_KEYS, _format_file_log_record, _patch_record_with_common_extra


def test_biz_log_provides_task_related_extra(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(business_log.settings, "LOG_BUSINESS_DETAIL", True)

    logger.configure(
        handlers=[
            {
                "sink": lines.append,
                "format": "{extra[request_id]}|{extra[user_id]}|{extra[task_id]}|{extra[job_id]}|{message}",
            }
        ],
        extra={
            "request_id": "-",
            "user_id": "-",
            "task_id": "-",
            "sample_id": "-",
            "job_id": "-",
            "export_id": "-",
        },
    )

    business_log.set_request_context(request_id="rid-1", user_id=7)
    business_log.biz_log("train.start", task_id=9, job_id=3)
    business_log.biz_log("train.poll")

    assert any("rid-1|7|9|3|[biz] train.start task_id=9 job_id=3" in line for line in lines)
    assert any("rid-1|7|-|-|[biz] train.poll" in line for line in lines)


def test_loguru_patcher_supports_top_level_task_id_format():
    lines: list[str] = []
    logger.configure(
        handlers=[
            {
                "sink": lines.append,
                "format": "{task_id}|{job_id}|{message}",
            }
        ],
        patcher=_patch_record_with_common_extra,
        extra={
            "request_id": "-",
            "user_id": "-",
            "task_id": 11,
            "sample_id": "-",
            "job_id": 22,
            "export_id": "-",
        },
    )

    logger.info("hello")

    assert any("11|22|hello" in line for line in lines)


def test_biz_log_provides_file_path_extra(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(business_log.settings, "LOG_BUSINESS_DETAIL", True)

    logger.configure(
        handlers=[
            {
                "sink": lines.append,
                "format": "{file_path}|{message}",
            }
        ],
        patcher=_patch_record_with_common_extra,
        extra={
            "request_id": "-",
            "user_id": "-",
            "task_id": "-",
            "sample_id": "-",
            "job_id": "-",
            "export_id": "-",
            "file_path": "-",
        },
    )

    business_log.biz_log(
        "attachment.download",
        file_path="upload/1/cp_1186_demo-thumbnail.jpg",
    )

    assert any(
        "upload/1/cp_1186_demo-thumbnail.jpg|[biz] attachment.download"
        in line
        for line in lines
    )


def test_biz_log_provides_device_id_extra(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(business_log.settings, "LOG_BUSINESS_DETAIL", True)

    logger.configure(
        handlers=[
            {
                "sink": lines.append,
                "format": "{extra[device_id]}|{message}",
            }
        ],
        patcher=_patch_record_with_common_extra,
        extra={
            "request_id": "-",
            "user_id": "-",
            "task_id": "-",
            "sample_id": "-",
            "job_id": "-",
            "export_id": "-",
            "file_path": "-",
        },
    )

    business_log.biz_log("edge.probe.start", device_id="edge_box_1")
    business_log.biz_log("edge.probe.done")

    assert any(
        "edge_box_1|[biz] edge.probe.start device_id=edge_box_1"
        in line
        for line in lines
    )
    assert any("-|[biz] edge.probe.done" in line for line in lines)


def test_loguru_patcher_supports_top_level_device_id_format():
    lines: list[str] = []
    logger.configure(
        handlers=[
            {
                "sink": lines.append,
                "format": "{device_id}|{message}",
            }
        ],
        patcher=_patch_record_with_common_extra,
        extra={
            "request_id": "-",
            "user_id": "-",
            "task_id": "-",
            "sample_id": "-",
            "job_id": "-",
            "export_id": "-",
            "file_path": "-",
            "device_id": "-",
        },
    )

    logger.bind(device_id="edge_box_1").info("hello")
    logger.info("world")

    assert any("edge_box_1|hello" in line for line in lines)
    assert any("-|world" in line for line in lines)


def test_biz_log_fastapi_path_template_does_not_crash_file_formatter(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(business_log.settings, "LOG_BUSINESS_DETAIL", True)

    logger.configure(
        handlers=[{"sink": lines.append, "format": _format_file_log_record}],
        patcher=_patch_record_with_common_extra,
        extra={
            "request_id": "-",
            "user_id": "-",
            "task_id": "-",
            "sample_id": "-",
            "job_id": "-",
            "export_id": "-",
            "file_path": "-",
            "device_id": "-",
        },
    )

    business_log.biz_log(
        "api.download_yolo_bm1688_runtime_log.start",
        method="GET",
        path=business_log.resolve_route_path_for_log(
            "/yolo-bm1688-exports/{export_id}/runtime-log/{kind}",
            {"export_id": 2, "kind": "compile"},
        ),
        export_id=2,
        kind="compile",
    )

    assert lines
    assert "runtime-log/compile" in lines[0]
    assert "{kind}" not in lines[0]


def test_resolve_route_path_for_log_replaces_known_params():
    resolved = business_log.resolve_route_path_for_log(
        "/yolo-bm1688-exports/{export_id}/runtime-log/{kind}",
        {"export_id": 2, "kind": "compile"},
    )
    assert resolved == "/yolo-bm1688-exports/2/runtime-log/compile"


def test_escape_log_format_braces():
    assert business_log.escape_log_format_braces("{kind}") == "{{kind}}"


def test_common_extra_keys_include_device_id():
    assert "device_id" in _COMMON_EXTRA_KEYS

