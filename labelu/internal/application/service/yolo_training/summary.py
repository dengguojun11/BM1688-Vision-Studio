"""YOLO response shaping and metrics summarization helpers."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


def format_train_failure_detail(
    job_work_root_fn: Callable[[int], Path],
    job_id: int,
    exc: Exception,
) -> str:
    base = str(exc).strip() or exc.__class__.__name__
    train_dir = job_work_root_fn(job_id) / "train"
    if train_dir.is_dir():
        return f"{base[:420]} | 详见 {train_dir}"
    return base[:500]


def read_train_phase(
    job_work_root_fn: Callable[[int], Path],
    train_phase_labels: dict[str, str],
    job_id: int,
) -> tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
    path = job_work_root_fn(job_id) / "phase.json"
    if not path.is_file():
        return None, None, None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None, None, None
    if not isinstance(data, dict):
        return None, None, None, None
    phase = str(data.get("phase") or "").strip() or None
    detail = str(data.get("detail") or "").strip()
    runtime_pid: Optional[int] = None
    try:
        if data.get("runtime_pid") is not None:
            runtime_pid = int(data.get("runtime_pid"))
    except (TypeError, ValueError):
        runtime_pid = None
    label = train_phase_labels.get(phase or "", phase or "")
    if detail:
        label = f"{label}?{detail}?"
    return phase, label or None, runtime_pid, detail or None


TRAIN_STEP_SEQUENCE: list[tuple[str, str]] = [
    ("preparing_dataset", "1. 准备数据集"),
    ("dataset_ready", "2. 数据集校验完成"),
    ("loading_model", "3. 加载初始权重"),
    ("running", "4. 执行训练"),
    ("finalizing", "5. 汇总评估结果"),
    ("completed", "6. 训练完成"),
]

TRAIN_PHASE_STEP_KEY: dict[str, str] = {
    "preparing_dataset": "preparing_dataset",
    "dataset_ready": "dataset_ready",
    "loading_model": "loading_model",
    "running": "running",
    "restarting": "running",
    "finalizing": "finalizing",
    "val_per_class": "finalizing",
    "test_val": "finalizing",
    "completed": "completed",
    "failed": "running",
    "cancelled": "running",
}


def read_dataset_validation_report(
    job_work_root_fn: Callable[[int], Path],
    job_id: int,
) -> dict[str, Any]:
    report_dir = job_work_root_fn(job_id) / "reports"
    split_summary_path = report_dir / "split_summary.csv"
    class_distribution_path = report_dir / "class_distribution.csv"
    label_errors_path = report_dir / "label_errors.csv"

    out: dict[str, Any] = {
        "exists": False,
        "ok": None,
        "error_count": 0,
        "class_count": None,
        "splits": [],
    }
    splits: list[dict[str, Any]] = []
    if split_summary_path.is_file():
        try:
            with split_summary_path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    split = str(row.get("split") or "").strip()
                    if not split:
                        continue
                    splits.append(
                        {
                            "split": split,
                            "images": int(row.get("images") or 0),
                            "labels_found": int(row.get("labels_found") or 0),
                            "missing_labels": int(row.get("missing_labels") or 0),
                            "boxes": int(row.get("boxes") or 0),
                        }
                    )
        except (OSError, ValueError):
            splits = []
    if splits:
        out["exists"] = True
        out["splits"] = splits

    class_count: Optional[int] = None
    if class_distribution_path.is_file():
        try:
            with class_distribution_path.open(
                "r", encoding="utf-8-sig", newline=""
            ) as f:
                rows = list(csv.DictReader(f))
            class_count = len(rows)
        except OSError:
            class_count = None
    out["class_count"] = class_count
    if class_count is not None:
        out["exists"] = True

    error_count = 0
    if label_errors_path.is_file():
        try:
            with label_errors_path.open("r", encoding="utf-8-sig", newline="") as f:
                error_count = sum(1 for _ in csv.DictReader(f))
        except OSError:
            error_count = 0
        out["exists"] = True
    out["error_count"] = error_count

    if out["exists"]:
        split_ok = all(int(item.get("missing_labels") or 0) == 0 for item in splits)
        out["ok"] = split_ok and error_count == 0
    return out


def dataset_ready_checks_from_report(
    report: dict[str, Any],
) -> list[dict[str, str]]:
    if not report.get("exists"):
        return []

    splits = report.get("splits") or []
    split_bits: list[str] = []
    labels_ok = True
    for item in splits:
        split_name = str(item.get("split") or "").strip() or "unknown"
        images = int(item.get("images") or 0)
        labels_found = int(item.get("labels_found") or 0)
        missing_labels = int(item.get("missing_labels") or 0)
        boxes = int(item.get("boxes") or 0)
        split_bits.append(
            f"{split_name}: 图片 {images}，标签 {labels_found}，缺失 {missing_labels}，框 {boxes}"
        )
        if missing_labels != 0 or images != labels_found:
            labels_ok = False

    class_count = report.get("class_count")
    error_count = int(report.get("error_count") or 0)
    ok = bool(report.get("ok"))
    checks = [
        {
            "key": "dataset_yaml",
            "label": "类别配置文件可读",
            "status": "done" if class_count else "failed",
            "detail": (
                f"已读取 data.yaml，并解析出 {int(class_count)} 个类别"
                if class_count
                else "未读到类别配置文件或未解析出类别"
            ),
        },
        {
            "key": "labels_match_images",
            "label": "train / val / test 图片与标签一一对应",
            "status": "done" if labels_ok else "failed",
            "detail": "；".join(split_bits) if split_bits else "未生成划分统计",
        },
        {
            "key": "label_content_valid",
            "label": "标签内容格式与框坐标合法",
            "status": "done" if error_count == 0 else "failed",
            "detail": (
                "未发现缺失标签、类别越界、列数错误、非数值或归一化越界框"
                if error_count == 0
                else f"发现 {error_count} 条标签错误，请查看 label_errors.csv"
            ),
        },
        {
            "key": "split_overlap",
            "label": "数据划分无 train / val / test 交叉污染",
            "status": "done" if error_count == 0 else "failed",
            "detail": (
                "未发现 train 与 val/test 的重复图片"
                if error_count == 0
                else "若存在 split_overlap 错误，会记录在 label_errors.csv"
            ),
        },
        {
            "key": "validation_conclusion",
            "label": "数据集验收结论",
            "status": "done" if ok else "failed",
            "detail": (
                "本次数据集校验通过，可进入训练" if ok else "本次数据集校验未通过"
            ),
        },
    ]
    return checks


TRAIN_STEP_STRUCTURE: dict[str, dict[str, Any]] = {
    "preparing_dataset": {
        "substeps": [
            (
                "load_context",
                "读取训练任务与合并任务",
                [],
                "从主任务和合并任务中读取 DONE 样本，并校验任务状态、媒体类型、权限和拉框类别是否允许一起训练。",
            ),
            (
                "export_samples",
                "导出 YOLO 图片与标签",
                [
                    ("dataset/images/train|val|test/", "训练图片"),
                    ("dataset/labels/train|val|test/", "YOLO 标签"),
                ],
                "把平台里的原图复制到 dataset/images，并把 rectTool 标注转换成 YOLO txt；明确负样本写空标签，未确认的空框样本会被跳过。",
            ),
            (
                "write_data_yaml",
                "生成 data.yaml",
                [("dataset/data.yaml", "类别与数据集路径配置")],
                "按本次训练的类别顺序写入 data.yaml，声明 train / val / test 路径、类别 names 和 nc。",
            ),
            (
                "split_dataset",
                "切分 train / val / test",
                [],
                "按配置比例随机切分训练集、验证集和测试集，并保证训练集和验证集不会为空。",
            ),
        ],
        "root_suffix": "dataset/",
    },
    "dataset_ready": {
        "substeps": [
            (
                "validate_dataset",
                "校验导出后的数据集",
                [],
                "逐项检查 data.yaml、图片与标签是否一一对应、标签格式是否合法、坐标是否越界，以及不同划分之间是否有图片重叠。",
            ),
            (
                "write_validation_reports",
                "生成数据集校验报告",
                [
                    ("reports/dataset_validation_report.md", "数据集验收结论"),
                    ("reports/split_summary.csv", "train / val / test 统计"),
                    ("reports/class_distribution.csv", "类别分布统计"),
                    ("reports/label_errors.csv", "标签错误明细（有错时生成）"),
                ],
                "把验收结果写成报告文件，便于回看这次导出的数据集到底通过了哪些检查、是否存在错误。",
            ),
            (
                "count_dataset",
                "统计样本与类别数量",
                [],
                "统计 train / val / test 的图片数、标签框数和类别数量，为后续训练概览和评估提供基础数据。",
            ),
            (
                "persist_partial_summary",
                "写入阶段性 metrics_summary",
                [("phase.json", "阶段状态 dataset_ready")],
                "把当前阶段的数据摘要先写入 metrics_summary 和 phase.json，页面和后续流程都依赖这份阶段信息。",
            ),
        ],
        "root_suffix": "reports/",
    },
    "loading_model": {
        "substeps": [
            (
                "resolve_weights",
                "解析本次训练使用的初始权重",
                [],
                "确定这次训练是从平台内置权重开始、从自定义权重开始，还是从 last.pt 续训。",
            ),
            (
                "load_yolo_model",
                "加载 YOLO 模型",
                [("train/weights/", "训练权重目录")],
                "调用 Ultralytics 的 YOLO(weights_path) 初始化模型，让后续训练和评估都基于这份权重运行。",
            ),
            (
                "bind_callbacks",
                "绑定训练回调与过程日志",
                [],
                "挂接训练开始、epoch、batch、训练结束等回调，用来写日志、检测暂停、记录权重和指标变化。",
            ),
        ],
        "root_suffix": "",
    },
    "running": {
        "substeps": [
            (
                "merge_hyperparams",
                "合并运行时超参与安全配置",
                [],
                "把默认超参、用户填写的超参、续训参数和安全修正合并成最终的 model.train 配置。",
            ),
            (
                "resolve_device",
                "解析 GPU / worker / amp 配置",
                [],
                "确定这次训练实际使用哪个 device，以及 workers、amp、batch 等运行参数。",
            ),
            (
                "run_train",
                "执行 Ultralytics 训练循环",
                [],
                "正式调用 model.train(...) 进入训练循环，按 epoch 持续前向、反向、保存指标和更新最佳权重。",
            ),
            (
                "write_runtime_outputs",
                "持续写出结果、参数与断点权重",
                [
                    ("train/results.csv", "逐 epoch 指标"),
                    ("train/args.yaml", "训练参数快照"),
                    ("train/weights/last.pt", "断点续训权重"),
                    ("train/weights/best.pt", "当前最佳权重"),
                ],
                "训练过程中会不断刷新 results.csv、args.yaml、last.pt 和 best.pt，页面上看到的进度和产物都来自这里。",
            ),
        ],
        "root_suffix": "train/",
    },
    "finalizing": {
        "substeps": [
            (
                "collect_final_metrics",
                "读取末轮与最佳轮次指标",
                [],
                "同时记录 results.csv 末轮指标和训练过程中最佳轮次指标；末轮只用于分析训练末段是否回落，不作为交付模型的最终结论。",
            ),
            (
                "run_val_metrics",
                "使用 best.pt 重验验证集",
                [
                    ("train/weights/best.pt", "最终评估使用权重"),
                    ("phase.json", "阶段状态 val_per_class / test_val"),
                ],
                "使用 best.pt 在 val 划分上重新评估，保存总体和各类别指标；这组结果是页面、质量准入和 AI 评估的权威验证口径。",
            ),
            (
                "run_test_metrics",
                "执行测试集分类型评估",
                [],
                "如果这次导出了 test 集并启用了自动测试评估，还会再对 test 划分跑一轮独立评估。",
            ),
            (
                "write_metrics_summary",
                "写入版本化指标契约",
                [("metrics_summary", "训练、验证、测试汇总")],
                "将 last_epoch_metrics、best_epoch_metrics、best_pt_val_metrics 和 test_val_metrics 分开写入 metrics_summary，并保留旧字段兼容历史作业。",
            ),
        ],
        "root_suffix": "train/",
    },
    "completed": {
        "substeps": [
            (
                "persist_completed_status",
                "更新作业状态为 COMPLETED",
                [],
                "把数据库里的训练作业状态正式写成 COMPLETED，表示训练主流程和收尾评估都已经走完。",
            ),
            (
                "save_weights_path",
                "记录 best.pt 下载路径",
                [("train/weights/best.pt", "最终可下载模型权重")],
                "把最终可用的 best.pt 绝对路径保存到作业记录里，后续下载、部署和发布都依赖这个路径。",
            ),
            (
                "mark_completed_phase",
                "写入 completed 阶段标记",
                [("phase.json", "阶段状态 completed")],
                "把 phase.json 标记成 completed，让页面、恢复逻辑和后续流程都知道这次训练已经完整结束。",
            ),
            (
                "trigger_followups",
                "触发训练后续流程",
                [("process.log", "训练过程日志")],
                "训练完成后继续触发自动评估、标注对比图等后续任务，训练本身结束，但围绕模型的附加分析还会继续。",
            ),
        ],
        "root_suffix": "train/weights/best.pt",
    },
}


def _step_root_path(job_id: int, step_key: str, weights_path: Optional[str]) -> str:
    root = f"{{MEDIA_ROOT}}/yolo_train/job-{int(job_id)}/"
    structure = TRAIN_STEP_STRUCTURE.get(step_key) or {}
    suffix = str(structure.get("root_suffix") or "")
    if step_key == "loading_model" and weights_path:
        return str(weights_path)
    return root + suffix


def _substep_status(
    *,
    step_status: str,
    is_current_step: bool,
    substep_index: int,
    substep_count: int,
) -> str:
    if step_status in {"done", "failed", "cancelled", "skipped"}:
        return step_status
    if step_status == "waiting":
        return "waiting"
    if not is_current_step:
        return "waiting"
    if substep_count <= 1:
        return "running"
    return "running" if substep_index == substep_count - 1 else "done"


def _step_substeps(
    *,
    step_key: str,
    step_status: str,
    train_phase: Optional[str],
    train_phase_label: Optional[str],
    train_phase_detail: Optional[str],
    current_epoch: Optional[int],
    epochs: Optional[int],
    current_step_key: str,
    dataset_validation_report: Optional[dict[str, Any]] = None,
):
    structure = TRAIN_STEP_STRUCTURE.get(step_key) or {}
    rows = structure.get("substeps") or []
    total = len(rows)
    out = []
    for idx, row in enumerate(rows):
        sub_key = row[0]
        label = row[1]
        raw_artifacts = row[2] if len(row) > 2 else []
        base_detail = str(row[3]).strip() if len(row) > 3 and row[3] else None
        status = _substep_status(
            step_status=step_status,
            is_current_step=(step_key == current_step_key),
            substep_index=idx,
            substep_count=total,
        )
        summary: Optional[str] = None
        detail: Optional[str] = base_detail
        if step_key == current_step_key and status in {
            "running",
            "failed",
            "cancelled",
        }:
            if (
                step_key == "running"
                and sub_key == "run_train"
                and current_epoch is not None
                and epochs
            ):
                summary = f"epoch {current_epoch} / {epochs}"
            elif train_phase_label:
                summary = train_phase_label
            if train_phase_detail:
                detail = (
                    f"{base_detail} 当前状态：{train_phase_detail}"
                    if base_detail
                    else train_phase_detail
                )
        checks = []
        if step_key == "dataset_ready" and sub_key == "validate_dataset":
            checks = dataset_ready_checks_from_report(dataset_validation_report or {})
            if checks and status == "done":
                passed = sum(1 for item in checks if item.get("status") == "done")
                summary = f"共校验 {len(checks)} 项，已通过 {passed} 项"
                detail = base_detail
        out.append(
            {
                "key": sub_key,
                "label": label,
                "status": status,
                "summary": summary,
                "detail": detail,
                "checks": checks,
                "artifacts": [
                    {"path": str(path), "label": str(artifact_label)}
                    for path, artifact_label in raw_artifacts
                ],
            }
        )
    return out


def _step_artifacts(step_key: str):
    return []


def build_train_steps(
    *,
    job_id: int,
    status: str,
    train_phase: Optional[str],
    train_phase_label: Optional[str],
    train_phase_detail: Optional[str],
    current_epoch: Optional[int],
    epochs: Optional[int],
    weights_path: Optional[str],
    dataset_validation_report: Optional[dict[str, Any]] = None,
):
    current_step_key = TRAIN_PHASE_STEP_KEY.get(train_phase or "")
    if not current_step_key:
        if status == "COMPLETED":
            current_step_key = "completed"
        elif status in {"FAILED", "CANCELLED", "PROCESSING"}:
            current_step_key = "running"
        else:
            current_step_key = "preparing_dataset"

    step_index_map = {item[0]: idx for idx, item in enumerate(TRAIN_STEP_SEQUENCE)}
    current_index = step_index_map.get(current_step_key, 0)
    failed = status == "FAILED" or train_phase == "failed"
    cancelled = status == "CANCELLED" or train_phase == "cancelled"

    def _summary(step_key: str, label: str) -> Optional[str]:
        if step_key == current_step_key:
            if step_key == "running" and current_epoch is not None and epochs:
                return f"epoch {current_epoch} / {epochs}"
            return train_phase_label or label
        if step_key == "dataset_ready":
            return "训练数据已导出并完成校验"
        if step_key == "loading_model":
            return "解析并加载本次训练使用的初始权重"
        if step_key == "running":
            if current_epoch is not None and epochs:
                return f"当前训练进度 epoch {current_epoch} / {epochs}"
            return "Ultralytics 训练循环"
        if step_key == "finalizing":
            return "汇总 val/test 指标并写回平台"
        if step_key == "completed":
            return "best.pt 已生成，可继续下载或部署"
        return label

    def _detail(step_key: str) -> Optional[str]:
        if step_key != current_step_key:
            return None
        if step_key == "running" and train_phase in {"failed", "cancelled"}:
            return train_phase_detail or train_phase_label
        return train_phase_detail or train_phase_label

    items = []
    for order, (step_key, label) in enumerate(TRAIN_STEP_SEQUENCE):
        if failed and step_key == current_step_key:
            visual_status = "failed"
        elif cancelled and step_key == current_step_key:
            visual_status = "cancelled"
        elif order < current_index or status == "COMPLETED":
            visual_status = "done"
        elif order == current_index:
            visual_status = "running"
        else:
            visual_status = "waiting"
        items.append(
            {
                "key": step_key,
                "label": label,
                "status": visual_status,
                "summary": _summary(step_key, label),
                "detail": _detail(step_key),
                "order": order,
                "substeps": _step_substeps(
                    step_key=step_key,
                    step_status=visual_status,
                    train_phase=train_phase,
                    train_phase_label=train_phase_label,
                    train_phase_detail=train_phase_detail,
                    current_epoch=current_epoch,
                    epochs=epochs,
                    current_step_key=current_step_key,
                    dataset_validation_report=dataset_validation_report,
                ),
                "artifacts": _step_artifacts(step_key),
                "root_path": _step_root_path(job_id, step_key, weights_path),
            }
        )
    return current_step_key, items


def results_csv_progress(
    media_root: str,
    work_subdir: str,
    job_id: int,
) -> tuple[Optional[int], Optional[str]]:
    csv_path = (
        Path(media_root) / work_subdir / f"job-{job_id}" / "train" / "results.csv"
    )
    if not csv_path.is_file():
        return None, None
    try:
        with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return None, None
    if len(rows) < 2:
        return None, None
    header = rows[0]
    last = rows[-1]
    current_epoch: Optional[int] = None
    try:
        if "epoch" in header:
            ei = header.index("epoch")
            if ei < len(last):
                current_epoch = int(float(last[ei]))
    except (ValueError, IndexError):
        pass
    snap = ",".join(last)
    if len(snap) > 400:
        snap = snap[:400] + "?"
    return current_epoch, snap


def float_metric(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def training_val_metrics_for_job(
    final_metrics_from_metrics_summary_fn: Callable[[Any], Optional[dict]],
    resolve_train_dir_from_weights_fn: Callable[[Any], Optional[Path]],
    parse_results_csv_final_metrics_fn: Callable[[Path], Optional[dict]],
    format_training_metrics_line_fn: Callable[[dict], str],
    float_metric_fn: Callable[[Any], Optional[float]],
    metrics_cls,
    job,
):
    fm = final_metrics_from_metrics_summary_fn(job.metrics_summary)
    source = "metrics_summary"
    if fm is None:
        td = resolve_train_dir_from_weights_fn(job.weights_path)
        fm = parse_results_csv_final_metrics_fn(td) if td else None
        source = "results_csv" if fm else "none"
    if fm is None:
        return None
    line = format_training_metrics_line_fn(fm)
    ep_raw = fm.get("epoch")
    epoch_i: Optional[int] = None
    if ep_raw is not None:
        try:
            epoch_i = int(float(ep_raw))
        except (TypeError, ValueError):
            epoch_i = None
    return metrics_cls(
        map50_95=float_metric_fn(fm.get("map50_95")),
        map50=float_metric_fn(fm.get("map50")),
        precision=float_metric_fn(fm.get("precision")),
        recall=float_metric_fn(fm.get("recall")),
        fitness=float_metric_fn(fm.get("fitness")),
        epoch=epoch_i,
        summary_line=line,
        source=source,
    )


def validation_pass_row(
    validation_pass_assessment_fn: Callable[..., dict[str, Any]],
    assessment_cls,
    settings_obj,
    training_metrics,
):
    if not training_metrics:
        return assessment_cls(
            status="无验证指标",
            detail="未读取到验证指标（metrics_summary / results.csv）",
        )
    data = validation_pass_assessment_fn(
        training_metrics.map50_95,
        training_metrics.map50,
        threshold_map5095=float(settings_obj.YOLO_VAL_PASS_MAP5095),
        threshold_map50=float(settings_obj.YOLO_VAL_PASS_MAP50),
    )
    return assessment_cls(status=str(data["status"]), detail=data.get("detail"))


def sample_counts_for_job(
    resolve_job_sample_counts_fn: Callable[..., dict[str, Optional[int]]], job
):
    return resolve_job_sample_counts_fn(
        job_id=job.id,
        metrics_summary=job.metrics_summary,
        train_sample_count=job.train_sample_count,
        val_sample_count=job.val_sample_count,
    )


def test_sample_count_for_job(
    sample_counts_for_job_fn: Callable[[Any], dict[str, Optional[int]]], job
):
    return sample_counts_for_job_fn(job).get("test")


def test_val_metrics_for_job(
    read_test_val_metrics_from_summary_fn: Callable[[Any], Optional[dict]],
    format_training_metrics_line_fn: Callable[[dict], str],
    float_metric_fn: Callable[[Any], Optional[float]],
    metrics_cls,
    job,
):
    tvm = read_test_val_metrics_from_summary_fn(job.metrics_summary)
    if not tvm:
        return None
    st = str(tvm.get("status") or "unknown")
    line: Optional[str] = None
    if st == "ok":
        line = format_training_metrics_line_fn(
            {
                "map50_95": tvm.get("map50_95"),
                "map50": tvm.get("map50"),
                "precision": tvm.get("precision"),
                "recall": tvm.get("recall"),
            }
        )
    return metrics_cls(
        status=st,
        split=str(tvm.get("split") or "test"),
        map50_95=float_metric_fn(tvm.get("map50_95")),
        map50=float_metric_fn(tvm.get("map50")),
        precision=float_metric_fn(tvm.get("precision")),
        recall=float_metric_fn(tvm.get("recall")),
        images=int(tvm["images"]) if tvm.get("images") is not None else None,
        instances=int(tvm["instances"]) if tvm.get("instances") is not None else None,
        summary_line=line,
        detail=str(tvm["detail"])[:500] if tvm.get("detail") else None,
        run_at=str(tvm["run_at"]) if tvm.get("run_at") else None,
    )


def val_test_comparison_for_job(
    final_metrics_from_metrics_summary_fn: Callable[[Any], Optional[dict]],
    read_test_val_metrics_from_summary_fn: Callable[[Any], Optional[dict]],
    build_val_test_comparison_fn: Callable[[Any, Any], Optional[dict]],
    comparison_cls,
    job,
):
    val_fm = final_metrics_from_metrics_summary_fn(job.metrics_summary)
    test_raw = read_test_val_metrics_from_summary_fn(job.metrics_summary)
    test_fm = test_raw if test_raw and test_raw.get("status") == "ok" else None
    cmp = build_val_test_comparison_fn(val_fm, test_fm)
    if not cmp:
        return None
    return comparison_cls(**cmp)


def creator_emails_by_user_ids(
    db, user_model, user_ids: Iterable[int]
) -> dict[int, str]:
    ids = {int(uid) for uid in user_ids if uid}
    if not ids:
        return {}
    rows = (
        db.query(user_model.id, user_model.username)
        .filter(user_model.id.in_(ids))
        .all()
    )
    return {int(uid): (uname or "").strip() for uid, uname in rows}


def classes_fallback_for_job(
    read_class_names_from_data_yaml_fn: Callable[[Path], list[str]],
    job_work_root_fn: Callable[[int], Path],
    session_scope_fn: Callable[[], Any],
    crud_task,
    build_yolo_rect_class_names_fn: Callable[[dict], list[str]],
    parse_task_config_fn: Callable[[str | None], dict],
    job,
) -> list[str]:
    names = read_class_names_from_data_yaml_fn(
        job_work_root_fn(job.id) / "dataset" / "data.yaml"
    )
    if names:
        return names
    with session_scope_fn() as db:
        task = crud_task.get(db=db, task_id=job.task_id)
        if not task or not task.config:
            return []
        return build_yolo_rect_class_names_fn(parse_task_config_fn(task.config))


def is_ultralytics_train_loop_complete(
    read_last_epoch_from_results_csv_fn: Callable[[Path], Optional[int]],
    work_root: Path,
    target_epochs: int,
) -> bool:
    if target_epochs <= 0:
        return False
    train_dir = work_root / "train"
    best_path = train_dir / "weights" / "best.pt"
    if not best_path.is_file():
        return False
    last_ep = read_last_epoch_from_results_csv_fn(train_dir)
    if last_ep is None:
        return False
    return int(last_ep) >= int(target_epochs)


def dataset_class_names_for_summary(
    read_class_names_from_data_yaml_fn: Callable[[Path], list[str]],
    classes_fallback_for_job_fn: Callable[[Any], list[str]],
    job,
    dataset_root: Path,
    *,
    prepared_classes: Optional[list[str]] = None,
) -> list[str]:
    if prepared_classes:
        return list(prepared_classes)
    names = read_class_names_from_data_yaml_fn(dataset_root / "data.yaml")
    if names:
        return names
    return classes_fallback_for_job_fn(job)


def class_metrics_for_job(
    classes_fallback_for_job_fn: Callable[[Any], list[str]],
    job_work_root_fn: Callable[[int], Path],
    count_class_instances_in_dataset_fn: Callable[[Path, list[str]], Any],
    build_class_metrics_detail_fn: Callable[..., Optional[dict]],
    row_cls,
    detail_cls,
    job,
):
    fallback = classes_fallback_for_job_fn(job)
    dataset_root = job_work_root_fn(job.id) / "dataset"
    fallback_counts = None
    if fallback and dataset_root.is_dir():
        fallback_counts = count_class_instances_in_dataset_fn(dataset_root, fallback)
    raw_detail = build_class_metrics_detail_fn(
        job.metrics_summary,
        fallback_classes=fallback or None,
        fallback_instance_counts=fallback_counts or None,
    )
    if not raw_detail:
        return None

    def _rows(key: str):
        return [row_cls(**row) for row in raw_detail.get(key) or []]

    return detail_cls(
        classes=raw_detail.get("classes") or [],
        rows=_rows("rows"),
        val_metrics=_rows("val_metrics"),
        test_metrics=_rows("test_metrics"),
    )


def metrics_fields_for_job(
    test_sample_count_for_job_fn: Callable[[Any], Optional[int]],
    training_val_metrics_for_job_fn: Callable[[Any], Any],
    test_val_metrics_for_job_fn: Callable[[Any], Any],
    val_test_comparison_for_job_fn: Callable[[Any], Any],
    job,
) -> dict:
    return {
        "test_sample_count": test_sample_count_for_job_fn(job),
        "training_val_metrics": training_val_metrics_for_job_fn(job),
        "test_val_metrics": test_val_metrics_for_job_fn(job),
        "val_test_comparison": val_test_comparison_for_job_fn(job),
    }


def merge_done_counts_from_job(job) -> dict[int, int]:
    raw = getattr(job, "metrics_summary", None) or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        mt = (data or {}).get("merge_tasks") or {}
        counts = mt.get("done_counts") or {}
        if isinstance(counts, dict):
            return {int(k): int(v) for k, v in counts.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {}


def merge_exported_counts_from_job(job) -> dict[int, int]:
    raw = getattr(job, "metrics_summary", None) or ""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        mt = (data or {}).get("merge_tasks") or {}
        counts = mt.get("exported_counts") or {}
        if isinstance(counts, dict):
            return {int(k): int(v) for k, v in counts.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {}


def merge_tasks_summary_payload(
    source_ids: list[int],
    per_task_done: dict[int, int],
    per_task_exported: dict[int, int] | None = None,
) -> dict:
    payload: dict = {
        "task_ids": source_ids,
        "done_counts": {str(k): int(v) for k, v in per_task_done.items()},
    }
    if per_task_exported:
        payload["exported_counts"] = {
            str(k): int(v) for k, v in per_task_exported.items()
        }
    return payload


def runtime_fields_for_job(
    read_train_phase_fn: Callable[
        [int],
        tuple[Optional[str], Optional[str], Optional[int], Optional[str]]
        | tuple[Optional[str], Optional[str], Optional[int]],
    ],
    active_train_jobs: set[int],
    active_yolo_train_job_ids_on_gpu_fn: Callable[[], set[int]],
    runtime_train_process_on_gpu_fn: Callable[[int], bool],
    remote_job_runtime_status_fn: Callable[[Any], Optional[str]],
    job,
    *,
    completed_status: str,
    failed_status: str,
    cancelled_status: str,
    pending_status: str,
    processing_status: str,
) -> dict[str, Any]:
    # Older integrations returned a three-item phase tuple.  Keep accepting it
    # while the service-level reader exposes the newer detail field.
    phase_info = read_train_phase_fn(job.id)
    if len(phase_info) >= 4:
        phase, _label, runtime_pid, _detail = phase_info[:4]
    else:
        phase, _label, runtime_pid = (*phase_info, None)[:3]
    status_value = str(job.status or "")
    runtime_status: Optional[str] = None
    restartable = False
    restart_reason: Optional[str] = None

    if status_value == completed_status:
        runtime_status = "COMPLETED"
    elif status_value == failed_status:
        runtime_status = "FAILED"
        restartable = True
        restart_reason = "训练失败后可基于 last.pt 继续运行"
    elif status_value == cancelled_status:
        still_running = runtime_pid is not None and (
            job.id in active_train_jobs or runtime_train_process_on_gpu_fn(runtime_pid)
        )
        if still_running:
            runtime_status = "RUNNING"
            restart_reason = "训练子进程正在停止，请稍后再继续运行"
        else:
            runtime_status = "CANCELLED"
            restartable = True
            restart_reason = "训练已暂停，可基于 checkpoint 继续运行"
    elif status_value == pending_status:
        if job.id in active_train_jobs:
            runtime_status = "RUNNING"
        elif str(getattr(job, "execution_mode", None) or "local").strip() == "remote":
            remote_runtime = remote_job_runtime_status_fn(job)
            if remote_runtime == "RUNNING":
                runtime_status = "RUNNING"
            else:
                runtime_status = "STOPPED"
                restartable = True
                restart_reason = "当前未检测到远程训练进程，可继续运行"
        else:
            runtime_status = "STOPPED"
            restartable = True
            restart_reason = "当前未检测到训练进程，可继续运行"
    elif status_value == processing_status:
        remote_runtime = None
        if str(getattr(job, "execution_mode", None) or "local").strip() == "remote":
            remote_runtime = remote_job_runtime_status_fn(job)

        if remote_runtime == "RUNNING":
            runtime_status = "RUNNING"
        elif remote_runtime == "FAILED":
            runtime_status = "FAILED"
            restartable = True
            restart_reason = "远程训练已失败，请结合 process.log 与 remote_status 排查"
        elif remote_runtime == "STOPPED":
            runtime_status = "STOPPED"
            restartable = True
            restart_reason = "远程训练已停止，请检查训练节点或 Agent 状态"
        elif job.id in active_train_jobs:
            runtime_status = "RUNNING"
        elif int(job.id) in active_yolo_train_job_ids_on_gpu_fn():
            runtime_status = "RUNNING"
        elif phase == "running" and runtime_pid is not None:
            if runtime_train_process_on_gpu_fn(runtime_pid):
                runtime_status = "RUNNING"
            else:
                runtime_status = "STOPPED"
                restartable = True
                restart_reason = f"训练进程 PID {runtime_pid} 当前未检测到占用 GPU，请先核对 NVIDIA 进程状态"
        else:
            runtime_status = "STOPPED"
            restartable = True
            restart_reason = "当前未检测到训练进程，可继续运行"

    return {
        "runtime_status": runtime_status,
        "runtime_pid": runtime_pid,
        "is_restartable": restartable,
        "restart_reason": restart_reason,
    }


def remote_fields_for_job(
    get_node_by_id_fn: Callable[[str], Optional[dict]],
    job,
) -> dict:
    mode = str(getattr(job, "execution_mode", None) or "local").strip() or "local"
    node_id = getattr(job, "train_node_id", None) or None
    node_name: Optional[str] = None
    remote_status: Optional[dict] = None
    remote_phase: Optional[str] = None
    if node_id:
        node = get_node_by_id_fn(str(node_id))
        if node:
            node_name = str(node.get("name") or node_id)
    raw = getattr(job, "remote_status_json", None) or ""
    if raw:
        try:
            remote_status = json.loads(raw)
            if isinstance(remote_status, dict):
                remote_phase = (
                    str(
                        remote_status.get("remote_phase")
                        or remote_status.get("phase")
                        or ""
                    ).strip()
                    or None
                )
            else:
                remote_status = None
        except (TypeError, json.JSONDecodeError):
            remote_status = None
    remote_upload_pct: Optional[int] = None
    if isinstance(remote_status, dict):
        phase_key = (
            str(remote_status.get("remote_phase") or remote_status.get("phase") or "")
            .strip()
            .lower()
        )
        if phase_key in ("uploading", "downloading"):
            raw_pct = remote_status.get("upload_pct")
            if raw_pct is not None:
                try:
                    remote_upload_pct = int(raw_pct)
                except (TypeError, ValueError):
                    remote_upload_pct = None
            if remote_upload_pct is None:
                ub = remote_status.get("upload_bytes")
                ut = remote_status.get("upload_total_bytes")
                try:
                    if ub is not None and ut:
                        remote_upload_pct = min(100, int(int(ub) * 100 / int(ut)))
                except (TypeError, ValueError, ZeroDivisionError):
                    remote_upload_pct = None
    return {
        "execution_mode": mode,
        "train_node_id": node_id,
        "train_node_name": node_name,
        "remote_status": remote_status,
        "remote_phase": remote_phase,
        "remote_upload_pct": remote_upload_pct,
    }


def local_gpu_fields_for_job(
    gpu_display_fields_for_job_fn: Callable[[int], dict[str, Optional[str]]],
    job,
) -> dict[str, Optional[str]]:
    mode = str(getattr(job, "execution_mode", None) or "local").strip() or "local"
    if mode == "remote":
        return {
            "train_gpu_device": None,
            "train_gpu_mode": None,
            "train_gpu_label": None,
        }
    fields = gpu_display_fields_for_job_fn(int(job.id))
    return {
        "train_gpu_device": fields.get("train_gpu_device"),
        "train_gpu_mode": fields.get("train_gpu_mode"),
        "train_gpu_label": fields.get("train_gpu_label"),
    }


def job_to_response(
    read_train_phase_fn: Callable[
        [int], tuple[Optional[str], Optional[str], Optional[int], Optional[str]]
    ],
    results_csv_progress_fn: Callable[[int], tuple[Optional[int], Optional[str]]],
    metrics_fields_for_job_fn: Callable[[Any], dict],
    validation_pass_for_job_fn: Callable[[Any], Any],
    sample_counts_for_job_fn: Callable[[Any], dict[str, Optional[int]]],
    parse_source_task_ids_fn: Callable[[Any], list[int]],
    merge_done_counts_from_job_fn: Callable[[Any], dict[int, int]],
    merge_exported_counts_from_job_fn: Callable[[Any], dict[int, int]],
    runtime_fields_for_job_fn: Callable[[Any], dict[str, Any]],
    merge_task_label_fn: Callable[[list[int]], str],
    job_hyperparams_dict_fn: Callable[[Any], Optional[dict]],
    class_metrics_for_job_fn: Callable[[Any], Any],
    dataset_validation_report_for_job_fn: Callable[[Any], dict[str, Any]],
    remote_fields_for_job_fn: Callable[[Any], dict],
    local_gpu_fields_for_job_fn: Callable[[Any], dict[str, Optional[str]]],
    response_cls,
    processing_status: str,
    pending_status: str,
    failed_status: str,
    cancelled_status: str,
    job,
):
    current_epoch: Optional[int] = None
    progress_snapshot: Optional[str] = None
    train_phase: Optional[str] = None
    train_phase_label: Optional[str] = None
    runtime_pid: Optional[int] = None
    train_phase_detail: Optional[str] = None
    if job.status in (
        processing_status,
        pending_status,
        failed_status,
        cancelled_status,
    ):
        train_phase, train_phase_label, runtime_pid, train_phase_detail = (
            read_train_phase_fn(job.id)
        )
    if job.status == processing_status:
        current_epoch, progress_snapshot = results_csv_progress_fn(job.id)

    mf = metrics_fields_for_job_fn(job)
    sc = sample_counts_for_job_fn(job)
    merge_ids = parse_source_task_ids_fn(job)
    merge_done = merge_done_counts_from_job_fn(job)
    merge_exported = merge_exported_counts_from_job_fn(job)
    merge_counts_display = merge_exported if merge_exported else merge_done
    runtime_fields = runtime_fields_for_job_fn(job)
    dataset_validation_report = dataset_validation_report_for_job_fn(job)
    if runtime_fields.get("runtime_pid") is None and runtime_pid is not None:
        runtime_fields["runtime_pid"] = runtime_pid
    current_step_key, train_steps = build_train_steps(
        job_id=int(job.id),
        status=str(job.status or ""),
        train_phase=train_phase,
        train_phase_label=train_phase_label,
        train_phase_detail=train_phase_detail,
        current_epoch=current_epoch,
        epochs=getattr(job, "epochs", None),
        weights_path=getattr(job, "weights_path", None),
        dataset_validation_report=dataset_validation_report,
    )
    return response_cls(
        id=job.id,
        task_id=job.task_id,
        merge_task_ids=merge_ids if len(merge_ids) > 1 else None,
        merge_task_label=merge_task_label_fn(merge_ids) if len(merge_ids) > 1 else None,
        merge_task_sample_counts=(
            {str(k): v for k, v in merge_counts_display.items()}
            if merge_counts_display
            else None
        ),
        status=job.status,
        category=getattr(job, "category", None) or "",
        hyperparams=job_hyperparams_dict_fn(job),
        model_weights=job.model_weights,
        epochs=job.epochs,
        imgsz=job.imgsz,
        train_sample_count=sc.get("train"),
        val_sample_count=sc.get("val"),
        test_sample_count=sc.get("test"),
        total_sample_count=sc.get("total"),
        weights_path=job.weights_path,
        metrics_summary=job.metrics_summary,
        error_message=job.error_message,
        ai_eval_full_status=getattr(job, "ai_eval_full_status", None),
        ai_eval_next_status=getattr(job, "ai_eval_next_status", None),
        created_at=job.created_at,
        updated_at=job.updated_at,
        current_epoch=current_epoch,
        progress_snapshot=progress_snapshot,
        train_phase=train_phase,
        train_phase_label=train_phase_label,
        train_phase_detail=train_phase_detail,
        current_step_key=current_step_key,
        train_steps=train_steps,
        training_val_metrics=mf["training_val_metrics"],
        test_val_metrics=mf["test_val_metrics"],
        val_test_comparison=mf["val_test_comparison"],
        validation_pass=validation_pass_for_job_fn(job),
        **runtime_fields,
        class_metrics=class_metrics_for_job_fn(job),
        **remote_fields_for_job_fn(job),
        **local_gpu_fields_for_job_fn(job),
    )
