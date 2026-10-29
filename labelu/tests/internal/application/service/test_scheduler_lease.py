from datetime import datetime, timedelta

from labelu.internal.application.service.scheduler_lease import (
    acquire_scheduler_lease,
    release_scheduler_lease,
    renew_scheduler_lease,
    scheduler_lease_is_current,
)


def test_scheduler_lease_uses_fencing_tokens_and_rejects_stale_owner(db):
    start = datetime(2026, 1, 1, 12, 0, 0)
    first = acquire_scheduler_lease(
        db, owner="node-a", lease_seconds=30, now=start
    )
    assert first is not None
    assert first.fencing_token == 1
    assert scheduler_lease_is_current(db, first, now=start + timedelta(seconds=1))

    renewed = renew_scheduler_lease(
        db, first, lease_seconds=30, now=start + timedelta(seconds=1)
    )
    assert renewed is not None
    assert renewed.fencing_token == first.fencing_token

    blocked = acquire_scheduler_lease(
        db, owner="node-b", lease_seconds=30, now=start + timedelta(seconds=2)
    )
    assert blocked is None

    takeover = acquire_scheduler_lease(
        db, owner="node-b", lease_seconds=30, now=start + timedelta(seconds=40)
    )
    assert takeover is not None
    assert takeover.fencing_token == first.fencing_token + 1
    assert renew_scheduler_lease(
        db, first, lease_seconds=30, now=start + timedelta(seconds=41)
    ) is None
    assert release_scheduler_lease(db, first, now=start + timedelta(seconds=41)) is False
    assert scheduler_lease_is_current(db, takeover, now=start + timedelta(seconds=41))


def test_scheduler_lease_release_allows_next_owner(db):
    start = datetime(2026, 1, 2, 8, 0, 0)
    first = acquire_scheduler_lease(db, owner="node-a", now=start)
    assert first is not None
    assert release_scheduler_lease(db, first, now=start + timedelta(seconds=1))
    second = acquire_scheduler_lease(
        db, owner="node-b", now=start + timedelta(seconds=2)
    )
    assert second is not None
    assert second.fencing_token == first.fencing_token + 1
