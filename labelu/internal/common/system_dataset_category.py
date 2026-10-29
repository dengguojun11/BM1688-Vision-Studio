"""Reserved dataset categories managed by LabelU itself."""

CONSISTENT_POOL_CATEGORY_KEY = "training_consistent_pool"
CONSISTENT_POOL_CATEGORY_NAME = "训练一致数据集"
CONSISTENT_POOL_CATEGORY_DESCRIPTION = (
    "系统自动汇总人工标注与训练模型检测结果严格一致的样本，仅管理员可见和使用。"
)
CONSISTENT_POOL_CATEGORY_SORT_ORDER = 9000


def is_system_dataset_category_key(value: object) -> bool:
    return str(value or "").strip().lower() == CONSISTENT_POOL_CATEGORY_KEY
