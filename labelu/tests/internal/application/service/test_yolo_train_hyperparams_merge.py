"""多卡训练时超参合并与失败重试规则。"""
import pytest

from labelu.internal.application.service.yolo_train import (
    _adjust_batch_for_single_sample_tail,
    _ensure_safe_final_train_batch,
    _is_multi_gpu_device,
    _merge_train_hyperparams,
    _model_train_with_retry,
)


def test_is_multi_gpu_device():
    assert _is_multi_gpu_device("0,1") is True
    assert _is_multi_gpu_device("0 1") is True
    assert _is_multi_gpu_device("0") is False
    assert _is_multi_gpu_device("cpu") is False


def test_merge_train_hyperparams_strips_workers_amp_on_multi_gpu(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 0)
    monkeypatch.setattr(settings, "YOLO_TRAIN_AMP", False)
    base = {"epochs": 50}
    extra = {"workers": 8, "amp": True, "batch": 4}
    merged = _merge_train_hyperparams(
        base,
        extra,
        train_device="0,1",
        job_id=1,
    )
    assert merged["workers"] == 0
    assert merged["amp"] is False
    assert merged["batch"] == 4
    assert merged["epochs"] == 50


def test_merge_train_hyperparams_forces_zero_workers_even_when_base_has_eight(monkeypatch):
    """多卡时即使 base / .env 带 workers=8，也必须压回 0。"""
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 8)
    monkeypatch.setattr(settings, "YOLO_TRAIN_AMP", True)
    base = {"epochs": 50, "workers": 8, "amp": True}
    merged = _merge_train_hyperparams(
        base,
        {},
        train_device="0,1",
        job_id=3,
    )
    assert merged["workers"] == 0
    assert merged["amp"] is False


def test_merge_train_hyperparams_uses_requested_workers_on_single_gpu(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 0)
    base = {"workers": 0, "amp": False}
    extra = {"workers": 8, "cache": "ram", "lr0": 0.01}
    merged = _merge_train_hyperparams(
        base,
        extra,
        train_device="0",
        job_id=2,
    )
    assert merged["workers"] == 8
    assert merged["cache"] is False
    assert merged["lr0"] == 0.01


def test_merge_train_hyperparams_caps_requested_workers_on_single_gpu(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 0)
    merged = _merge_train_hyperparams(
        {"epochs": 50},
        {"workers": 24},
        train_device="1",
        job_id=6,
    )

    assert merged["workers"] == 8


def test_merge_train_hyperparams_sets_safe_single_gpu_runtime(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 0)
    monkeypatch.setattr(settings, "YOLO_TRAIN_AMP", True)
    monkeypatch.setattr("labelu.internal.application.service.yolo_train.os.cpu_count", lambda: 32)
    merged = _merge_train_hyperparams(
        {"epochs": 50, "imgsz": 640},
        {},
        train_device="1",
        job_id=4,
    )
    assert merged["workers"] == 0
    assert merged["amp"] is True
    assert merged["cache"] is False


def test_merge_train_hyperparams_disables_cache_on_multi_gpu(monkeypatch):
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 8)
    monkeypatch.setattr(settings, "YOLO_TRAIN_AMP", True)
    merged = _merge_train_hyperparams(
        {"epochs": 50, "workers": 8, "amp": True, "cache": "ram"},
        {"cache": "disk"},
        train_device="0,1",
        job_id=5,
    )
    assert merged["workers"] == 0
    assert merged["amp"] is False
    assert merged["cache"] is False


def test_is_retryable_train_failure():
    from labelu.internal.application.service.yolo_train import _is_retryable_train_failure

    assert _is_retryable_train_failure(RuntimeError("CUDA out of memory")) is True
    assert _is_retryable_train_failure(RuntimeError("CUDNN_STATUS_ALLOC_FAILED")) is True
    assert _is_retryable_train_failure(RuntimeError("NCCL error")) is False
    assert (
        _is_retryable_train_failure(
            RuntimeError("torch.distributed.elastic.multiprocessing returned non-zero exit status 1")
        )
        is False
    )
    assert _is_retryable_train_failure(ValueError("bad yaml")) is False


