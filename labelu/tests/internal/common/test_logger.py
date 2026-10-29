import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace

from loguru import logger
from loguru._colorizer import Colorizer

from labelu.internal.common.logger import _format_file_log_record, format_record


def test_format_record_keeps_message_braces_literal():
    record = {"extra": {}, "message": "boom {self.path} {missing_key}"}

    rendered = format_record(record)

    assert "{exception}" in rendered
    assert "{extra[_safe_message]}" in rendered
    assert record["extra"]["_safe_message"] == "boom {self.path} {missing_key}"


def test_file_format_escapes_loguru_markup_in_dynamic_values():
    record = {
        "time": datetime.now(timezone.utc),
        "process": SimpleNamespace(id=12345),
        "level": logger.level("WARNING"),
        "extra": {"request_id": "rid1", "user_id": "-"},
        "name": "module",
        "function": "<module>",
        "line": 1,
        "message": 'upstream response: {"message":"<50002> InternalError"}',
        "exception": None,
    }

    rendered = _format_file_log_record(record)

    Colorizer.prepare_format(rendered)
    assert r"\<50002>" in rendered
    assert r"\<module>" in rendered


def test_file_main_and_exception_lines_identify_process():
    lines = []
    error_message = 'failure {"html": "<div>{field}</div>"}'
    sink_id = logger.add(
        lines.append,
        format=_format_file_log_record,
        catch=False,
        backtrace=False,
        diagnose=False,
    )
    try:
        try:
            raise ValueError(error_message)
        except ValueError:
            logger.exception("operation failed")
    finally:
        logger.remove(sink_id)

    rendered_lines = str(lines[-1]).splitlines()
    assert len(rendered_lines) > 1
    for line in rendered_lines:
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \|", line)
        assert f"pid={os.getpid()}" in line
    assert error_message in rendered_lines[-1]


def test_terminal_format_identifies_process():
    lines = []
    sink_id = logger.add(lines.append, format=format_record, catch=False)
    try:
        logger.info("process identity")
    finally:
        logger.remove(sink_id)

    assert f"pid={os.getpid()}" in str(lines[-1])
