from typing import Any


def strategy_task_marker(row: Any) -> dict[str, str] | None:
    if row is None:
        return None
    task_type = str(getattr(row, "task_type", "") or "").strip()
    type_labels = {
        "model_capability": "自动化模型优化",
        "threshold_experiment": "自动化阈值实验",
    }
    return {
        "source": "third_party_api",
        "source_label": "第三方接口",
        "task_type": task_type,
        "task_type_label": type_labels.get(task_type, task_type or "第三方策略任务"),
        "external_task_id": str(getattr(row, "external_task_id", "") or ""),
        "platform_status": str(getattr(row, "platform_status", "") or ""),
    }
