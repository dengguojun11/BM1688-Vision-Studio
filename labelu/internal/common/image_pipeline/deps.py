from __future__ import annotations

from labelu.internal.common.optional_dependencies import ensure_image_pipeline_packages_installed


def ensure_image_pipeline_deps(*, reason: str = "图像导入流水线") -> bool:
    return ensure_image_pipeline_packages_installed(reason=reason)
