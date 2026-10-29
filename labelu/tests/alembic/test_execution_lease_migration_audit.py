from pathlib import Path

from labelu.scripts.audit_execution_lease_migration import audit_migration_drift


def test_formal_chain_and_orm_lease_contract_is_complete():
    report = audit_migration_drift()

    assert report["正式迁移 head"] == ["zf2a3b4c5d6e"]
    assert report["正式迁移是否唯一 head"] is True
    assert report["候选迁移存在"] is True
    assert report["候选是否属于正式versions"] is False
    assert report["状态"] == "无迁移漂移"

    for table_name in (
        "yolo_train_job",
        "yolo_bm1688_export",
        "yolo_edge_validation_job",
    ):
        assert set(report["ORM租约契约"][table_name]["columns"]) == {
            "queue_lease_owner",
            "queue_lease_expires_at",
            "execution_lease_owner",
            "execution_lease_expires_at",
            "execution_fencing_token",
        }
        expected_scan_index = (
            "ix_yolo_bm1688_export_worker_scan"
            if table_name == "yolo_bm1688_export"
            else (
                "ix_yolo_edge_validation_job_dispatch_scan"
                if table_name == "yolo_edge_validation_job"
                else "ix_yolo_train_job_training_worker_scan"
            )
        )
        assert expected_scan_index in report["ORM租约契约"][table_name]["indexes"]
        assert report["正式迁移缺失字段"][table_name] == []
        assert report["正式迁移缺失索引"][table_name] == []

    assert report["正式迁移字段来源"]["execution_lease_owner"]
    assert report["正式迁移字段来源"]["execution_fencing_token"]
    assert report["正式迁移字段来源"]["queue_lease_owner"]
    assert report["ORM租约契约"]["yolo_bm1688_export"]["辅助字段"] == ["worker_action"]
    assert report["ORM租约契约"]["yolo_bm1688_export"]["辅助索引"] == [
        "ix_yolo_bm1688_export_worker_action"
    ]
    assert all(not missing for missing in report["正式迁移缺失辅助字段"].values())
    assert all(not missing for missing in report["正式迁移缺失辅助索引"].values())


def test_audit_is_read_only_and_uses_expected_candidate_path():
    report = audit_migration_drift()
    assert Path(report["候选迁移路径"]).name == "gai_p1_08_add_dispatch_leases.py"
    assert report["正式迁移文件数"] > 100
