from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple


@dataclass
class ScanResult:
    stems: Set[str]
    duplicates: Dict[str, List[str]]


def normalize_stem(stem: str, case_insensitive: bool) -> str:
    return stem.lower() if case_insensitive else stem


def collect_files(
    base_dir: Path,
    extensions: Iterable[str],
    recursive: bool,
    case_insensitive: bool,
) -> ScanResult:
    ext_set = {e.lower().strip() for e in extensions if e.strip()}
    pattern = "**/*" if recursive else "*"
    duplicates: Dict[str, List[str]] = {}
    index: Dict[str, List[str]] = {}

    for file_path in base_dir.glob(pattern):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in ext_set:
            continue
        stem = normalize_stem(file_path.stem, case_insensitive)
        relative = str(file_path.relative_to(base_dir))
        index.setdefault(stem, []).append(relative)

    stems = set(index.keys())
    for stem, files in index.items():
        if len(files) > 1:
            duplicates[stem] = sorted(files)

    return ScanResult(stems=stems, duplicates=duplicates)


def ensure_dir(path: Path, name: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{name} 不存在: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"{name} 不是目录: {path}")


def build_report(
    images_dir: Path,
    xml_dir: Path,
    image_result: ScanResult,
    xml_result: ScanResult,
) -> dict:
    matched = image_result.stems & xml_result.stems
    only_images = sorted(image_result.stems - xml_result.stems)
    only_xml = sorted(xml_result.stems - image_result.stems)

    return {
        "images_dir": str(images_dir),
        "xml_dir": str(xml_dir),
        "image_total_unique_stems": len(image_result.stems),
        "xml_total_unique_stems": len(xml_result.stems),
        "matched_count": len(matched),
        "only_images_count": len(only_images),
        "only_xml_count": len(only_xml),
        "duplicate_image_stems_count": len(image_result.duplicates),
        "duplicate_xml_stems_count": len(xml_result.duplicates),
        "only_images": only_images,
        "only_xml": only_xml,
        "duplicate_image_stems": image_result.duplicates,
        "duplicate_xml_stems": xml_result.duplicates,
    }


def print_report(report: dict) -> None:
    print("=== 图片/XML 配对检查结果 ===")
    print(f"图片目录: {report['images_dir']}")
    print(f"XML 目录: {report['xml_dir']}")
    print(f"图片唯一 stem 数: {report['image_total_unique_stems']}")
    print(f"XML 唯一 stem 数: {report['xml_total_unique_stems']}")
    print(f"成功配对数量: {report['matched_count']}")
    print(f"仅图片存在数量: {report['only_images_count']}")
    print(f"仅 XML 存在数量: {report['only_xml_count']}")
    print(f"图片重名 stem 数: {report['duplicate_image_stems_count']}")
    print(f"XML 重名 stem 数: {report['duplicate_xml_stems_count']}")

    if report["only_images"]:
        print("\n[仅图片存在]")
        for stem in report["only_images"]:
            print(f"- {stem}")
    if report["only_xml"]:
        print("\n[仅 XML 存在]")
        for stem in report["only_xml"]:
            print(f"- {stem}")

    if report["duplicate_image_stems"]:
        print("\n[图片重名 stem]")
        for stem, files in report["duplicate_image_stems"].items():
            print(f"- {stem}: {files}")

    if report["duplicate_xml_stems"]:
        print("\n[XML 重名 stem]")
        for stem, files in report["duplicate_xml_stems"].items():
            print(f"- {stem}: {files}")