def test_model_train_does_not_lower_batch_for_ddp_subprocess_failure():
    class _Log:
        def __init__(self):
            self.blocks = []
            self.lines = []

        def write(self, msg):
            self.lines.append(msg)

        def write_block(self, name, msg):
            self.blocks.append((name, msg))

    class _Model:
        def __init__(self):
            self.batches = []

        def train(self, **kw):
            self.batches.append(kw["batch"])
            raise RuntimeError(
                "torch.distributed.elastic.multiprocessing.errors.ChildFailedError: "
                "returned non-zero exit status 1"
            )

    model = _Model()
    with pytest.raises(RuntimeError):
        _model_train_with_retry(
            model,
            {"batch": 128, "device": "0,1", "imgsz": 640},
            train_log=_Log(),
            job_id=127,
        )

    assert model.batches == [128]


def test_model_train_lowers_batch_for_cuda_oom():
    class _Log:
        def write(self, msg):
            pass

        def write_block(self, name, msg):
            pass

    class _Model:
        def __init__(self):
            self.batches = []

        def train(self, **kw):
            self.batches.append(kw["batch"])
            if kw["batch"] == 128:
                raise RuntimeError("CUDA out of memory")
            return "ok"

    model = _Model()
    result = _model_train_with_retry(
        model,
        {"batch": 128, "device": "0", "imgsz": 640},
        train_log=_Log(),
        job_id=128,
    )

    assert result == "ok"
    assert model.batches == [128, 64]


def test_apply_large_imgsz_safe_train_profile_single_gpu_and_batch_cap(monkeypatch):
    from labelu.internal.application.service.yolo_train import (
        _apply_large_imgsz_safe_train_profile,
    )
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_AMP", True)
    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 4)
    monkeypatch.setattr(
        "labelu.internal.common.yolo_train_gpu_allocator.choose_single_gpu_device",
        lambda **kwargs: "1",
    )

    class _Log:
        def __init__(self):
            self.lines = []

        def write(self, msg):
            self.lines.append(msg)

    log = _Log()
    train_kw = {
        "imgsz": 1280,
        "batch": 16,
        "multi_scale": True,
        "device": "0,1",
        "workers": 8,
    }
    hp_extra = {"amp": True, "batch": 16, "multi_scale": True}
    device = _apply_large_imgsz_safe_train_profile(
        train_kw,
        hp_extra,
        train_device="0,1",
        job_id=97,
        train_log=log,
        train_sample_count=0,
    )
    assert device == "1"
    assert train_kw["device"] == "1"
    assert train_kw["batch"] == 4
    assert train_kw["workers"] == 4
    assert train_kw.get("amp") is False
    assert any("single GPU" in ln for ln in log.lines)


def test_adjust_batch_for_single_sample_tail():
    assert _adjust_batch_for_single_sample_tail(batch=4, train_sample_count=4881) == 3
    assert _adjust_batch_for_single_sample_tail(batch=8, train_sample_count=4844) == 8
    assert _adjust_batch_for_single_sample_tail(batch=1, train_sample_count=4881) == 1


def test_apply_large_imgsz_safe_train_profile_avoids_single_sample_tail(monkeypatch):
    from labelu.internal.application.service.yolo_train import (
        _apply_large_imgsz_safe_train_profile,
    )
    from labelu.internal.common.config import settings

    monkeypatch.setattr(settings, "YOLO_TRAIN_AMP", False)
    monkeypatch.setattr(settings, "YOLO_TRAIN_WORKERS", 4)

    class _Log:
        def __init__(self):
            self.lines = []

        def write(self, msg):
            self.lines.append(msg)

    log = _Log()
    train_kw = {
        "imgsz": 1280,
        "batch": 4,
        "multi_scale": True,
        "device": "0",
        "workers": 4,
        "cache": False,
        "amp": False,
    }
    device = _apply_large_imgsz_safe_train_profile(
        train_kw,
        {},
        train_device="0",
        job_id=166,
        train_log=log,
        train_sample_count=4881,
    )

    assert device == "0"
    assert train_kw["batch"] == 3
    assert any("avoid single-sample tail" in ln for ln in log.lines)


def test_ensure_safe_final_train_batch_adjusts_tail_batch():
    class _Log:
        def __init__(self):
            self.lines = []

        def write(self, msg):
            self.lines.append(msg)

    log = _Log()
    train_kw = {"batch": 4}
    _ensure_safe_final_train_batch(
        train_kw,
        train_sample_count=4877,
        job_id=169,
        train_log=log,
    )

    assert train_kw["batch"] == 3
    assert any("train_final_batch_guard" in ln for ln in log.lines)
