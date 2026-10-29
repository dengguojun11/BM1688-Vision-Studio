"""Shared request-idempotency helpers for user initiated create operations."""

from __future__ import annotations

import hashlib
import json
from typing import Any


MAX_IDEMPOTENCY_KEY_LENGTH = 256


def normalize_idempotency_key(value: str | None) -> str | None:
    """Return a bounded, transport-independent key or ``None`` when omitted."""

    key = str(value or "").strip()
    if not key:
        return None
    return key


def request_payload_hash(payload: Any) -> str:
    """Build a stable digest used to reject accidental key reuse."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
