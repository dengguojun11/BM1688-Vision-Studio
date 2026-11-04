"""Backfill public model integration metadata from export artifacts."""
from __future__ import annotations

import argparse

from labelu.internal.application.service.published_model_integration import (
    refresh_models_for_export,
)
from labelu.internal.common.db import session_scope
from labelu.internal.domain.models.published_model_catalog import (
    PublishedModelCatalog,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-id", type=int, default=0)
    args = parser.parse_args()
    with session_scope() as db:
        query = db.query(PublishedModelCatalog).filter(
            PublishedModelCatalog.deleted_at.is_(None),
            PublishedModelCatalog.export_id.isnot(None),
        )
        if args.export_id:
            query = query.filter(PublishedModelCatalog.export_id == args.export_id)
        rows = query.all()
        export_ids = sorted({int(row.export_id) for row in rows if row.export_id})
        refreshed = 0
        for export_id in export_ids:
            refreshed += refresh_models_for_export(db, export_id)
        db.flush()
        print(f"export_ids={len(export_ids)} refreshed={refreshed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
