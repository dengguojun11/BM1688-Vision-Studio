"""导出后的 YOLO 数据集验收（参考离线工具包 validate_dataset，平台内轻量集成）。"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def _parse_data_yaml_names(text: str) -> list[str]:
    names: list[str] = []
    in_names = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("names:"):
            in_names = True
            continue
        if in_names:
            m = re.match(r"^\s*(\d+)\s*:\s*(.+)$", line)
            if m:
                names.append(m.group(2).strip().strip("'\""))
            elif stripped and not stripped.startswith("#"):
                in_names = False
    return names


def _load_dataset_meta(dataset_root: Path) -> tuple[Path, list[str], int]:
    for name in ("data.yaml", "dataset.yaml"):
        p = dataset_root / name
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            names = _parse_data_yaml_names(text)
            nc_match = re.search(r"^nc:\s*(\d+)\s*$", text, re.MULTILINE)
            nc = int(nc_match.group(1)) if nc_match else len(names)
            return p, names, nc
    raise FileNotFoundError(f"未找到 data.yaml（旧作业可能仍有 dataset.yaml）: {dataset_root}")


def _images_in_split(dataset_root: Path, split: str) -> list[Path]:
    d = dataset_root / "images" / split
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def _label_for_image(image_path: Path) -> Path:
    parts = list(image_path.parts)
    if "images" in parts:
        idx = parts.index("images")
        parts[idx] = "labels"
        return Path(*parts).with_suffix(".txt")
    return image_path.with_suffix(".txt")


def _validate_label_file(label_path: Path, nc: int) -> tuple[list[int], list[tuple[str, str]]]:
    counts = [0] * nc
    errors: list[tuple[str, str]] = []
    if not label_path.is_file():
        return counts, [("missing_label", "label file missing")]
    text = label_path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        row = line.strip()
        if not row:
            continue
        parts = row.split()
        if len(parts) < 5:
            errors.append((f"line_{lineno}", "expected at least 5 columns"))
            continue
        try:
            cls = int(float(parts[0]))
            x, y, w, h = (float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
        except ValueError:
            errors.append((f"line_{lineno}", "non numeric label value"))
            continue
        if cls < 0 or cls >= nc:
            errors.append((f"line_{lineno}", f"class id {cls} outside [0,{nc - 1}]"))
            continue
        if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
            errors.append((f"line_{lineno}", f"box outside normalized range: {x} {y} {w} {h}"))
        else:
            counts[cls] += 1
    return counts, errors


def validate_exported_yolo_dataset(
    dataset_root: Path,
    *,
    strict: bool = True,
    write_reports: bool = True,
    reports_dir: Path | None = None,
) -> dict[str, Any]:
    """
    校验平台导出的 dataset 目录；报告写入 <job>/reports/（非 dataset/ 内）。
    strict=True 且存在 error 行时抛出 ValueError。
    """
    dataset_root = dataset_root.resolve()
    _yaml_path, names, nc = _load_dataset_meta(dataset_root)
    if not names:
        raise ValueError("data.yaml 中未解析到 names")

    split_rows: list[dict[str, Any]] = []
    class_counts: dict[str, list[int]] = {
        split: [0] * nc for split in ("train", "val", "test")
    }
    error_rows: list[dict[str, str]] = []
    split_image_paths: dict[str, set[str]] = {}

    for split in ("train", "val", "test"):
        images = _images_in_split(dataset_root, split)
        if not images and split == "test":
            continue
        split_image_paths[split] = {str(p.resolve()) for p in images}
        missing = 0
        labels_found = 0
        boxes = 0
        for image in images:
            label = _label_for_image(image)
            counts, errors = _validate_label_file(label, nc)
            if label.is_file():
                labels_found += 1
            else:
                missing += 1
            boxes += sum(counts)
            class_counts[split] = [a + b for a, b in zip(class_counts[split], counts)]
            for loc, msg in errors:
                error_rows.append(
                    {
                        "split": split,
                        "image": str(image),
                        "label": str(label),
                        "where": loc,
                        "error": msg,
                    }
                )
        if images or split in ("train", "val"):
            split_rows.append(
                {
                    "split": split,
                    "images": len(images),
                    "labels_found": labels_found,
                    "missing_labels": missing,
                    "boxes": boxes,
                }
            )

    train_paths = split_image_paths.get("train", set())
    for split in ("val", "test"):
        overlap = train_paths & split_image_paths.get(split, set())
        for p in sorted(overlap):
            error_rows.append(
                {
                    "split": split,
                    "image": p,
                    "label": "",
                    "where": "split_overlap",
                    "error": "image also appears in train",
                }
            )

    if reports_dir is None:
        from labelu.internal.application.service.yolo_dataset import yolo_job_reports_dir

        reports_dir = yolo_job_reports_dir(dataset_root)
    report_dir = reports_dir.resolve()
    if write_reports:
        report_dir.mkdir(parents=True, exist_ok=True)
        with open(report_dir / "split_summary.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["split", "images", "labels_found", "missing_labels", "boxes"],
            )
            writer.writeheader()
            writer.writerows(split_rows)

        with open(report_dir / "class_distribution.csv", "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["class_id", "name", "train", "val", "test"])
            for i, name in enumerate(names):
                writer.writerow(
                    [
                        i,
                        name,
                        class_counts["train"][i],
                        class_counts["val"][i],
                        class_counts["test"][i],
                    ]
                )

        if error_rows:
            with open(report_dir / "label_errors.csv", "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["split", "image", "label", "where", "error"]
                )
                writer.writeheader()
                writer.writerows(error_rows)

        ok = len(error_rows) == 0
        with open(report_dir / "dataset_validation_report.md", "w", encoding="utf-8") as f:
            f.write("# 数据集验收\n\n")
            f.write(f"- 结论: **{'通过' if ok else '未通过'}**\n")
            f.write(f"- 类别数 nc={nc}\n")
            f.write(f"- 错误条数: {len(error_rows)}\n\n")
            for row in split_rows:
                f.write(
                    f"- {row['split']}: images={row['images']} boxes={row['boxes']} "
                    f"missing_labels={row['missing_labels']}\n"
                )

    summary = {
        "ok": len(error_rows) == 0,
        "error_count": len(error_rows),
        "class_names": names,
        "splits": split_rows,
        "report_dir": str(report_dir) if write_reports else None,
    }

    if strict and error_rows:
        preview = "; ".join(
            f"{r['split']}/{r['where']}: {r['error']}" for r in error_rows[:5]
        )
        raise ValueError(
            f"YOLO 数据集验收未通过（{len(error_rows)} 项）。示例: {preview}。"
            f"详见 {report_dir}"
        )
    return summary
