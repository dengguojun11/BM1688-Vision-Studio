from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "x6y7z8a9b0c1_label_attributes_unique_colors.py"
    )
    spec = importlib.util.spec_from_file_location(
        "label_attributes_unique_colors_migration", migration_path
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_color_backfill_is_deterministic_and_unique():
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        bind.execute(
            sa.text(
                "CREATE TABLE annotation_label "
                "(id INTEGER PRIMARY KEY, color VARCHAR(32) NOT NULL)"
            )
        )
        bind.execute(
            sa.text(
                "INSERT INTO annotation_label (id, color) VALUES "
                "(1, '#1677FF'), (2, '#1677ff'), (3, '#1677ff'), (4, '#52c41a')"
            )
        )
        migration._deduplicate_colors(bind)
        first = bind.execute(
            sa.text("SELECT id, color FROM annotation_label ORDER BY id")
        ).all()
        migration._deduplicate_colors(bind)
        second = bind.execute(
            sa.text("SELECT id, color FROM annotation_label ORDER BY id")
        ).all()

    colors = [row.color for row in first]
    assert first == second
    assert colors[0] == "#1677ff"
    assert len(colors) == len(set(colors))


def test_attributes_text_column_has_no_mysql_incompatible_default():
    migration = _load_migration_module()
    source = Path(migration.__file__).read_text(encoding="utf-8")
    assert "sa.Column(\"attributes_json\", sa.Text(), nullable=True)" in source
    assert "sa.Text(), nullable=False, server_default" not in source
