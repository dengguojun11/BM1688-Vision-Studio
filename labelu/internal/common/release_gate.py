"""Local, infrastructure-free release gate checks for M6-04.

The gate intentionally verifies deploy-critical invariants without contacting
production services: one Alembic head, production security configuration, and
a real SQLite backup/restore round trip.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic.config import Config
from alembic.script import ScriptDirectory

from labelu.internal.common.production_security import (
    ProductionSecurityReport,
    validate_production_security,
)


@dataclass(frozen=True)
class ReleaseGateReport:
    migration_heads: tuple[str, ...]
    security: ProductionSecurityReport
    backup_restore_ok: bool

    @property
    def valid(self) -> bool:
        return (
            len(self.migration_heads) == 1
            and self.security.valid
            and self.backup_restore_ok
        )


def migration_heads(config_path: str | Path) -> tuple[str, ...]:
    """Return Alembic heads without opening or mutating a database."""

    config = Config(str(config_path))
    script = ScriptDirectory.from_config(config)
    return tuple(script.get_heads())


def backup_restore_simulation() -> bool:
    """Exercise SQLite's backup API and verify schema plus row restoration."""

    with TemporaryDirectory(prefix="7gai-release-gate-") as directory:
        source_path = Path(directory) / "source.db"
        backup_path = Path(directory) / "backup.db"
        restored_path = Path(directory) / "restored.db"
        with closing(sqlite3.connect(source_path)) as source:
            source.execute("CREATE TABLE gate_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            source.executemany(
                "INSERT INTO gate_probe (value) VALUES (?)",
                [("migration",), ("security",), ("restore",)],
            )
            source.commit()
            with closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
        with closing(sqlite3.connect(backup_path)) as backup, closing(
            sqlite3.connect(restored_path)
        ) as restored:
            backup.backup(restored)
            row = restored.execute(
                "SELECT COUNT(*), GROUP_CONCAT(value, ',') FROM gate_probe"
            ).fetchone()
        return row == (3, "migration,security,restore")


def run_release_gate(
    *, config_path: str | Path, jwt_from_env: bool | None = None
) -> ReleaseGateReport:
    heads = migration_heads(config_path)
    security = validate_production_security(jwt_from_env=jwt_from_env)
    return ReleaseGateReport(
        migration_heads=heads,
        security=security,
        backup_restore_ok=backup_restore_simulation(),
    )


__all__ = [
    "ReleaseGateReport",
    "migration_heads",
    "backup_restore_simulation",
    "run_release_gate",
]
