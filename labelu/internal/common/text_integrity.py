from __future__ import annotations

_ENCODING_REPLACEMENT_MARKERS = ("?", "\ufffd")


def contains_encoding_replacement_marker(value: str | None) -> bool:
    """Return whether text contains markers commonly produced by lossy decoding."""
    text = str(value or "")
    return any(marker in text for marker in _ENCODING_REPLACEMENT_MARKERS)
