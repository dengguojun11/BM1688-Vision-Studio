from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import labelu.internal.domain.models  # noqa: F401 - register ORM tables
from labelu.internal.application.service.task_cleanup import _delete_by_ids
from labelu.internal.common.db import Base


def test_delete_by_ids_skips_metadata_table_missing_from_database():
    """Old local databases may not contain retired optional task tables."""

    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as db:
        assert "edge_device_job" in Base.metadata.tables
        assert "edge_device_job" not in inspect(engine).get_table_names()

        assert _delete_by_ids(db, "edge_device_job", "task_id", {1}) == 0

