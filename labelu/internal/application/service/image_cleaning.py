"""Policy and read-only image similarity helpers for image cleaning.

The service deliberately has no database/session dependency.  Policy state is
read and written through the existing system configuration provider, while
the preview helpers operate only on the supplied image paths and metadata.
"""

from __future__ import annotations

import hashlib
import json
import math
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from labelu.internal.application.response.image_cleaning import (
    ImageCleaningPolicyResponse,
)
from labelu.internal.common.image_pipeline.features import image_similarity
from labelu.internal.common.system_config_provider import (
    get_runtime_override,
    read_group_values,
    resolve_value,
    write_group_values,
)

_DEFAULT_IMAGE_SIMILARITY = image_similarity

IMAGE_CLEANING_POLICY_GROUP = "image_cleaning_policy"
IMAGE_CLEANING_POLICY_ITEM = "policy"
IMAGE_CLEANING_ALGORITHM_VERSION = "visual-v1"
IMAGE_CLEANING_CONFIG_BACKEND_LABEL = "MySQL 治理表（system_config_*）"
IMAGE_CLEANING_PRESET_NAMES = ("simple", "standard", "strict")

_DEFAULT_THRESHOLDS = {
    "simple": 0.98,
    "standard": 0.95,
    "strict": 0.90,
}

# Public callers receive a detached copy through ``load_policy``.  Keeping the
# constant itself in the documented wire shape makes it useful in tests and in
# a future registry bootstrap without exposing mutable nested state to callers.
DEFAULT_POLICY: dict[str, Any] = {
    "active_preset": "standard",
    "presets": {
        name: {"threshold": threshold}
        for name, threshold in _DEFAULT_THRESHOLDS.items()
    },
    "revision": 1,
    "algorithm_version": IMAGE_CLEANING_ALGORITHM_VERSION,
}

# A preview compares one upload against several protected images.  Keep the
# per-preview feature cache out of the public API so direct similarity calls
# retain their existing behavior and tests can still replace visual_similarity.
_VISUAL_FEATURE_CACHE: ContextVar[dict[tuple[str, int, int], Any] | None] = ContextVar(
    "image_cleaning_visual_feature_cache", default=None
)


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    # Pydantic command/response instances are accepted without coupling the
    # service to a particular Pydantic major-version serialization method.
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        return dumped if isinstance(dumped, Mapping) else None
    if hasattr(value, "dict"):
        dumped = value.dict()
        return dumped if isinstance(dumped, Mapping) else None
    return None


def _policy_payload(value: Any) -> Mapping[str, Any] | None:
    """Unwrap the governance item when a caller passes ``{"policy": ...}``."""

    mapping = _as_mapping(value)
    if mapping is None:
        return None
    nested = mapping.get(IMAGE_CLEANING_POLICY_ITEM)
    if isinstance(nested, Mapping) and not any(
        key in mapping
        for key in ("active_preset", "presets", "revision", "algorithm_version")
    ):
        return nested
    return mapping


