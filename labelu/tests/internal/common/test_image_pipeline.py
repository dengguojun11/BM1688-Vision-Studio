"""图像导入流水线：批处理元数据与暂存清理。"""
from __future__ import annotations

import gc
from pathlib import Path

import numpy as np
import pytest

from labelu.internal.common.image_pipeline import staging
from labelu.internal.common.image_pipeline.batch import BatchItem, run_import_batch


def test_staging_save_load_and_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.image_pipeline.staging.settings.MEDIA_ROOT",
        tmp_path,
    )
    feat = np.ones(24, dtype=np.float32)
    staging.save_feature(task_id=9, attachment_id=101, feature=feat)
    del feat
    gc.collect()
    loaded = staging.load_feature(9, 101)
    assert loaded is not None
    assert loaded.shape[0] == 24
    del loaded
    staging.clear_staging_for_attachments(9, [101])
    assert not staging.staging_path(9, 101).is_file()


def test_run_import_batch_assigns_scene_and_cleans_memory():
    pytest.importorskip("sklearn")
    n = 6
    items = [
        BatchItem(
            file_id=i + 1,
            path=Path(f"img_{i}.jpg"),
            filename=f"img_{i:04d}.jpg",
            feature=np.random.RandomState(i).rand(24).astype(np.float32),
        )
        for i in range(n)
    ]
    config = {
        "import_steps": ["cluster", "remove_outliers", "subscenes"],
        "import_outlier_action": "tag",
        "clustering": {"num_clusters": 2, "auto_cluster": False},
        "outliers": {"ratio": 0.1},
        "subscenes": {"similarity_threshold": 0.85, "method": "hist"},
    }
    meta = run_import_batch(items, config)
    del items
    gc.collect()
    assert len(meta) == n
    assert all("scene_id" in meta[k] for k in meta)
    assert all("is_outlier" in meta[k] for k in meta)
