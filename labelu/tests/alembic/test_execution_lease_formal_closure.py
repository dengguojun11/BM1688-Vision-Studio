"""正式 head 与 ORM 执行租约闭包的只读阻断证据。"""

from __future__ import annotations

from labelu.scripts.audit_execution_lease_migration import audit_migration_drift


def test_formal_head_and_execution_lease_orm_closure_are_complete():
    report = audit_migration_drift()
    assert report["正式迁移是否唯一 head"] is True
    assert report["正式迁移 head"] == ["zf2a3b4c5d6e"]

    for table_name, contract in report["ORM租约契约"].items():
        assert set(contract["columns"]) == {
            "queue_lease_owner",
            "queue_lease_expires_at",
            "execution_lease_owner",
            "execution_lease_expires_at",
            "execution_fencing_token",
        }, table_name
        assert report["正式迁移缺失字段"][table_name] == [], table_name
        assert report["正式迁移缺失索引"][table_name] == [], table_name
        assert report["正式迁移缺失辅助字段"].get(table_name, []) == [], table_name
        assert report["正式迁移缺失辅助索引"].get(table_name, []) == [], table_name

    assert report["ORM租约契约"]["yolo_train_job"]["辅助字段"] == [
        "assigned_gpu_json",
        "execution_runtime_pid",
    ]
    assert report["ORM租约契约"]["yolo_bm1688_export"]["辅助字段"] == [
        "worker_action"
    ]


def test_no_safe_new_lease_migration_is_needed_at_current_head():
    report = audit_migration_drift()
    # z4 already owns all execution-lease/worker_action sources. A new empty
    # migration would only add a redundant revision and risk a second head.
    assert report["正式迁移字段来源"]["execution_fencing_token"] == [
        "z4a5b6c7d8e9_add_execution_leases_and_bm1688_worker_actions.py"
    ]
    assert report["正式迁移辅助字段来源"]["worker_action"] == [
        "z4a5b6c7d8e9_add_execution_leases_and_bm1688_worker_actions.py"
    ]
    assert report["正式迁移缺失字段"] == {
        "yolo_train_job": [],
        "yolo_bm1688_export": [],
        "yolo_edge_validation_job": [],
    }
    assert report["正式迁移缺失索引"] == {
        "yolo_train_job": [],
        "yolo_bm1688_export": [],
        "yolo_edge_validation_job": [],
    }