def _threshold_value(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("threshold must be a finite number between 0 and 1")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("threshold must be a finite number between 0 and 1") from None
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ValueError("threshold must be a finite number between 0 and 1")
    return parsed


def normalize_policy(policy: Any = None) -> dict[str, Any]:
    """Return a detached policy in the canonical wire shape.

    Missing fields use the documented defaults.  Invalid supplied values are
    retained long enough for :func:`validate_policy` to report them instead of
    being silently replaced by a safer-looking default.
    """

    source = _policy_payload(policy) or {}
    normalized: dict[str, Any] = deepcopy(DEFAULT_POLICY)

    if "active_preset" in source:
        normalized["active_preset"] = source.get("active_preset")
    if "revision" in source:
        normalized["revision"] = source.get("revision")
    if "algorithm_version" in source:
        normalized["algorithm_version"] = source.get("algorithm_version")

    supplied_presets = source.get("presets")
    if supplied_presets is not None:
        if isinstance(supplied_presets, Mapping):
            # A complete policy may omit a preset only when it is being
            # normalized from a partial PATCH; defaults fill those omissions.
            for name in IMAGE_CLEANING_PRESET_NAMES:
                if name in supplied_presets:
                    raw = supplied_presets[name]
                    if isinstance(raw, Mapping):
                        normalized["presets"][name] = {
                            "threshold": raw.get("threshold")
                        }
                    else:
                        normalized["presets"][name] = {"threshold": raw}
            # Preserve unknown keys so validation can reject them explicitly.
            for name, raw in supplied_presets.items():
                if name not in IMAGE_CLEANING_PRESET_NAMES:
                    normalized["presets"][name] = raw
        else:
            normalized["presets"] = supplied_presets

    return normalized


def validate_policy(policy: Any) -> list[str]:
    """Return deterministic validation reason codes for a policy snapshot."""

    source = _policy_payload(policy)
    if source is None:
        return ["policy_missing"]

    reasons: list[str] = []
    active = source.get("active_preset")
    if str(active or "").strip() not in IMAGE_CLEANING_PRESET_NAMES:
        reasons.append("active_preset_invalid")

    presets = source.get("presets")
    if not isinstance(presets, Mapping):
        reasons.append("presets_missing")
    else:
        unknown = sorted(
            set(str(key) for key in presets).difference(IMAGE_CLEANING_PRESET_NAMES)
        )
        if unknown:
            reasons.append("preset_names_invalid")
        parsed_thresholds: dict[str, float] = {}
        for name in IMAGE_CLEANING_PRESET_NAMES:
            if name not in presets:
                reasons.append(f"preset_{name}_missing")
                continue
            raw = presets[name]
            if not isinstance(raw, Mapping):
                reasons.append(f"preset_{name}_invalid")
                continue
            if set(raw).difference({"threshold"}):
                reasons.append(f"preset_{name}_fields_invalid")
            try:
                parsed_thresholds[name] = _threshold_value(raw.get("threshold"))
            except ValueError:
                reasons.append(f"preset_{name}_threshold_invalid")
        if len(parsed_thresholds) == len(IMAGE_CLEANING_PRESET_NAMES):
            if not (
                parsed_thresholds["simple"]
                >= parsed_thresholds["standard"]
                >= parsed_thresholds["strict"]
            ):
                reasons.append("preset_order_invalid")

    revision = source.get("revision")
    if isinstance(revision, bool):
        reasons.append("revision_invalid")
    else:
        try:
            parsed_revision = int(revision)
        except (TypeError, ValueError):
            parsed_revision = 0
        revision_text_invalid = isinstance(revision, str) and (
            str(parsed_revision) != revision.strip()
        )
        if parsed_revision < 1 or revision_text_invalid:
            reasons.append("revision_invalid")

    if (
        str(source.get("algorithm_version") or "").strip()
        != IMAGE_CLEANING_ALGORITHM_VERSION
    ):
        reasons.append("algorithm_version_invalid")
    return reasons


def _validated_policy(policy: Any) -> dict[str, Any]:
    normalized = normalize_policy(policy)
    reasons = validate_policy(normalized)
    if reasons:
        raise ValueError("invalid image-cleaning policy: " + ", ".join(reasons))

    # Normalize numeric values only after validation has established that all
    # values are finite and bounded.
    normalized["active_preset"] = str(normalized["active_preset"]).strip()
    normalized["revision"] = int(normalized["revision"])
    normalized["algorithm_version"] = IMAGE_CLEANING_ALGORITHM_VERSION
    normalized["presets"] = {
        name: {"threshold": _threshold_value(normalized["presets"][name]["threshold"])}
        for name in IMAGE_CLEANING_PRESET_NAMES
    }
    return normalized


def load_policy() -> dict[str, Any]:
    """Load the policy from the governance provider, defaulting on first use."""

    db_values = read_group_values(IMAGE_CLEANING_POLICY_GROUP)
    db_policy = db_values.get(IMAGE_CLEANING_POLICY_ITEM)
    candidate = resolve_value(
        runtime_value=get_runtime_override(
            IMAGE_CLEANING_POLICY_GROUP,
            IMAGE_CLEANING_POLICY_ITEM,
        ),
        db_value=db_policy if isinstance(db_policy, Mapping) else DEFAULT_POLICY,
        default_value=DEFAULT_POLICY,
    )
    return _validated_policy(candidate)


def _command_payload(policy: Any) -> dict[str, Any]:
    mapping = _as_mapping(policy)
    if mapping is None:
        raise ValueError("image-cleaning policy must be an object")
    return dict(mapping)


def _merge_policy(
    current: Mapping[str, Any], patch: Mapping[str, Any]
) -> dict[str, Any]:
    merged = deepcopy(dict(current))
    if "active_preset" in patch and patch.get("active_preset") is not None:
        merged["active_preset"] = patch.get("active_preset")
    if "algorithm_version" in patch and patch.get("algorithm_version") is not None:
        merged["algorithm_version"] = patch.get("algorithm_version")
    if "revision" in patch and patch.get("revision") is not None:
        merged["revision"] = patch.get("revision")
    if "presets" in patch and patch.get("presets") is not None:
        incoming = patch.get("presets")
        if not isinstance(incoming, Mapping):
            merged["presets"] = incoming
        else:
            merged_presets = dict(merged.get("presets") or {})
            for name, value in incoming.items():
                merged_presets[name] = value
            merged["presets"] = merged_presets
    return merged


def save_policy(policy: Any) -> dict[str, Any]:
    """Validate and persist a patch/full policy, returning the full snapshot.

    A changed policy receives the next revision.  Replaying an identical patch
    is idempotent and keeps the existing revision.
    """

    current = load_policy()
    patch = _command_payload(policy)
    expected_revision = patch.get("expected_revision")
    if expected_revision is not None and int(expected_revision) != int(
        current["revision"]
    ):
        raise RuntimeError("图片清洗配置已被其他管理人员更新，请刷新后重试")
    candidate = _validated_policy(_merge_policy(current, patch))
    # Revision is server-owned.  A client may submit it for compatibility, but
    # it cannot force a decrement or skip a revision on a changed snapshot.
    candidate["revision"] = int(current["revision"])
    comparable_current = deepcopy(current)
    comparable_candidate = deepcopy(candidate)
    comparable_current.pop("revision", None)
    comparable_candidate.pop("revision", None)
    if comparable_candidate != comparable_current:
        candidate["revision"] = int(current["revision"]) + 1

    write_group_values(
        IMAGE_CLEANING_POLICY_GROUP,
        {IMAGE_CLEANING_POLICY_ITEM: candidate},
        value_source="db",
        change_reason="save image cleaning policy",
        expected_revisions={IMAGE_CLEANING_POLICY_ITEM: int(current["revision"])},
    )
    return deepcopy(candidate)


def policy_response(policy: Any = None) -> ImageCleaningPolicyResponse:
    """Convert a policy snapshot to the public response model."""

    payload = _validated_policy(load_policy() if policy is None else policy)
    return ImageCleaningPolicyResponse(
        **payload,
        config_backend_label=IMAGE_CLEANING_CONFIG_BACKEND_LABEL,
    )


def _visual_cache_key(path: str | Path) -> tuple[str, int, int] | None:
    try:
        resolved = Path(path).resolve()
        stat = resolved.stat()
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return str(resolved), int(stat.st_size), int(stat.st_mtime_ns)


def _cached_histogram(path: str | Path, cache: dict[tuple[str, int, int], Any]):
    key = _visual_cache_key(path)
    if key is None:
        return None
    if key in cache:
        return cache[key]
    try:
        import cv2
        import numpy as np

        image = cv2.imread(str(path))
        if image is None:
            histogram = None
        else:
            hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
            histogram = cv2.calcHist(
                [hsv],
                [0, 1, 2],
                None,
                [50, 60, 60],
                [0, 180, 0, 256, 0, 256],
            )
            cv2.normalize(histogram, histogram, 0, 1, cv2.NORM_MINMAX)
            histogram = np.asarray(histogram)
    except Exception:
        histogram = None
    cache[key] = histogram
    return histogram


def _cached_visual_similarity(
    path1: str | Path, path2: str | Path, cache: dict[tuple[str, int, int], Any]
) -> float:
    try:
        import cv2

        first = _cached_histogram(path1, cache)
        second = _cached_histogram(path2, cache)
        if first is None or second is None:
            return 0.0
        return max(0.0, float(cv2.compareHist(first, second, cv2.HISTCMP_CORREL)))
    except Exception:
        return 0.0


def visual_similarity(path1: str | Path, path2: str | Path) -> float:
    """Return a bounded visual similarity score for two image files."""

    first = Path(path1)
    second = Path(path2)
    try:
        if first.resolve() == second.resolve() and first.is_file():
            return 1.0
    except OSError:
        pass
    cache = _VISUAL_FEATURE_CACHE.get()
    try:
        if cache is not None and image_similarity is _DEFAULT_IMAGE_SIMILARITY:
            score = _cached_visual_similarity(first, second, cache)
        else:
            score = float(image_similarity(first, second, method="hist"))
    except (OSError, RuntimeError, ValueError, TypeError, ImportError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return max(0.0, min(1.0, score))


def _item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _item_id(item: Any, index: int) -> Any:
    value = _item_value(item, "id")
    if value is None or value == "":
        value = _item_value(item, "filename")
    if value is None or value == "":
        value = index
    return value


def _identity_token(item: Any, index: int) -> str:
    item_id = _item_id(item, index)
    sha = str(_item_value(item, "sha256") or "").strip().lower()
    # A content hash is the canonical identity.  Uploads normally persist one,
    # but older rows may not.  Hash the readable file in that case so a remote
    # preview (which uses a temporary local path) has the same identity when it
    # is committed later.
    if not sha:
        path = str(_item_value(item, "path") or "")
        try:
            digest = hashlib.sha256()
            with Path(path).open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            sha = digest.hexdigest()
        except (OSError, TypeError, ValueError):
            # Keep the fingerprint deterministic even when the caller only
            # supplies metadata and no readable file.
            filename = str(_item_value(item, "filename") or "")
            sha = f"filename:{filename}"
    return json.dumps(
        {"id": str(item_id), "sha256": sha},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def preview_paths(
    items: Iterable[Any],
    threshold: float,
    *,
    revision: int = int(DEFAULT_POLICY["revision"]),
) -> dict[str, Any]:
    token = _VISUAL_FEATURE_CACHE.set({})
    try:
        return _preview_paths(items, threshold, revision=revision)
    finally:
        _VISUAL_FEATURE_CACHE.reset(token)


def _preview_paths(
    items: Iterable[Any],
    threshold: float,
    *,
    revision: int = int(DEFAULT_POLICY["revision"]),
) -> dict[str, Any]:
    """Preview exact/visual duplicates while retaining the first input item.

    The function is intentionally read-only.  ``pairs`` contains only the
    duplicate relationships needed by a review UI; no file is deleted.
    """

    threshold_value = _threshold_value(threshold)
    try:
        revision_value = int(revision)
    except (TypeError, ValueError):
        raise ValueError("revision must be a positive integer") from None
    if revision_value < 1:
        raise ValueError("revision must be a positive integer")

    rows = list(items or [])
    ids = [_item_id(item, index) for index, item in enumerate(rows)]
    duplicate_indexes: set[int] = set()
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(rows):
        if left_index in duplicate_indexes:
            continue
        left_sha = str(_item_value(left, "sha256") or "").strip().lower()
        left_path = _item_value(left, "path")
        for right_index in range(left_index + 1, len(rows)):
            if right_index in duplicate_indexes:
                continue
            right = rows[right_index]
            right_sha = str(_item_value(right, "sha256") or "").strip().lower()
            score = 0.0
            if left_sha and right_sha and left_sha == right_sha:
                score = 1.0
            elif left_path is not None and _item_value(right, "path") is not None:
                score = visual_similarity(left_path, _item_value(right, "path"))
            if score < threshold_value:
                continue
            duplicate_indexes.add(right_index)
            pairs.append(
                {
                    "keep_id": ids[left_index],
                    "duplicate_id": ids[right_index],
                    "similarity": round(float(score), 6),
                }
            )

    duplicate_ids = [
        ids[index] for index in range(len(rows)) if index in duplicate_indexes
    ]
    keep_ids = [
        ids[index] for index in range(len(rows)) if index not in duplicate_indexes
    ]
    fingerprint_payload = {
        "algorithm_version": IMAGE_CLEANING_ALGORITHM_VERSION,
        "revision": revision_value,
        "threshold": threshold_value,
        "items": [_identity_token(item, index) for index, item in enumerate(rows)],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "total": len(rows),
        "duplicate_ids": duplicate_ids,
        "keep_ids": keep_ids,
        "pairs": pairs,
        "deleted_count": len(duplicate_ids),
        "kept_count": len(keep_ids),
        "fingerprint": fingerprint,
    }


__all__ = [
    "DEFAULT_POLICY",
    "IMAGE_CLEANING_ALGORITHM_VERSION",
    "IMAGE_CLEANING_CONFIG_BACKEND_LABEL",
    "IMAGE_CLEANING_POLICY_GROUP",
    "IMAGE_CLEANING_POLICY_ITEM",
    "IMAGE_CLEANING_PRESET_NAMES",
    "load_policy",
    "normalize_policy",
    "policy_response",
    "preview_paths",
    "save_policy",
    "validate_policy",
    "visual_similarity",
]
