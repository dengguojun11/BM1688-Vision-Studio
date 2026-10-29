"""Coordinate contract helpers for rule regions and lines.

The rule engine stores normalized coordinates (0..1) so a rule can be reused
across camera resolutions.  These helpers intentionally accept both the
legacy ``[x, y]`` point form and the explicit ``{"x": ..., "y": ...}`` form.
"""

from __future__ import annotations

from numbers import Real
from typing import Any


def _dimension(value: Real, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or float(value) <= 0:
        raise ValueError(f"{name} must be a positive number")
    return float(value)


def _point(value: Any) -> tuple[float, float]:
    if isinstance(value, dict):
        x, y = value.get("x"), value.get("y")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        x, y = value
    else:
        raise ValueError("point must contain x and y coordinates")
    if any(isinstance(item, bool) or not isinstance(item, Real) for item in (x, y)):
        raise ValueError("point coordinates must be numbers")
    return float(x), float(y)


def normalize_rule_coordinates(
    points: list[Any], *, width: Real, height: Real
) -> dict[str, Any]:
    """Convert pixel points to a stable normalized coordinate envelope."""

    image_width = _dimension(width, "width")
    image_height = _dimension(height, "height")
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("at least two points are required")
    normalized = []
    for raw in points:
        x, y = _point(raw)
        if not 0 <= x <= image_width or not 0 <= y <= image_height:
            raise ValueError("pixel coordinate is outside image bounds")
        normalized.append({"x": round(x / image_width, 8), "y": round(y / image_height, 8)})
    return {
        "coordinate_space": "normalized",
        "points": normalized,
        "source_size": {"width": image_width, "height": image_height},
    }


def denormalize_rule_coordinates(
    envelope: dict[str, Any], *, width: Real, height: Real
) -> list[dict[str, float]]:
    """Convert a normalized rule coordinate envelope back to pixel points."""

    image_width = _dimension(width, "width")
    image_height = _dimension(height, "height")
    if not isinstance(envelope, dict) or envelope.get("coordinate_space") != "normalized":
        raise ValueError("coordinate_space must be normalized")
    points = envelope.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError("at least two points are required")
    result = []
    for raw in points:
        x, y = _point(raw)
        if not 0 <= x <= 1 or not 0 <= y <= 1:
            raise ValueError("normalized coordinate must be between 0 and 1")
        result.append({"x": round(x * image_width, 6), "y": round(y * image_height, 6)})
    return result

