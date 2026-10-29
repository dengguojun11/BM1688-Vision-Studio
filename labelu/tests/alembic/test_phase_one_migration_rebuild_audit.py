from pathlib import Path

import pytest

from labelu.internal.common.phase_one_migration_rebuild_audit import (
    CURRENT_LOCAL_HEAD,
    LEGACY_SUFFIX_PLAN,
    PRODUCTION_BASELINE,
    REPLACEMENT_REVISIONS,
    audit_legacy_suffix,
    audit_replacement_workspace,
)


def _write_replacement_workspace(directory: Path) -> None:
    parent = PRODUCTION_BASELINE
    for index, revision in enumerate(REPLACEMENT_REVISIONS, start=1):
        (directory / f"{index:02d}_segment.py").write_text(
            f'revision = "{revision}"\n'
            f'down_revision = "{parent}"\n'
            "def upgrade():\n    pass\n"
            "def downgrade():\n    pass\n",
            encoding="utf-8",
        )
        parent = revision


def test_legacy_suffix_has_complete_object_level_mapping():
    report = audit_legacy_suffix("labelu/alembic_labelu/alembic.ini")

    assert report == {
        "production_baseline": PRODUCTION_BASELINE,
        "current_head": CURRENT_LOCAL_HEAD,
        "legacy_revision_count": 52,
        "replace_count": 40,
        "exclude_count": 12,
    }
    assert len(LEGACY_SUFFIX_PLAN) == 52
    assert all(plan.objects for plan in LEGACY_SUFFIX_PLAN.values())


def test_isolated_replacement_workspace_accepts_only_linear_eight_segments(tmp_path: Path):
    _write_replacement_workspace(tmp_path)

    assert audit_replacement_workspace(tmp_path) == {
        "production_baseline": PRODUCTION_BASELINE,
        "head": "gai_p1_08",
        "revision_count": 8,
    }


def test_replacement_workspace_rejects_a_second_root(tmp_path: Path):
    _write_replacement_workspace(tmp_path)
    second = tmp_path / "02_segment.py"
    second.write_text(
        second.read_text(encoding="utf-8").replace(
            'down_revision = "gai_p1_01"', 'down_revision = "h7i8j9k0l1m2"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected down_revision"):
        audit_replacement_workspace(tmp_path)


def test_replacement_workspace_rejects_merge_parent(tmp_path: Path):
    _write_replacement_workspace(tmp_path)
    second = tmp_path / "02_segment.py"
    second.write_text(
        second.read_text(encoding="utf-8").replace(
            'down_revision = "gai_p1_01"',
            'down_revision = ("gai_p1_01", "other_head")',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="merge or dynamic down_revision is forbidden"):
        audit_replacement_workspace(tmp_path)
