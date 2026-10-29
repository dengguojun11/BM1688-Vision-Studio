"""Configure handlers and formats for application loggers."""

import logging
import sys
import traceback
from pathlib import Path
from pprint import pformat

# if you dont like imports of private modules
# you can move it to typing.py module
from loguru import logger
from loguru._defaults import LOGURU_FORMAT

from labelu.internal.common.business_log import (
    escape_log_format_braces,
    escape_log_format_tags,
)


class InterceptHandler(logging.Handler):
    """
    Default handler from examples in loguru documentaion.
    See https://loguru.readthedocs.io/en/stable/overview.html#entirely-compatible-with-standard-logging
    """

    def emit(self, record: logging.LogRecord):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def format_record(record: dict) -> str:
    """
    Custom format for loguru loggers.
    Uses pformat for log any data like request/response body during debug.
    Works with logging if loguru handler it.

    Example:
    >>> payload = [{"users":[{"name": "Nick", "age": 87, "is_active": True}, {"name": "Alex", "age": 27, "is_active": True}], "count": 2}]
    >>> logger.bind(payload=).debug("users payload")
    >>> [   {   'count': 2,
    >>>         'users': [   {'age': 87, 'is_active': True, 'name': 'Nick'},
    >>>                      {'age': 27, 'is_active': True, 'name': 'Alex'}]}]
    """

    format_string = LOGURU_FORMAT.replace(
        "{message}", "pid={process.id} | {extra[_safe_message]}"
    )
    # Field values are not re-parsed as format templates. Keep braces and
    # markup literal here; escaping them would duplicate braces in output.
    record["extra"]["_safe_message"] = str(record.get("message", ""))
    if record["extra"].get("payload") is not None:
        record["extra"]["payload"] = pformat(
            record["extra"]["payload"], indent=4, compact=True, width=88
        )
        format_string += "\n<level>{extra[payload]}</level>"

    format_string += "{exception}\n"
    return format_string


_FILE_LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
    "pid={process.id} rid={extra[request_id]} uid={extra[user_id]} | "
    "{name}:{function}:{line} - {message}{exception}\n"
)

_COMMON_EXTRA_KEYS = (
    "request_id",
    "user_id",
    "task_id",
    "sample_id",
    "job_id",
    "export_id",
    "file_path",
    "device_id",
)


def _format_file_log_record(record: dict) -> str:
    """文件日志：主行与异常堆栈每一行均带时间戳和进程号，便于 tail/grep。"""
    ts = record["time"].strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    level = f"{record['level'].name: <8}"
    rid = record["extra"].get("request_id", "-")
    uid = record["extra"].get("user_id", "-")
    pid = record["process"].id
    head = (
        f"{ts} | {level} | pid={pid} rid={rid} uid={uid} | "
        f"{record['name']}:{record['function']}:{record['line']} - "
        f"{record['message']}"
    )
    lines = [head]
    exc = record["exception"]
    if exc is not None:
        try:
            exc_text = "".join(traceback.format_exception(*exc)).rstrip("\n")
        except Exception:
            exc_text = ""
        if exc_text:
            for el in exc_text.split("\n"):
                lines.append(f"{ts} | {level} | pid={pid} | {el}")
    return escape_log_format_tags(escape_log_format_braces("\n".join(lines) + "\n"))


def _patch_record_with_common_extra(record: dict) -> None:
    """Mirror common extra fields to top-level record keys for custom format strings."""
    extra = record.get("extra") or {}
    for key in _COMMON_EXTRA_KEYS:
        record[key] = extra.get(key, "-")


def _file_log_handlers(
    log_dir: Path, *, level: str, rotation: str, retention: str, error_retention: str
) -> list[dict]:
    log_dir.mkdir(parents=True, exist_ok=True)
    return [
        {
            "sink": str(log_dir / "labelu.log"),
            "level": level,
            "format": _format_file_log_record,
            "rotation": rotation,
            "retention": retention,
            "encoding": "utf-8",
            "enqueue": True,
        },
        {
            "sink": str(log_dir / "error.log"),
            "level": "ERROR",
            "format": _format_file_log_record,
            "rotation": rotation,
            "retention": error_retention,
            "encoding": "utf-8",
            "enqueue": True,
        },
    ]


def init_logging():
    """
    Replaces logging handlers with a handler for using the custom handler.

    WARNING!
    if you call the init_logging in startup event function,
    then the first logs before the application start will be in the old format

    >>> app.add_event_handler("startup", init_logging)
    stdout:
    INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
    INFO:     Started reloader process [11528] using statreload
    INFO:     Started server process [6036]
    INFO:     Waiting for application startup.
    2020-07-25 02:19:21.357 | INFO     | uvicorn.lifespan.on:startup:34 - Application startup complete.

    """

    # disable handlers for specific uvicorn loggers
    # to redirect their output to the default uvicorn logger
    # works with uvicorn==0.11.6
    loggers = (logging.getLogger(name) for name in logging.root.manager.loggerDict)
    for l in loggers:
        l.handlers = []

    # change handler for default uvicorn logger
    intercept_handler = InterceptHandler()
    log_names = [
        "fastapi",
        "uvicorn",
        "uvicorn.access",
        "sqlalchemy",
        "sqlalchemy.pool",
    ]
    for log_name in log_names:
        logging.getLogger(log_name).handlers = [intercept_handler]

    from labelu.internal.common.config import settings

    handlers: list[dict] = [
        {"sink": sys.stdout, "level": logging.DEBUG, "format": format_record},
    ]
    if settings.LOG_FILE_ENABLED:
        level = (settings.LOG_LEVEL or "INFO").strip().upper()
        handlers.extend(
            _file_log_handlers(
                settings.effective_log_dir,
                level=level,
                rotation=(settings.LOG_ROTATION or "00:00").strip(),
                retention=(settings.LOG_RETENTION or "30 days").strip(),
                error_retention=(settings.LOG_ERROR_RETENTION or "90 days").strip(),
            )
        )

    logger.configure(
        handlers=handlers,
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

    if settings.LOG_FILE_ENABLED:
        log_dir = settings.effective_log_dir
        logger.info(
            "LabelU file logs: dir={} tail_app={} tail_error={}",
            log_dir,
            log_dir / "labelu.log",
            log_dir / "error.log",
        )
