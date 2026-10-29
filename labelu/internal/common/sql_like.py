"""Helpers for treating user-entered LIKE search text literally."""

from __future__ import annotations

LIKE_ESCAPE = "\\"


def literal_like_pattern(value: str | None) -> str:
    """Build a contains pattern without treating LIKE metacharacters as wildcards."""
    escaped = (
        str(value or "")
        .strip()
        .replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"
