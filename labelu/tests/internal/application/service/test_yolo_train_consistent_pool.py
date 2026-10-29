"""训练筛选一致池：筛选规则与任务命名。"""

import pytest

from labelu.internal.common.config import settings
from labelu.internal.application.service import yolo_train_consistent_pool as service
from labelu.internal.application.service.yolo_train_consistent_pool import (
    is_consistent_manifest_item,
    pool_task_name,
)


def test_pool_task_name():
    assert pool_task_name("垃圾暴露") == "垃圾暴露-训练筛选一致"


def test_legacy_category_lookup_does_not_guess_between_label_contracts(monkeypatch):
    monkeypatch.setattr(
        service,
        "_read_registry",
        lambda: {
            "pools": {
                "category:5|signature-a": {
                    "task_id": 220,
                    "category_scope_key": "category:5",
                    "last_category": "非机动车违停",
                },
                "category:5|signature-b": {
                    "task_id": 222,
                    "category_scope_key": "category:5",
                    "last_category": "非机动车违停",
                },
            },
            "categories": {
                "category:5": {
                    "task_id": 222,
                    "name": "非机动车违停",
                }
            },
        },
    )

    assert service._pool_task_id_for_category("非机动车违停") is None


@pytest.mark.parametrize("category", ["????-全量训练", "鸟类\ufffd别"])
def test_pool_task_name_rejects_encoding_replacement_markers(category):
    with pytest.raises(ValueError, match="encoding replacement markers"):
        pool_task_name(category)


def test_is_consistent_requires_no_fn_fp():
    assert is_consistent_manifest_item(
        {
            "fn": 0,
            "fp": 0,
            "gt": 2,
            "pred": 2,
            "tp": 2,
            "issues": ["ok"],
            "match_iou_threshold": settings.YOLO_CONSISTENT_POOL_MATCH_IOU,
            "min_matched_iou": settings.YOLO_CONSISTENT_POOL_MATCH_IOU,
        }
    )
    assert not is_consistent_manifest_item(
        {"fn": 1, "fp": 0, "gt": 2, "pred": 1, "tp": 1, "issues": ["fn"]}
    )
    assert not is_consistent_manifest_item(
        {"fn": 0, "fp": 1, "gt": 2, "pred": 3, "tp": 2, "issues": ["fp"]}
    )


def test_is_consistent_excludes_zero_box():
    """0 框图 issues=ok 但不算一致。"""
    assert not is_consistent_manifest_item(
        {"fn": 0, "fp": 0, "gt": 0, "pred": 0, "tp": 0, "issues": ["ok"]}
    )


def test_is_consistent_requires_tp_gt_pred_positive():
    assert not is_consistent_manifest_item(
        {"fn": 0, "fp": 0, "gt": 1, "pred": 0, "tp": 0, "issues": ["ok"]}
    )


def test_is_consistent_rejects_low_iou_and_legacy_reports():
    base = {
        "fn": 0,
        "fp": 0,
        "gt": 1,
        "pred": 1,
        "tp": 1,
        "issues": ["ok"],
    }
    assert not is_consistent_manifest_item(base)
    assert not is_consistent_manifest_item(
        {
            **base,
            "match_iou_threshold": settings.YOLO_CONSISTENT_POOL_MATCH_IOU,
            "min_matched_iou": settings.YOLO_CONSISTENT_POOL_MATCH_IOU - 0.01,
        }
    )
