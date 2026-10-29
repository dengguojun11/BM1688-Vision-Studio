"""Pure, deterministic analysis of AICM's historical model catalogue.

Versions here describe chronological entries with the same label *set*.  They
do not assert common training lineage, better quality, or interchangeable output
class order.  No network, database, or filesystem operations occur in this module.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

CHINA_TIME = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _timestamp(value: Any) -> float | None:
    if isinstance(value, datetime):
        parsed = value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
    else:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            try:
                date = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
                parsed = date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()
            except (ValueError, OverflowError):
                return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    # Some exported snapshots use milliseconds instead of seconds.
    if parsed >= 100_000_000_000:
        parsed /= 1000
    try:
        datetime.fromtimestamp(parsed, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return parsed


def _model_sort_key(model: Mapping[str, Any]) -> tuple:
    identity = str(model.get("id") if model.get("id") is not None else "")
    try:
        identity_key = (0, int(identity), identity)
    except ValueError:
        identity_key = (1, 0, identity)
    # Missing dates follow dated entries; never pretend an undated row is oldest.
    return (
        model.get("published_timestamp") is None,
        model.get("published_timestamp") or 0,
        identity_key,
    )


def _labels(value: Any) -> tuple[list[str], list[dict[str, str]], int]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [value]
    if isinstance(value, dict):
        if "key" in value or "name" in value:
            value = [value]
        else:
            value = value.get("list", value.get("results", value.get("data", [])))
    if not isinstance(value, list):
        value = []
    ordered: list[str] = []
    details: list[dict[str, str]] = []
    fallback_count = 0
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            name = str(item.get("name") or "").strip()
            identity = key or name
            if not key and identity:
                fallback_count += 1
        elif isinstance(item, str):
            key = identity = item.strip()
            name = ""
        else:
            continue
        if not identity or identity in seen:
            continue
        seen.add(identity)
        ordered.append(identity)
        details.append({"key": key, "name": name, "identity": identity})
    return ordered, details, fallback_count


def _has_zip_download_url(row: Mapping[str, Any]) -> bool:
    """AICM exposes compiled downloads inside an evolution list or object."""
    if str(row.get("zip_download_url") or "").strip():
        return True

    def contains_zip(value: Any) -> bool:
        if isinstance(value, list):
            return any(contains_zip(item) for item in value)
        if not isinstance(value, Mapping):
            return False
        if str(value.get("zip_download_url") or "").strip():
            return True
        url = str(value.get("download_url") or "").strip()
        try:
            path = urlsplit(url).path.lower()
        except ValueError:
            path = url.split("?", 1)[0].lower()
        if path.endswith(".zip"):
            return True
        return any(
            contains_zip(child)
            for child in value.values()
            if isinstance(child, (list, Mapping))
        )

    return contains_zip(row.get("evolution_set_obj_info")) or contains_zip(
        {"download_url": row.get("download_url")}
    )


def analyze_models(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Compare English label identities and assign chronological group versions.

    ``rows`` must contain one entry per remote model.  Input rows are never
    modified.  Duplicate remote IDs are retained and reported as a warning so
    pagination problems cannot silently change the apparent source count.
    Empty label sets do not form groups or share labels with each other.
    """
    models: list[dict[str, Any]] = []
    groups: dict[tuple[str, ...], list[int]] = defaultdict(list)
    inverted: dict[str, set[int]] = defaultdict(set)
    ids: dict[str, list[int]] = defaultdict(list)
    fallback_count = 0
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("模型列表中的每一条数据都必须是对象")
        labels, details, missing_keys = _labels(raw.get("label_class_set_obj_info"))
        fallback_count += missing_keys
        label_set = tuple(sorted(labels))
        signature = (
            hashlib.sha256(
                json.dumps(label_set, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            if labels
            else None
        )
        published = _timestamp(raw.get("published_at"))
        created = _timestamp(raw.get("created_at"))
        timestamp = published if published is not None else created
        index = len(models)
        model = {
            "id": raw.get("id"),
            "name": str(raw.get("name") or ""),
            "labels": labels,
            "label_details": details,
            "label_signature": signature,
            "label_group_id": f"labels-{signature[:16]}" if signature else None,
            "version": None,
            "version_label": None,
            "label_group_version": None,
            "label_group_size": 0,
            "published_timestamp": timestamp,
            "version_time": (
                datetime.fromtimestamp(timestamp, CHINA_TIME).isoformat()
                if timestamp is not None
                else None
            ),
            "version_time_source": (
                "published_at"
                if published is not None
                else "created_at" if created is not None else None
            ),
            "has_zip_download_url": _has_zip_download_url(raw),
            "in_use": raw.get("in_use") is True
            or raw.get("in_use") == 1
            or str(raw.get("in_use") or "").lower() == "true",
            "related_model_count": 0,
            "identical_model_count": 0,
            "partial_overlap_model_count": 0,
        }
        models.append(model)
        if raw.get("id") is not None:
            ids[str(raw["id"])].append(index)
        if labels:
            groups[label_set].append(index)
            for label in label_set:
                inverted[label].add(index)

    label_groups: list[dict[str, Any]] = []
    order_mismatch_groups = 0
    for labels, indexes in sorted(groups.items()):
        ordered_indexes = sorted(
            indexes, key=lambda index: _model_sort_key(models[index])
        )
        order_mismatch = len({tuple(models[index]["labels"]) for index in indexes}) > 1
        if order_mismatch:
            order_mismatch_groups += 1
        versions = []
        for version, index in enumerate(ordered_indexes, start=1):
            model = models[index]
            model["version"] = version
            model["version_label"] = f"v{version}"
            model["label_group_version"] = model["version_label"]
            model["label_group_size"] = len(indexes)
            model["identical_model_count"] = len(indexes) - 1
            versions.append(
                {
                    "id": model["id"],
                    "name": model["name"],
                    "version": version,
                    "version_label": f"v{version}",
                    "version_time": model["version_time"],
                    "version_time_source": model["version_time_source"],
                }
            )
        label_groups.append(
            {
                "label_group_id": models[indexes[0]]["label_group_id"],
                "label_signature": models[indexes[0]]["label_signature"],
                "labels": list(labels),
                "model_count": len(indexes),
                "model_ids": [models[index]["id"] for index in ordered_indexes],
                "versions": versions,
                "label_order_mismatch": order_mismatch,
            }
        )

    related_sum = 0
    for index, model in enumerate(models):
        related: set[int] = set()
        for label in model["labels"]:
            related.update(inverted[label])
        related.discard(index)
        model["related_model_count"] = len(related)
        model["partial_overlap_model_count"] = (
            len(related) - model["identical_model_count"]
        )
        related_sum += len(related)

    shared_labels = []
    for label, indexes in sorted(inverted.items()):
        if len(indexes) < 2:
            continue
        ordered_indexes = sorted(
            indexes, key=lambda index: _model_sort_key(models[index])
        )
        shared_labels.append(
            {
                "label": label,
                "model_count": len(indexes),
                "model_ids": [models[index]["id"] for index in ordered_indexes],
                "models": [
                    {
                        "id": models[index]["id"],
                        "name": models[index]["name"],
                        "version_label": models[index]["version_label"],
                        "label_group_id": models[index]["label_group_id"],
                    }
                    for index in ordered_indexes
                ],
            }
        )
    repeated = [group for group in label_groups if group["model_count"] > 1]
    identical_pairs = sum(
        group["model_count"] * (group["model_count"] - 1) // 2 for group in repeated
    )
    related_pairs = related_sum // 2
    duplicate_ids = sorted(
        identity for identity, indexes in ids.items() if len(indexes) > 1
    )
    warnings = []
    if duplicate_ids:
        warnings.append(
            {
                "code": "duplicate_model_ids",
                "message": "来源列表包含重复模型编号，请检查分页是否重复",
                "model_ids": duplicate_ids,
            }
        )
    if order_mismatch_groups:
        warnings.append(
            {
                "code": "label_order_mismatch",
                "message": "部分模型标签集合相同但原始顺序不同；不能据此直接复用类别编号或训练权重",
                "group_count": order_mismatch_groups,
            }
        )
    if fallback_count:
        warnings.append(
            {
                "code": "label_key_missing",
                "message": "部分标签没有英文值，比较时使用名称；需确认其含义后再自动匹配业务标签",
                "label_occurrence_count": fallback_count,
            }
        )
    undated = sum(model["published_timestamp"] is None for model in models)
    if undated:
        warnings.append(
            {
                "code": "model_time_missing",
                "message": "部分模型没有有效发布时间和创建时间，组内放在有日期模型之后并按编号排序",
                "model_count": undated,
            }
        )
    summary = {
        "total_models": len(models),
        "label_group_count": len(label_groups),
        "identical_label_group_count": len(repeated),
        "models_in_identical_groups": sum(group["model_count"] for group in repeated),
        "extra_versions": sum(group["model_count"] - 1 for group in repeated),
        "shared_label_model_count": sum(
            bool(model["related_model_count"]) for model in models
        ),
        "related_pair_count": related_pairs,
        "identical_pair_count": identical_pairs,
        "partial_overlap_pair_count": related_pairs - identical_pairs,
        "shared_label_count": len(shared_labels),
        "order_mismatch_group_count": order_mismatch_groups,
        "unlabeled_model_count": sum(not model["labels"] for model in models),
        "undated_model_count": undated,
        "duplicate_model_id_count": len(duplicate_ids),
        "published_time_count": sum(
            model["version_time_source"] == "published_at" for model in models
        ),
        "created_time_fallback_count": sum(
            model["version_time_source"] == "created_at" for model in models
        ),
        "zip_download_url_count": sum(
            model["has_zip_download_url"] for model in models
        ),
        "in_use_count": sum(model["in_use"] for model in models),
    }
    return {
        "summary": summary,
        "models": models,
        "label_groups": label_groups,
        "shared_labels": shared_labels,
        "warnings": warnings,
    }
