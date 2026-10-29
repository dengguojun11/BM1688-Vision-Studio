import argparse
import os
import shutil
import xml.etree.ElementTree as ET

XML_FOLDER_PATH = r"E:\7G\AI\roadsideillegalvending\data\labels"
BACKUP_DIR = r"E:\7G\AI\roadsideillegalvending\firstdata\labels_deleted_backup"
LOG_PATH = r"E:\7G\AI\roadsideillegalvending\firstdata\size_check_report.txt"


def _parse_size(root):
    size_elem = root.find("size")
    if size_elem is None:
        return None, None, "missing_size"

    width_elem = size_elem.find("width")
    height_elem = size_elem.find("height")
    if width_elem is None or height_elem is None:
        return None, None, "missing_width_or_height"

    try:
        width = int(float((width_elem.text or "").strip()))
        height = int(float((height_elem.text or "").strip()))
    except (ValueError, TypeError):
        return None, None, "invalid_width_or_height"

    return width, height, ""


def _write_log(log_path, rows):
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("filename\taction\treason\twidth\theight\n")
        for row in rows:
            f.write(
                f"{row['filename']}\t{row['action']}\t{row['reason']}\t{row['width']}\t{row['height']}\n"
            )


def remove_zero_size_xml(xml_folder_path, mode="delete", dry_run=False, backup_dir=None, log_path=None):
    if not os.path.isdir(xml_folder_path):
        raise FileNotFoundError(f"目录不存在: {xml_folder_path}")

    if mode == "move" and not backup_dir and not dry_run:
        raise ValueError("mode=move 时必须提供 backup_dir")

    if mode == "move" and backup_dir and not dry_run:
        os.makedirs(backup_dir, exist_ok=True)

    xml_files = [f for f in os.listdir(xml_folder_path) if f.lower().endswith(".xml")]
    total = len(xml_files)
    invalid = 0
    processed = 0
    errors = 0
    logs = []

    for filename in xml_files:
        xml_file_path = os.path.join(xml_folder_path, filename)
        width = ""
        height = ""

        try:
            tree = ET.parse(xml_file_path)
            root = tree.getroot()
        except ET.ParseError:
            errors += 1
            logs.append(
                {"filename": filename, "action": "skip", "reason": "xml_parse_error", "width": "", "height": ""}
            )
            print(f"解析失败，跳过: {xml_file_path}")
            continue

        width, height, reason = _parse_size(root)
        if reason:
            errors += 1
            logs.append({"filename": filename, "action": "skip", "reason": reason, "width": "", "height": ""})
            print(f"结构异常，跳过: {xml_file_path} ({reason})")
            continue

        if width <= 0 or height <= 0:
            invalid += 1
            action = "dry_run"
            if not dry_run:
                if mode == "move":
                    target_path = os.path.join(backup_dir, filename)
                    shutil.move(xml_file_path, target_path)
                    action = "moved"
                else:
                    os.remove(xml_file_path)
                    action = "deleted"
                processed += 1

            logs.append(
                {
                    "filename": filename,
                    "action": action,
                    "reason": "non_positive_size",
                    "width": width,
                    "height": height,
                }
            )
            print(f"{action}: {filename} (width={width}, height={height})")

    if log_path:
        _write_log(log_path, logs)
        print(f"\n日志已输出: {log_path}")

    print("\n检查完成")
    print(f"XML总数: {total}")
    print(f"尺寸异常(width<=0 or height<=0): {invalid}")
    if dry_run:
        print("当前为 dry-run，仅预览，未实际删除/移动")
    else:
        print(f"已处理: {processed}")
    print(f"解析/结构异常: {errors}")


def main():
    parser = argparse.ArgumentParser(description="删除或移动 size 异常(width<=0 or height<=0) 的 VOC XML")
    parser.add_argument("--xml-dir", default=XML_FOLDER_PATH, help="XML目录")
    parser.add_argument("--mode", choices=["delete", "move"], default="delete", help="处理方式: delete 或 move")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不真正删除/移动")
    parser.add_argument("--backup-dir", default=BACKUP_DIR, help="mode=move 时的备份目录")
    parser.add_argument("--log-path", default=LOG_PATH, help="日志文件输出路径")
    args = parser.parse_args()

    remove_zero_size_xml(
        xml_folder_path=args.xml_dir,
        mode=args.mode,
        dry_run=args.dry_run,
        backup_dir=args.backup_dir,
        log_path=args.log_path,
    )


if __name__ == "__main__":
    main()