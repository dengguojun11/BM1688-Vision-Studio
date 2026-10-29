"""本地 YOLO 训练 GPU 自动分配：按任务负载选择单卡或双卡，小任务可并行占不同卡。"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from loguru import logger

from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.common.config import settings

_ALLOC_LOCK = threading.Lock()
# job_id -> 占用的 GPU 编号（双卡作业为 (0, 1)）
_JOB_GPU_ALLOCATIONS: Dict[int, Tuple[int, ...]] = {}

_MODEL_SIZE_WEIGHT: Dict[str, float] = {
    "n": 1.0,
    "s": 1.35,
    "m": 2.0,
    "l": 3.0,
    "x": 4.5,
}
# load_score >= 阈值时使用双卡 DDP（imgsz>=1024 仍强制单卡，见 job_needs_dual_gpu）
_DEFAULT_DUAL_GPU_SCORE_THRESHOLD = 4.0


@dataclass(frozen=True)
class GpuAllocationPlan:
    device: str
    gpu_indices: Tuple[int, ...]
    load_score: float
    tier: str  # small | medium | large
    reason: str


def yolo_train_gpu_auto_alloc_enabled() -> bool:
    """多卡池 + 开关开启时启用按作业分配 GPU。"""
    if not bool(getattr(settings, "YOLO_TRAIN_GPU_AUTO_ALLOC", True)):
        return False
    explicit = (settings.YOLO_TRAIN_DEVICE or "").strip().lower()
    if explicit == "cpu":
        return False
    pool = train_gpu_pool_indices()
    return len(pool) >= 2


def _train_gpu_indices_from_settings() -> List[int]:
    from labelu.internal.application.service.yolo_train import (
        _cuda_runtime_snapshot,
        _is_cpu_train_device,
        _requested_gpu_indices,
        _resolve_yolo_train_device,
    )

    explicit = (settings.YOLO_TRAIN_DEVICE or "").strip()
    if explicit and explicit.lower() not in ("auto",):
        if _is_cpu_train_device(explicit):
            return []
        try:
            return _requested_gpu_indices(explicit)
        except ValueError:
            return []
    device = _resolve_yolo_train_device()
    if _is_cpu_train_device(device):
        cuda_ok, device_count = _cuda_runtime_snapshot()
        if cuda_ok and device_count > 0:
            return list(range(device_count))
        return []
    try:
        return _requested_gpu_indices(device)
    except ValueError:
        return []


def train_gpu_pool_indices() -> Tuple[int, ...]:
    indices = _train_gpu_indices_from_settings()
    return tuple(sorted(set(indices)))


def parse_device_indices(device: str) -> Tuple[int, ...]:
    from labelu.internal.application.service.yolo_train import _requested_gpu_indices

    token = (device or "").strip()
    if not token or token.lower() == "cpu":
        return ()
    try:
        return tuple(sorted(_requested_gpu_indices(token)))
    except ValueError:
        return ()


def device_to_str(indices: Iterable[int]) -> str:
    return ",".join(str(i) for i in sorted(set(indices)))


def _model_size_weight(model_weights: str) -> float:
    name = (model_weights or "yolo11n.pt").lower()
    m = re.search(r"yolo11([nslmx])", name)
    if m:
        return _MODEL_SIZE_WEIGHT.get(m.group(1), 2.0)
    if name.endswith(".pt"):
        return 2.5
    return 2.0


def _batch_from_cmd(cmd: CreateYoloTrainJobCommand) -> int:
    hp = cmd.hyperparams or {}
    try:
        return max(1, int(hp.get("batch") or 16))
    except (TypeError, ValueError):
        return 16


def estimate_job_load_score(
    cmd: CreateYoloTrainJobCommand, *, sample_count: int = 0
) -> float:
    """估算训练负载，用于单卡/双卡决策（越大越倾向双卡）。"""
    imgsz = max(320, int(cmd.imgsz or 640))
    model_w = _model_size_weight(cmd.model_weights or "yolo11n.pt")
    imgsz_w = (imgsz / 640.0) ** 2
    batch_w = _batch_from_cmd(cmd) / 16.0
    score = model_w * imgsz_w * batch_w
    total = max(0, int(sample_count or 0))
    if total >= 8000:
        score *= 1.35
    elif total >= 3000:
        score *= 1.15
    elif total >= 1000:
        score *= 1.05
    return round(score, 3)


def classify_job_gpu_tier(
    cmd: CreateYoloTrainJobCommand, *, sample_count: int = 0
) -> str:
    """Classify jobs for throughput-first scheduling on a multi-GPU server."""
    imgsz = max(320, int(cmd.imgsz or 640))
    score = estimate_job_load_score(cmd, sample_count=sample_count)
    threshold = _dual_gpu_score_threshold()
    total = max(0, int(sample_count or 0))

    # Large images are safer on a single GPU. Keep them single-card by default
    # while still separating them from tiny jobs for placement decisions.
    if imgsz >= 1024:
        return "medium"
    if score >= max(threshold * 1.5, 6.0):
        return "large"
    if total >= 8000 and score >= threshold:
        return "large"
    if score >= max(threshold * 0.75, 2.5):
        return "medium"
    if total >= 3000:
        return "medium"
    return "small"


def job_needs_dual_gpu(
    cmd: CreateYoloTrainJobCommand,
    *,
    sample_count: int = 0,
    queued_job_count: int = 0,
) -> bool:
    """Only truly heavy jobs should reserve both GPUs, and only when queue is short."""
    if not yolo_train_gpu_auto_alloc_enabled() or len(train_gpu_pool_indices()) < 2:
        return False
    if classify_job_gpu_tier(cmd, sample_count=sample_count) != "large":
        return False
    if int(queued_job_count or 0) > 0:
        return False
    if bool(getattr(settings, "YOLO_TRAIN_GPU_PREFER_DUAL", True)):
        return True
    threshold = float(
        getattr(settings, "YOLO_TRAIN_GPU_DUAL_SCORE_THRESHOLD", _DEFAULT_DUAL_GPU_SCORE_THRESHOLD)
        or _DEFAULT_DUAL_GPU_SCORE_THRESHOLD
    )
    return estimate_job_load_score(cmd, sample_count=sample_count) >= threshold


def _dual_gpu_score_threshold() -> float:
    return float(
        getattr(settings, "YOLO_TRAIN_GPU_DUAL_SCORE_THRESHOLD", _DEFAULT_DUAL_GPU_SCORE_THRESHOLD)
        or _DEFAULT_DUAL_GPU_SCORE_THRESHOLD
    )


def _gpus_meet_free_threshold(
    indices: Iterable[int],
    free_mb: Dict[int, int],
    min_free_mb: int,
) -> bool:
    if min_free_mb <= 0:
        return True
    for idx in indices:
        free = free_mb.get(idx)
        if free is None:
            continue
        if free < min_free_mb:
            return False
    return True


def _occupied_gpu_set(occupied_by_gpu: Dict[int, Optional[int]]) -> Set[int]:
    return {gpu for gpu, jid in occupied_by_gpu.items() if jid is not None}


def build_gpu_occupancy(
    *,
    exclude_job_id: Optional[int] = None,
    extra_from_smi: Optional[Dict[int, Optional[int]]] = None,
) -> Dict[int, Optional[int]]:
    """gpu_index -> job_id；合并内存登记与 nvidia-smi 探测（重启后兜底）。"""
    pool = train_gpu_pool_indices()
    occupied: Dict[int, Optional[int]] = {idx: None for idx in pool}
    with _ALLOC_LOCK:
        for jid, indices in _JOB_GPU_ALLOCATIONS.items():
            if exclude_job_id is not None and int(jid) == int(exclude_job_id):
                continue
            for idx in indices:
                if idx in occupied:
                    occupied[idx] = int(jid)
    if extra_from_smi:
        for idx, jid in extra_from_smi.items():
            if idx not in occupied or jid is None:
                continue
            if occupied[idx] is None:
                occupied[idx] = int(jid)
    return occupied


def register_job_gpu_allocation(job_id: int, indices: Tuple[int, ...]) -> None:
    with _ALLOC_LOCK:
        _JOB_GPU_ALLOCATIONS[int(job_id)] = tuple(sorted(set(indices)))


def release_job_gpu_allocation(job_id: int) -> None:
    with _ALLOC_LOCK:
        _JOB_GPU_ALLOCATIONS.pop(int(job_id), None)


def get_registered_device_for_job(job_id: int) -> Optional[str]:
    with _ALLOC_LOCK:
        indices = _JOB_GPU_ALLOCATIONS.get(int(job_id))
    if not indices:
        return None
    return device_to_str(indices)


def gpu_allocation_path(job_id: int) -> Path:
    from labelu.internal.application.service.yolo_train import _job_work_root

    return _job_work_root(int(job_id)) / "gpu_allocation.json"


def persist_job_gpu_allocation(job_id: int, plan: GpuAllocationPlan) -> None:
    path = gpu_allocation_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "device": plan.device,
        "gpu_indices": list(plan.gpu_indices),
        "load_score": plan.load_score,
        "tier": plan.tier,
        "reason": plan.reason,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    register_job_gpu_allocation(job_id, plan.gpu_indices)


def load_persisted_gpu_allocation(job_id: int) -> Optional[GpuAllocationPlan]:
    path = gpu_allocation_path(job_id)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    device = str(raw.get("device") or "").strip()
    indices = tuple(int(i) for i in (raw.get("gpu_indices") or parse_device_indices(device)))
    if not device or not indices:
        return None
    return GpuAllocationPlan(
        device=device,
        gpu_indices=indices,
        load_score=float(raw.get("load_score") or 0),
        tier=str(raw.get("tier") or "small"),
        reason=str(raw.get("reason") or "restored"),
    )


def effective_min_free_mb_for_job(
    cmd: CreateYoloTrainJobCommand,
    *,
    base_min_mb: int,
    sample_count: int = 0,
) -> int:
    """小任务用更低显存门槛，避免 LocateAnything 占 GPU0 时误挡 GPU1/可训练卡。"""
    floor_small = int(getattr(settings, "YOLO_TRAIN_GPU_MIN_FREE_MB_SMALL", 4096) or 4096)
    if classify_job_gpu_tier(cmd, sample_count=sample_count) == "large":
        return max(floor_small, int(base_min_mb))
    return max(2048, min(int(base_min_mb), floor_small))


def _candidate_gpu_sort_key(
    gpu_index: int,
    *,
    free_mb: Dict[int, int],
) -> Tuple[int, int]:
    free = int(free_mb.get(gpu_index, -1))
    return (-free, gpu_index)


def _choose_best_single_gpu(
    indices: Iterable[int],
    *,
    free_mb: Dict[int, int],
) -> Optional[int]:
    candidates = list(dict.fromkeys(int(i) for i in indices))
    if not candidates:
        return None
    candidates.sort(key=lambda idx: _candidate_gpu_sort_key(idx, free_mb=free_mb))
    return candidates[0]


def _query_gpu_free_memory_mb() -> Dict[int, int]:
    try:
        proc = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    out: Dict[int, int] = {}
    for line in (proc.stdout or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            out[int(parts[0])] = int(float(parts[1]))
        except ValueError:
            continue
    return out


def choose_single_gpu_device(
    *,
    candidate_indices: Optional[Iterable[int]] = None,
    occupied_by_gpu: Optional[Dict[int, Optional[int]]] = None,
    free_mb: Optional[Dict[int, int]] = None,
    exclude_job_id: Optional[int] = None,
) -> Optional[str]:
    pool = tuple(sorted(set(candidate_indices or train_gpu_pool_indices())))
    if not pool:
        return None
    occupancy = (
        occupied_by_gpu
        if occupied_by_gpu is not None
        else build_gpu_occupancy(exclude_job_id=exclude_job_id)
    )
    candidates = [i for i in pool if occupancy.get(i) in (None, exclude_job_id)]
    if not candidates:
        candidates = list(pool)
    chosen = _choose_best_single_gpu(
        candidates,
        free_mb=free_mb if free_mb is not None else _query_gpu_free_memory_mb(),
    )
    if chosen is None:
        return None
    return str(chosen)


def plan_gpu_allocation(
    cmd: CreateYoloTrainJobCommand,
    *,
    occupied_by_gpu: Dict[int, Optional[int]],
    free_mb: Dict[int, int],
    min_free_mb: int,
    sample_count: int = 0,
    queued_job_count: int = 0,
) -> Optional[GpuAllocationPlan]:
    """为作业挑选 GPU；无可用槽位时返回 None。"""
    pool = train_gpu_pool_indices()
    if not pool:
        return None

    score = estimate_job_load_score(cmd, sample_count=sample_count)
    tier = classify_job_gpu_tier(cmd, sample_count=sample_count)
    need_dual = job_needs_dual_gpu(
        cmd,
        sample_count=sample_count,
        queued_job_count=queued_job_count,
    )
    busy = _occupied_gpu_set(occupied_by_gpu)

    if need_dual:
        if len(pool) < 2:
            need_dual = False
        elif any(occupied_by_gpu.get(i) for i in pool):
            return None
        elif not _gpus_meet_free_threshold(pool, free_mb, min_free_mb):
            return None
        else:
            if bool(getattr(settings, "YOLO_TRAIN_GPU_PREFER_DUAL", True)):
                reason = (
                    f"dual_gpu prefer_dual model={cmd.model_weights} imgsz={cmd.imgsz}"
                )
            else:
                reason = (
                    f"load_score={score}>={_dual_gpu_score_threshold()} "
                    f"model={cmd.model_weights} imgsz={cmd.imgsz}"
                )
            return GpuAllocationPlan(
                device=device_to_str(pool),
                gpu_indices=pool,
                load_score=score,
                tier="large",
                reason=reason,
            )

    free_gpus = [i for i in pool if occupied_by_gpu.get(i) is None and i not in busy]
    free_gpus = [
        i
        for i in free_gpus
        if _gpus_meet_free_threshold([i], free_mb, min_free_mb)
    ]
    if not free_gpus:
        return None

    chosen = _choose_best_single_gpu(free_gpus, free_mb=free_mb)
    if chosen is None:
        return None
    reason = (
        f"single_gpu tier={tier} score={score} model={cmd.model_weights} imgsz={cmd.imgsz}"
    )
    return GpuAllocationPlan(
        device=str(chosen),
        gpu_indices=(chosen,),
        load_score=score,
        tier=tier,
        reason=reason,
    )


def try_allocate_gpu_for_job(
    job_id: int,
    cmd: CreateYoloTrainJobCommand,
    *,
    occupied_by_gpu: Dict[int, Optional[int]],
    free_mb: Dict[int, int],
    min_free_mb: int,
    sample_count: int = 0,
    queued_job_count: int = 0,
) -> Optional[GpuAllocationPlan]:
    persisted = load_persisted_gpu_allocation(job_id)
    if persisted:
        indices = persisted.gpu_indices
        if all(occupied_by_gpu.get(i) in (None, int(job_id)) for i in indices):
            if _gpus_meet_free_threshold(indices, free_mb, min_free_mb):
                register_job_gpu_allocation(job_id, indices)
                return persisted
        return None

    plan = plan_gpu_allocation(
        cmd,
        occupied_by_gpu=occupied_by_gpu,
        free_mb=free_mb,
        min_free_mb=min_free_mb,
        sample_count=sample_count,
        queued_job_count=queued_job_count,
    )
    if plan:
        persist_job_gpu_allocation(job_id, plan)
        logger.info(
            "YOLO train job {} GPU alloc: device={} tier={} score={} ({})",
            job_id,
            plan.device,
            plan.tier,
            plan.load_score,
            plan.reason,
        )
    return plan


def allocation_unavailable_reason(
    cmd: CreateYoloTrainJobCommand,
    *,
    occupied_by_gpu: Dict[int, Optional[int]],
    free_mb: Dict[int, int],
    min_free_mb: int,
    sample_count: int = 0,
) -> str:
    if job_needs_dual_gpu(cmd, sample_count=sample_count):
        busy = [i for i in train_gpu_pool_indices() if occupied_by_gpu.get(i)]
        if busy:
            return f"大任务需双卡，当前 GPU {busy} 已被占用"
        for idx in train_gpu_pool_indices():
            free = free_mb.get(idx)
            if free is not None and free < min_free_mb:
                return f"大任务需双卡，GPU {idx} 可用显存 {free}MB < {min_free_mb}MB"
        return "大任务需双卡，当前无足够连续空闲资源"
    busy = [i for i in train_gpu_pool_indices() if occupied_by_gpu.get(i)]
    if len(busy) >= len(train_gpu_pool_indices()):
        return f"训练 GPU {list(train_gpu_pool_indices())} 均已占用（{len(busy)} 槽满）"
    for idx in train_gpu_pool_indices():
        if occupied_by_gpu.get(idx):
            continue
        free = free_mb.get(idx)
        if free is not None and free < min_free_mb:
            return f"GPU {idx} 可用显存 {free}MB < 自动阈值 {min_free_mb}MB"
    if busy:
        return f"训练 GPU {list(train_gpu_pool_indices())} 均已占用（{len(busy)} 槽满）"
    return "等待训练 GPU 资源"


def _device_from_train_args_yaml(job_id: int) -> Optional[str]:
    args_path = gpu_allocation_path(job_id).parent / "train" / "args.yaml"
    if not args_path.is_file():
        return None
    try:
        text = args_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("device:"):
            continue
        raw = stripped.split(":", 1)[1].strip().strip("'\"")
        return raw or None
    return None


def gpu_display_fields_for_job(job_id: int) -> dict[str, Optional[str]]:
    """供 API/页面展示：单卡、双卡 DDP 或待分配。"""
    plan = load_persisted_gpu_allocation(job_id)
    device = plan.device if plan else None
    tier = plan.tier if plan else None
    if not device:
        device = _device_from_train_args_yaml(job_id)
    if not device:
        return {
            "train_gpu_device": None,
            "train_gpu_mode": None,
            "train_gpu_label": None,
            "train_gpu_tier": tier,
        }
    token = device.strip().lower()
    if token == "cpu":
        return {
            "train_gpu_device": "cpu",
            "train_gpu_mode": "cpu",
            "train_gpu_label": "CPU",
            "train_gpu_tier": tier,
        }
    indices = parse_device_indices(device)
    if not indices:
        return {
            "train_gpu_device": device,
            "train_gpu_mode": None,
            "train_gpu_label": None,
            "train_gpu_tier": tier,
        }
    if len(indices) >= 2:
        label = f"双卡 DDP ({device_to_str(indices)})"
        mode = "dual"
    else:
        label = f"单卡 GPU{indices[0]}"
        mode = "single"
    return {
        "train_gpu_device": device_to_str(indices),
        "train_gpu_mode": mode,
        "train_gpu_label": label,
        "train_gpu_tier": tier,
    }
