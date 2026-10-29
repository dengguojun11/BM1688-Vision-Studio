from datetime import datetime
from pathlib import Path

from labelu.internal.common.dev_change_watchdog import (
    SnapshotItem,
    append_markdown_record,
    classify_path,
    render_markdown_entry,
    summarize_changes,
)


def test_classify_path():
    assert classify_path("docs/README.md") == "文档"
    assert classify_path("frontend/extensions/pages/demo.html") == "前端"
    assert classify_path("labelu/tests/test_demo.py") == "测试"
    assert classify_path("pyproject.toml") == "配置"
    assert classify_path("deploy/systemd/labelu.service") == "部署"
    assert classify_path("labelu/internal/common/config.py") == "后端"


def test_render_markdown_entry_contains_paths_and_summary():
    before = {}
    after = {
        "labelu/internal/common/dev_change_watchdog.py": SnapshotItem(
            path="labelu/internal/common/dev_change_watchdog.py",
            status=" M",
            size=100,
            mtime_ns=1,
        ),
        "docs/06-运维与协作/开发记录.md": SnapshotItem(
            path="docs/06-运维与协作/开发记录.md",
            status="??",
            size=100,
            mtime_ns=2,
        ),
    }
    summary = summarize_changes(before, after, set(after))
    entry = render_markdown_entry(
        summary,
        datetime(2026, 6, 30, 12, 0, 0),
        datetime(2026, 6, 30, 12, 0, 20),
    )
    assert entry is not None
    assert "自动记录" in entry
    assert "`labelu/internal/common/dev_change_watchdog.py`" in entry
    assert "状态统计" in entry


def test_append_markdown_record_adds_month_day_and_entry(tmp_path: Path):
    log_path = tmp_path / "开发记录.md"
    log_path.write_text(
        "# 开发记录\n\n## 3. 快速索引\n\n---\n",
        encoding="utf-8",
    )
    append_markdown_record(
        log_path,
        "### 12:00 自动记录：文档变更（1项）\n\n- 类型：文档",
        now=datetime(2026, 6, 30, 12, 0, 0),
    )
    text = log_path.read_text(encoding="utf-8")
    assert "### 2026-06" in text
    assert "- [2026-06-30](#2026-06-30)" in text
    assert "## 2026-06-30" in text
    assert "### 12:00 自动记录：文档变更（1项）" in text
