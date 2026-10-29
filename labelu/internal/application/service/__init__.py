"""Application services; auto-instrument public async entrypoints for [biz] logs."""
from __future__ import annotations

import importlib
import inspect
import pkgutil

from labelu.internal.common.business_log import log_business_operation


def instrument_async_service_module(module) -> None:
    """Wrap module-level async callables (not leading with _) for business logging."""
    mod_name = module.__name__
    short_name = mod_name.rsplit(".", 1)[-1]

    for name, obj in list(vars(module).items()):
        if name.startswith("_"):
            continue
        if not inspect.iscoroutinefunction(obj):
            continue
        if getattr(obj, "__module__", None) != mod_name:
            continue
        if getattr(obj, "__wrapped_log_business__", False):
            continue
        wrapped = log_business_operation(f"{short_name}.{name}")(obj)
        setattr(module, name, wrapped)


def _instrument_all_service_modules() -> None:
    package = __name__
    prefix = f"{package}."
    for module_info in pkgutil.walk_packages(__path__, prefix=prefix):
        if module_info.name.endswith(".tests"):
            continue
        module = importlib.import_module(module_info.name)
        instrument_async_service_module(module)


_instrument_all_service_modules()
