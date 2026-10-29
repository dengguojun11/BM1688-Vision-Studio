from __future__ import annotations

import gc
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from labelu.internal.common.image_pipeline.features import (
    extract_features,
    image_similarity,
)


@dataclass
class BatchItem:
    file_id: int
    path: Path
    filename: str
    feature: np.ndarray | None = None


def _natural_sort_key(text: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", text)]


def _number_from_stem(name: str) -> int:
    m = re.search(r"\d+", name)
    return int(m.group()) if m else 0


def run_import_batch(
    items: list[BatchItem],
    config: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    """
    对一批本地图片做聚类 / 离群 / 子场景分析，仅返回元数据，不移动或重命名文件。
    函数返回前释放中间矩阵与模型占用的内存。
    """
    steps = config.get("import_steps") or ["cluster", "remove_outliers", "subscenes"]
    outlier_action = (config.get("import_outlier_action") or "tag").lower()
    meta: dict[int, dict[str, Any]] = {
        it.file_id: {"version": 1, "is_outlier": False} for it in items
    }
    if not items:
        return meta

    features_list: list[np.ndarray] = []
    valid_items: list[BatchItem] = []

    try:
        for it in items:
            feat = it.feature
            if feat is None:
                feat = extract_features(it.path)
            if feat is not None:
                features_list.append(feat)
                valid_items.append(it)
            else:
                meta[it.file_id]["feature_error"] = True

        if len(valid_items) < 1:
            return meta

        features = np.stack(features_list, axis=0)
        del features_list

        labels = np.zeros(len(valid_items), dtype=int)
        if "cluster" in steps and len(valid_items) >= 2:
            labels = _cluster_labels(features, config)
            for it, lab in zip(valid_items, labels):
                meta[it.file_id]["scene_id"] = f"scene_{int(lab):02d}"

        if "remove_outliers" in steps:
            _apply_outliers(valid_items, labels, features, config, meta, outlier_action)

        if "subscenes" in steps:
            _apply_subscenes(valid_items, labels, config, meta)

        return meta
    finally:
        gc.collect()


def _cluster_labels(features: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    scaled = scaler.fit_transform(features)
    n = len(features)
    num_clusters = config.get("clustering", {}).get("num_clusters")
    auto_cluster = config.get("clustering", {}).get("auto_cluster", True)
    if num_clusters is None and auto_cluster:
        num_clusters = max(2, int(np.sqrt(n / 2)))
    elif num_clusters is None:
        num_clusters = 2
    num_clusters = min(int(num_clusters), n)
    if num_clusters < 2:
        return np.zeros(n, dtype=int)
    kmeans = KMeans(n_clusters=num_clusters, random_state=42, n_init=10)
    labels = kmeans.fit_predict(scaled)
    del scaler, scaled, kmeans
    return labels


def _apply_outliers(
    valid_items: list[BatchItem],
    labels: np.ndarray,
    features: np.ndarray,
    config: dict[str, Any],
    meta: dict[int, dict[str, Any]],
    outlier_action: str,
) -> None:
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler

    ratio = float(config.get("outliers", {}).get("ratio", 0.1))
    ratio = min(max(ratio, 0.01), 0.49)

    scene_groups: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        scene_groups.setdefault(int(lab), []).append(idx)

    for indices in scene_groups.values():
        if len(indices) < 3:
            continue
        sub_feat = features[indices]
        scaler = StandardScaler()
        X = scaler.fit_transform(sub_feat)
        iso = IsolationForest(contamination=ratio, random_state=42)
        preds = iso.fit_predict(X)
        del scaler, X, iso
        for local_i, pred in enumerate(preds):
            if pred == -1:
                fid = valid_items[indices[local_i]].file_id
                meta[fid]["is_outlier"] = True
                if outlier_action == "reject":
                    meta[fid]["rejected"] = True


def _apply_subscenes(
    valid_items: list[BatchItem],
    labels: np.ndarray,
    config: dict[str, Any],
    meta: dict[int, dict[str, Any]],
) -> None:
    threshold = float(config.get("subscenes", {}).get("similarity_threshold", 0.85))
    method = config.get("subscenes", {}).get("method", "hist")

    scene_groups: dict[int, list[int]] = {}
    for idx, lab in enumerate(labels):
        scene_groups.setdefault(int(lab), []).append(idx)

    for scene_lab, indices in scene_groups.items():
        group = [valid_items[i] for i in indices]
        group.sort(key=lambda x: _natural_sort_key(x.filename))
        if len(group) < 2:
            if len(group) == 1:
                num = _number_from_stem(Path(group[0].filename).stem)
                meta[group[0].file_id]["subscene"] = {
                    "scene_id": meta[group[0].file_id].get("scene_id"),
                    "start_num": num,
                    "end_num": num,
                    "count": 1,
                }
            continue

        segments: list[list[BatchItem]] = []
        current = [group[0]]
        for i in range(1, len(group)):
            sim = image_similarity(group[i - 1].path, group[i].path, method=method)
            if sim >= threshold:
                current.append(group[i])
            else:
                segments.append(current)
                current = [group[i]]
        segments.append(current)

        for seg in segments:
            first_num = _number_from_stem(Path(seg[0].filename).stem)
            last_num = _number_from_stem(Path(seg[-1].filename).stem)
            block = {
                "scene_id": meta[seg[0].file_id].get("scene_id", f"scene_{scene_lab:02d}"),
                "start_num": first_num,
                "end_num": last_num,
                "count": len(seg),
            }
            for it in seg:
                meta[it.file_id]["subscene"] = block