def move_or_delete_unmatched(
    images_dir: Path,
    xml_dir: Path,
    only_images: List[str],
    only_xml: List[str],
    image_exts: Iterable[str],
    action: str,
    quarantine_dir: Path | None,
    recursive: bool,
) -> Tuple[int, int]:
    image_pattern = "**/*" if recursive else "*"
    xml_pattern = "**/*" if recursive else "*"
    image_ext_set = {e.lower().strip() for e in image_exts}

    image_map: Dict[str, Path] = {}
    for p in images_dir.glob(image_pattern):
        if p.is_file() and p.suffix.lower() in image_ext_set:
            image_map[p.stem] = p

    xml_map: Dict[str, Path] = {}
    for p in xml_dir.glob(xml_pattern):
        if p.is_file() and p.suffix.lower() == ".xml":
            xml_map[p.stem] = p

    moved_or_deleted_images = 0
    moved_or_deleted_xml = 0

    if action == "move":
        assert quarantine_dir is not None
        quarantine_images = quarantine_dir / "images"
        quarantine_xml = quarantine_dir / "xml"
        quarantine_images.mkdir(parents=True, exist_ok=True)
        quarantine_xml.mkdir(parents=True, exist_ok=True)

    for stem in only_images:
        src = image_map.get(stem)
        if not src:
            continue
        if action == "move":
            dst = quarantine_images / src.name
            shutil.move(str(src), str(dst))
        else:
            src.unlink()
        moved_or_deleted_images += 1

    for stem in only_xml:
        src = xml_map.get(stem)
        if not src:
            continue
        if action == "move":
            dst = quarantine_xml / src.name
            shutil.move(str(src), str(dst))
        else:
            src.unlink()
        moved_or_deleted_xml += 1

    return moved_or_deleted_images, moved_or_deleted_xml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量检查图片与 XML 是否一一配对。")
    parser.add_argument("--images-dir", type=Path, required=True, help="图片目录路径")
    parser.add_argument("--xml-dir", type=Path, required=True, help="XML 目录路径")
    parser.add_argument(
        "--image-exts",
        type=str,
        default=".jpg,.jpeg,.png,.bmp,.webp",
        help="图片扩展名，逗号分隔（示例：.jpg,.png）",
    )
    parser.add_argument("--recursive", action="store_true", help="是否递归扫描子目录")
    parser.add_argument("--case-insensitive", action="store_true", help="文件名大小写不敏感匹配")
    parser.add_argument(
        "--action",
        choices=["report", "move", "delete"],
        default="report",
        help="report: 仅报告；move: 移动不配对文件到隔离目录；delete: 删除不配对文件",
    )
    parser.add_argument(
        "--quarantine-dir",
        type=Path,
        default=None,
        help="action=move 时，不配对文件移动到该目录",
    )
    parser.add_argument("--report-file", type=Path, default=None, help="将检查结果写入 JSON 文件")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="执行 move/delete 时跳过二次确认（自动化场景建议显式传入）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_exts = [x.strip().lower() for x in args.image_exts.split(",") if x.strip()]

    ensure_dir(args.images_dir, "图片目录")
    ensure_dir(args.xml_dir, "XML 目录")

    if args.action == "move" and args.quarantine_dir is None:
        raise ValueError("action=move 时必须提供 --quarantine-dir")

    image_result = collect_files(
        base_dir=args.images_dir,
        extensions=image_exts,
        recursive=args.recursive,
        case_insensitive=args.case_insensitive,
    )
    xml_result = collect_files(
        base_dir=args.xml_dir,
        extensions=[".xml"],
        recursive=args.recursive,
        case_insensitive=args.case_insensitive,
    )

    report = build_report(args.images_dir, args.xml_dir, image_result, xml_result)
    print_report(report)

    if args.report_file:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入报告: {args.report_file}")

    if args.action in {"move", "delete"}:
        if not args.yes:
            print("\n检测到危险操作。请追加 --yes 确认后重试。")
            return 2

        changed_img, changed_xml = move_or_delete_unmatched(
            images_dir=args.images_dir,
            xml_dir=args.xml_dir,
            only_images=report["only_images"],
            only_xml=report["only_xml"],
            image_exts=image_exts,
            action=args.action,
            quarantine_dir=args.quarantine_dir,
            recursive=args.recursive,
        )
        print(f"\n已处理不配对文件: 图片 {changed_img} 个, XML {changed_xml} 个")

    has_issue = (
        report["only_images_count"] > 0
        or report["only_xml_count"] > 0
        or report["duplicate_image_stems_count"] > 0
        or report["duplicate_xml_stems_count"] > 0
    )
    return 1 if has_issue else 0


if __name__ == "__main__":
    raise SystemExit(main())
