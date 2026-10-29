import inspect

from labelu.internal.common.api_route_logging import _wrap_endpoint, should_skip_api_route_logging


async def _sample_handler(task_id: int, sample_id: int):
    return task_id + sample_id


def test_wrap_endpoint_marks_handler():
    wrapped = _wrap_endpoint(_sample_handler, path="/tasks/{task_id}/samples/{sample_id}", methods={"GET"})
    assert getattr(wrapped, "__wrapped_api_route__", False)
    assert inspect.iscoroutinefunction(wrapped)


def test_local_dev_session_route_skips_api_route_logging():
    wrapped = _wrap_endpoint(_sample_handler, path="/local-dev/session", methods={"GET"})
    assert wrapped is _sample_handler
    assert should_skip_api_route_logging("/local-dev/session") is True
    assert should_skip_api_route_logging("/tasks") is False
