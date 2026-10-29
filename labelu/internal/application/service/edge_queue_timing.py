"""Shared timing and ETA rules for edge-validation queue views.

The queue table and the job detail endpoint must describe the same snapshot.
Keeping the arithmetic here prevents one surface from silently falling back to
different duration estimates or allowing terminal clocks to keep running.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

TERMINAL_STATUSES = frozenset({"COMPLETED", "PARTIAL_COMPLETED", "FAILED", "CANCELLED"})
MAX_HISTORY_DURATION_SECONDS = 24 * 3600


def now() -> datetime:
    """Return the legacy naive application timestamp used by job rows."""

    return datetime.now()


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize naive/aware timestamps for arithmetic.

    Existing job rows are predominantly naive local datetimes.  They are
    interpreted as UTC here for compatibility with the existing queue math;
    new callers should persist one consistent timezone representation.
    """

    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def seconds_since(
    value: datetime | None, *, current: datetime | None = None
) -> int | None:
    parsed = as_utc(value)
    if parsed is None:
        return None
    end = as_utc(current or now())
    if end is None:
        return None
    return max(0, int((end - parsed).total_seconds()))


def decode_json(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if raw in (None, ""):
        return default
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def execution_profile(row: object) -> str:
    request = decode_json(getattr(row, "result_json", None), {})
    request = request.get("request") if isinstance(request, dict) else {}
    value = str((request or {}).get("execution_profile") or "quick")
    return value if value in {"quick", "certification"} else "quick"


def duration_seconds(row: object) -> float | None:
    started = as_utc(getattr(row, "started_at", None))
    completed = as_utc(getattr(row, "completed_at", None))
    if started is None or completed is None or completed < started:
        return None
    value = (completed - started).total_seconds()
    return value if math.isfinite(value) and value > 0 else None


def history_durations(rows: Iterable[object]) -> list[float]:
    values = [duration_seconds(row) for row in rows]
    return [
        value
        for value in values
        if value is not None and value <= MAX_HISTORY_DURATION_SECONDS
    ]


def profile_duration_medians(rows: Iterable[object]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        duration = duration_seconds(row)
        if duration is None or duration > MAX_HISTORY_DURATION_SECONDS:
            continue
        grouped[execution_profile(row)].append(duration)
    return {
        profile: float(statistics.median(values))
        for profile, values in grouped.items()
        if values
    }


def fallback_duration_seconds(profile: str) -> int:
    # Certification runs serially execute FP32/FP16/INT8.
    return 4_800 if profile == "certification" else 900


def estimate_for_row(
    row: object,
    *,
    median_duration: float | None = None,
    median_by_profile: dict[str, float] | None = None,
    current: datetime | None = None,
) -> int | None:
    status_value = str(getattr(row, "status", "") or "").upper()
    if status_value in TERMINAL_STATUSES:
        return 0

    profile = execution_profile(row)
    if median_by_profile:
        # Do not let a certification-heavy history distort a quick run (or
        # vice versa) when the requested profile has no completed sample.
        baseline = median_by_profile.get(profile) or fallback_duration_seconds(profile)
    else:
        baseline = median_duration or fallback_duration_seconds(profile)
    if status_value == "PROCESSING":
        elapsed = seconds_since(getattr(row, "started_at", None), current=current) or 0
        return max(0, int(round(baseline - elapsed)))
    return max(0, int(round(baseline)))


def timing_for_row(
    row: object, *, current: datetime | None = None
) -> tuple[int | None, int | None]:
    """Return waiting/running seconds with a stable terminal snapshot.

    A few historical pack-preparation failure rows have no ``completed_at``.
    Their SQLAlchemy ``onupdate`` timestamp is the best persisted transition
    marker, so terminal clocks fall back to ``updated_at`` instead of growing
    forever on every poll.
    """

    snapshot = current or now()
    status_value = str(getattr(row, "status", "") or "").upper()
    queued_at = getattr(row, "queued_at", None)
    started_at = getattr(row, "started_at", None)
    completed_at = getattr(row, "completed_at", None)
    terminal_at = completed_at
    if status_value in TERMINAL_STATUSES and terminal_at is None:
        terminal_at = getattr(row, "updated_at", None)

    waiting_end = started_at or terminal_at
    running_end = terminal_at
    if status_value not in TERMINAL_STATUSES:
        waiting_end = waiting_end or snapshot
        running_end = running_end or snapshot

    return (
        seconds_since(queued_at, current=waiting_end),
        seconds_since(started_at, current=running_end),
    )
