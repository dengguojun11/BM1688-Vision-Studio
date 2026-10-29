from datetime import datetime

from labelu.internal.common.dev_commit_record import (
    _parse_name_status_z,
    render_commit_entry,
)


def test_parse_name_status_z_skips_dev_log_file():
    raw = "M\x00frontend/extensions/pages/demo.html\x00A\x00docs/06-运维与协作/开发记录.md\x00"
    records = _parse_name_status_z(raw)
    assert len(records) == 1
    assert records[0]["path"] == "frontend/extensions/pages/demo.html"
    assert records[0]["category"] == "前端"


def test_render_commit_entry_contains_subject_and_files():
    records = [
        {
            "status": "M",
            "path": "labelu/internal/common/dev_commit_record.py",
            "category": "后端",
            "scope": "labelu/internal/common",
        },
        {
            "status": "A",
            "path": "docs/06-运维与协作/README.md",
            "category": "文档",
            "scope": "docs/06-运维与协作",
        },
    ]
    entry = render_commit_entry(
        records,
        "feat: auto record dev docs",
        datetime(2026, 6, 30, 14, 0, 0),
    )
    assert entry is not None
    assert "提交 `feat: auto record dev docs`" in entry
    assert "`labelu/internal/common/dev_commit_record.py`" in entry
    assert "`docs/06-运维与协作/README.md`" in entry
