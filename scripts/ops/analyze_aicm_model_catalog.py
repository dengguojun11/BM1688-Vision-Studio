"""Export complete AICM analysis from a saved, credential-free API snapshot.

Usage: python scripts/ops/analyze_aicm_model_catalog.py --input .tmp/aicm/models.json --output-dir .tmp/aicm/report
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from labelu.internal.application.service.aicm_model_analysis import analyze_models


def extract_models(body: Any) -> list[dict]:
    if isinstance(body, list):
        if not all(isinstance(row, dict) for row in body):
            raise ValueError("模型列表中的每一条数据都必须是对象")
        return body
    if isinstance(body, dict):
        for key in ("list", "results", "models", "data"):
            if key in body and isinstance(body[key], (dict, list)):
                return extract_models(body[key])
    raise ValueError("输入文件没有找到模型列表，支持列表或 data/list/results 包装")


def _cell(value: Any) -> str:
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def _csv_cell(value: Any) -> Any:
    # Do not let externally supplied model names become spreadsheet formulas.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _csv(path: Path, columns: list[str], rows: list[list[Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows([[_csv_cell(value) for value in row] for row in rows])


def export_analysis(
    rows: list[dict], output_dir: Path, *, source_info: dict | None = None
) -> dict:
    analysis = analyze_models(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "模型标签分析.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _csv(
        output_dir / "全部模型.csv",
        [
            "模型编号",
            "模型名称",
            "标签组编号",
            "组内时间版本",
            "版本日期（北京时间）",
            "版本日期来源",
            "原始标签顺序",
            "完全同标签的其他模型数",
            "仅部分标签重叠的其他模型数",
            "任一标签相交的其他模型数",
        ],
        [
            [
                model["id"],
                model["name"],
                model["label_group_id"],
                model["version_label"],
                model["version_time"],
                model["version_time_source"],
                "、".join(model["labels"]),
                model["identical_model_count"],
                model["partial_overlap_model_count"],
                model["related_model_count"],
            ]
            for model in analysis["models"]
        ],
    )
    _csv(
        output_dir / "标签集合分组.csv",
        [
            "标签组编号",
            "标签集合",
            "模型数量",
            "按时间排列的模型编号",
            "标签顺序不一致",
        ],
        [
            [
                group["label_group_id"],
                "、".join(group["labels"]),
                group["model_count"],
                "、".join(map(str, group["model_ids"])),
                "是" if group["label_order_mismatch"] else "否",
            ]
            for group in analysis["label_groups"]
        ],
    )
    _csv(
        output_dir / "共享标签倒排.csv",
        [
            "共享标签",
            "涉及模型数量",
            "模型编号",
            "模型名称",
            "组内时间版本",
            "标签组编号",
        ],
        [
            [
                entry["label"],
                entry["model_count"],
                model["id"],
                model["name"],
                model["version_label"],
                model["label_group_id"],
            ]
            for entry in analysis["shared_labels"]
            for model in entry["models"]
        ],
    )
    summary = analysis["summary"]
    lines = [
        "# AICM 已发布模型标签分析",
        "",
        f"本报告分析输入数据的全部 {len(rows)} 条记录，不截断列表。",
        "获取模式（--fetch）会只读请求 AICM 模型目录，并复用系统分页完整性校验；快照模式（--input）只读取已保存的数据，不会再次联网。远端完整性由获取阶段核对接口总数、分页与重复编号，快照分析本身不重新证明远端当前总数。",
        "",
        "## 比较规则",
        "",
        "标签以英文值 key 为准，缺失时用名称 name；忽略准确率、中文展示名和原始排列顺序。英文值区分大小写。空标签不参与分组和交集。",
        "组内优先按有效发布时间排序，无有效发布时间则使用创建时间，同时间按数字编号排序；无有效日期的模型排在组末。日期统一显示为北京时间（东八区），未带时区的来源日期按协调世界时解释后转换。",
        "v1 至 vN 只是同一标签集合的时间排序，不代表性能升级、同一训练血缘或权重兼容。标签集合相同但输出顺序不同时，需单独映射类别编号。",
        "",
        "## 全量汇总",
        "",
        "| 项目 | 数量 |",
        "| --- | ---: |",
        f"| 模型记录总数 | {summary['total_models']} |",
        f"| 非空标签集合组数 | {summary['label_group_count']} |",
        f"| 至少两个模型标签集合完全一致的组数 | {summary['identical_label_group_count']} |",
        f"| 完全一致组涉及的模型数 | {summary['models_in_identical_groups']} |",
        f"| 完全一致组除首版外的模型数 | {summary['extra_versions']} |",
        f"| 与其他模型至少一个标签相同的模型数 | {summary['shared_label_model_count']} |",
        f"| 任一标签相交的无序模型对数 | {summary['related_pair_count']} |",
        f"| 标签集合完全一致的无序模型对数 | {summary['identical_pair_count']} |",
        f"| 仅部分标签相交的无序模型对数 | {summary['partial_overlap_pair_count']} |",
        f"| 标签集合相同但顺序不同的组数 | {summary['order_mismatch_group_count']} |",
        f"| 没有有效标签的模型数 | {summary['unlabeled_model_count']} |",
        f"| 提供有效发布时间的模型数 | {summary['published_time_count']} |",
        f"| 发布时间无效而回退到创建时间的模型数 | {summary['created_time_fallback_count']} |",
        f"| 提供压缩包下载地址的模型数 | {summary['zip_download_url_count']} |",
        f"| 来源标记使用中的模型数 | {summary['in_use_count']} |",
        "",
        "无序模型对指两条模型之间的关系，甲乙与乙甲只计一次。相关模型数包含完全一致和部分相交，不能把三列直接相加。",
        "",
        "## 数据警示",
        "",
    ]
    if source_info:
        source_lines = ["", "## 本次数据来源", ""]
        if source_info.get("mode") == "fetch":
            source_lines += [
                "来源：使用已有发布环境配置，只读获取 AICM 全量模型列表。",
                "",
                "获取阶段已通过系统模型目录的分页完整性校验；本报告与同目录 snapshot.json 对应。",
                "",
            ]
        else:
            source_lines += [
                f"来源：已保存快照 `{source_info.get('input_path', '')}`。本次生成报告未重新请求远端。",
                "",
            ]
        if source_info.get("snapshot_sha256"):
            source_lines += [f"快照校验值：`{source_info['snapshot_sha256']}`。", ""]
        source_lines += [
            f"本次读取 {len(rows)} 条记录、{len({str(row.get('id')) for row in rows if row.get('id') is not None})} 个不同模型编号。",
            "",
        ]
        lines[4:4] = source_lines
    if analysis["warnings"]:
        lines.extend(f"- {warning['message']}。" for warning in analysis["warnings"])
    else:
        lines.append("未发现重复编号、日期缺失、英文值缺失或相同集合顺序不同。")
    lines += [
        "",
        "## 全部模型",
        "",
        "| 编号 | 名称 | 组内时间版本 | 日期 | 原始标签顺序 | 同标签模型数 | 部分重叠模型数 | 任一标签相交模型数 |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for model in analysis["models"]:
        values = [
            model["id"],
            model["name"],
            model["version_label"] or "未分组",
            model["version_time"] or "缺失",
            "、".join(model["labels"]) or "无标签",
            model["identical_model_count"],
            model["partial_overlap_model_count"],
            model["related_model_count"],
        ]
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    lines += ["", "## 全部标签集合及版本", ""]
    for group in analysis["label_groups"]:
        lines += [
            f"### {_cell('、'.join(group['labels']))}",
            "",
            f"组编号：{group['label_group_id']}；共 {group['model_count']} 个模型。",
            "",
            "| 时间版本 | 模型编号 | 模型名称 | 日期 | 日期来源 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for model in group["versions"]:
            values = [
                model["version_label"],
                model["id"],
                model["name"],
                model["version_time"] or "缺失",
                (
                    "发布时间"
                    if model["version_time_source"] == "published_at"
                    else "创建时间" if model["version_time_source"] else "缺失"
                ),
            ]
            lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
        if group["label_order_mismatch"]:
            lines += ["", "该组模型原始标签顺序不一致，不能直接复用输出类别编号。"]
        lines.append("")
    lines += [
        "## 全部共享标签",
        "",
        "| 标签 | 涉及模型数 | 全部模型编号及名称 |",
        "| --- | ---: | --- |",
    ]
    for entry in analysis["shared_labels"]:
        names = "；".join(
            f"{model['id']}：{model['name']}" for model in entry["models"]
        )
        lines.append(
            f"| {_cell(entry['label'])} | {entry['model_count']} | {_cell(names)} |"
        )
    (output_dir / "AICM模型全量分析报告.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return analysis


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从全量模型快照生成中文标签分组与版本分析"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="保存的接口响应或全量模型列表")
    source.add_argument(
        "--fetch",
        action="store_true",
        help="使用本地已有发布配置只读获取 AICM 全部模型，保存脱离凭据的快照",
    )
    parser.add_argument(
        "--environment-id", default="", help="指定已配置的发布环境，默认当前环境"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="报告输出目录，建议使用 .tmp 子目录",
    )
    args = parser.parse_args(argv)
    if args.fetch:
        # Reuse the production pagination/client logic, with no upload or local
        # catalogue sync.  Authentication configuration stays inside that client.
        from labelu.internal.application.service.published_model_catalog import (
            _fetch_all_remote_models,
        )
        from labelu.internal.common.aicm_publish_config import get_active_environment_id

        environment_id = args.environment_id or get_active_environment_id()
        rows = _fetch_all_remote_models(environment_id)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        snapshot = json.dumps(rows, ensure_ascii=False, indent=2)
        (args.output_dir / "snapshot.json").write_text(
            snapshot, encoding="utf-8", newline="\n"
        )
        source_info = {
            "mode": "fetch",
            "snapshot_sha256": hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
        }
    else:
        snapshot_bytes = args.input.read_bytes()
        body = json.loads(snapshot_bytes.decode("utf-8-sig"))
        rows = extract_models(body)
        source_info = {
            "mode": "input",
            "input_path": str(args.input.resolve()),
            "snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
        }
    analysis = export_analysis(rows, args.output_dir, source_info=source_info)
    print(
        json.dumps(
            {
                "summary": analysis["summary"],
                "output_dir": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
