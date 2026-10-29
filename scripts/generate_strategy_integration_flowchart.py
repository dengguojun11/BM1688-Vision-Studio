from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont


OUTPUT_PATH = r"D:\PycharmProjects\labelU\docs\01-任务与标注\实验进化平台任务接入LabelU流程图.png"
FONT_PATH = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD_PATH = r"C:\Windows\Fonts\msyhbd.ttc"


BG = "#f5f2e8"
PANEL = "#fbfaf6"
TEXT = "#1f2937"
LINE = "#5b6472"
HEADER = "#b85c38"
ACCENT = "#d7e7f5"
ACCENT2 = "#e7efd8"
ACCENT3 = "#f8e0d2"
ACCENT4 = "#efe5fb"


def load_font(size: int, bold: bool = False):
    return ImageFont.truetype(FONT_BOLD_PATH if bold else FONT_PATH, size)


def draw_round_box(draw: ImageDraw.ImageDraw, box, fill, outline=LINE, radius=18, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_text_block(draw, xy, text, font, fill=TEXT, line_spacing=10):
    x, y = xy
    bbox = draw.multiline_textbbox((x, y), text, font=font, spacing=line_spacing)
    draw.multiline_text((x, y), text, font=font, fill=fill, spacing=line_spacing)
    return bbox


def arrow(draw, start, end, fill=LINE, width=4, arrow_size=10):
    x1, y1 = start
    x2, y2 = end
    draw.line((x1, y1, x2, y2), fill=fill, width=width)
    if abs(x2 - x1) >= abs(y2 - y1):
        if x2 >= x1:
            pts = [(x2, y2), (x2 - arrow_size, y2 - arrow_size // 2), (x2 - arrow_size, y2 + arrow_size // 2)]
        else:
            pts = [(x2, y2), (x2 + arrow_size, y2 - arrow_size // 2), (x2 + arrow_size, y2 + arrow_size // 2)]
    else:
        if y2 >= y1:
            pts = [(x2, y2), (x2 - arrow_size // 2, y2 - arrow_size), (x2 + arrow_size // 2, y2 - arrow_size)]
        else:
            pts = [(x2, y2), (x2 - arrow_size // 2, y2 + arrow_size), (x2 + arrow_size // 2, y2 + arrow_size)]
    draw.polygon(pts, fill=fill)


def center_text(draw, box, text, font, fill=TEXT, spacing=8):
    left, top, right, bottom = box
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = left + (right - left - w) / 2
    y = top + (bottom - top - h) / 2
    draw.multiline_text((x, y), text, font=font, fill=fill, spacing=spacing, align="center")


def main():
    W, H = 2400, 3400
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    title_font = load_font(46, bold=True)
    subtitle_font = load_font(24)
    section_font = load_font(26, bold=True)
    box_font = load_font(22)
    small_font = load_font(20)

    draw_text_block(draw, (80, 55), "实验进化平台 -> LabelU 接口对接与结果回传流程图", title_font, fill=HEADER)
    draw_text_block(
        draw,
        (85, 120),
        "基于当前已沟通后的完整建议版字段与接口，区分：已实现链路、建议新增链路",
        subtitle_font,
        fill="#4b5563",
    )

    sections = [
        ((60, 190, 1160, 1240), ACCENT, "1. 外部任务接入（当前已实现）"),
        ((1240, 190, 2340, 1240), ACCENT2, "2. 统一边端验证（内部已实现）"),
        ((60, 1290, 1160, 2170), ACCENT3, "3. 人工审核后再验证（底层可复用）"),
        ((1240, 1290, 2340, 2170), ACCENT4, "4. 对方主动发起验证（建议新增外部门面接口）"),
        ((60, 2230, 2340, 3290), PANEL, "5. 结果展示与回传（内部展示已实现，外部回传待补）"),
    ]

    for box, fill, title in sections:
        draw_round_box(draw, box, fill=fill, radius=28, width=3)
        draw_text_block(draw, (box[0] + 24, box[1] + 20), title, section_font, fill=HEADER)

    # section 1
    boxes = {
        "a": (120, 300, 520, 410),
        "b": (630, 300, 1080, 430),
        "c": (630, 470, 1080, 590),
        "d": (630, 640, 1080, 760),
        "e": (630, 810, 1080, 930),
        "f": (630, 980, 1080, 1120),
    }
    draw_round_box(draw, boxes["a"], "#fff7ed")
    center_text(draw, boxes["a"], "实验进化平台 / 对方系统", box_font)
    draw_round_box(draw, boxes["b"], "#ffffff")
    center_text(draw, boxes["b"], "POST /api/v1/integrations/strategy-tasks\n完整建议版请求", box_font)
    draw_round_box(draw, boxes["c"], "#ffffff")
    center_text(draw, boxes["c"], "strategy_task_integration\n校验 schema + external_task_id 幂等", box_font)
    draw_round_box(draw, boxes["d"], "#ffffff")
    center_text(draw, boxes["d"], "解析 dataset.s3_uri\n复用 / 创建 S3 datasource", box_font)
    draw_round_box(draw, boxes["e"], "#ffffff")
    center_text(draw, boxes["e"], "创建 / 更新内部 Task\n导入图片 + 读取 metadata + 落预标注", box_font)
    draw_round_box(draw, boxes["f"], "#ffffff")
    center_text(draw, boxes["f"], "返回创建结果\nlabelu_task_id / import_status /\nsample_count / pre_annotation_count", box_font)

    arrow(draw, (520, 355), (630, 355))
    arrow(draw, (855, 430), (855, 470))
    arrow(draw, (855, 590), (855, 640))
    arrow(draw, (855, 760), (855, 810))
    arrow(draw, (855, 930), (855, 980))

    draw_text_block(
        draw,
        (120, 470),
        "已沟通并采用的字段：\n"
        "schema_version\nexternal_task_id\n"
        "task_name\n"
        "task_type\n"
        "dataset\n"
        "labeling\n"
        "target\n"
        "experiment",
        small_font,
    )

    # section 2
    boxes2 = {
        "g": (1320, 300, 1820, 440),
        "h": (1320, 500, 1820, 620),
        "i": (1320, 680, 1820, 800),
        "j": (1320, 860, 1820, 980),
        "k": (1320, 1040, 1820, 1160),
        "l": (1900, 520, 2270, 760),
    }
    for key in ["g", "h", "i", "j", "k", "l"]:
        draw_round_box(draw, boxes2[key], "#ffffff")
    center_text(draw, boxes2["g"], "是否需要自动验证？\n有预标注 -> 倾向 run_and_eval\n无标注 -> 倾向 run_only", box_font)
    center_text(draw, boxes2["h"], "解析 target.model_id -> 内部 export_id\n当前待补：模型映射层", box_font)
    center_text(draw, boxes2["i"], "创建 YoloEdgeValidationJob", box_font)
    center_text(draw, boxes2["j"], "POST /api/v1/yolo-edge-validation/jobs/{job_id}/run", box_font)
    center_text(draw, boxes2["k"], "边端执行与结果回拉\nSSH / Agent -> board_report / bmcv / bmodel_sop", box_font)
    center_text(draw, boxes2["l"], "当前已实现的内部验证接口：\nPOST /yolo-edge-validation/jobs\nPOST /jobs/{job_id}/run\nGET /jobs\nGET /jobs/{job_id}\nPOST /recommend-thresholds\nPOST /refine-thresholds", small_font)
    arrow(draw, (1820, 370), (1900, 620))
    arrow(draw, (1570, 440), (1570, 500))
    arrow(draw, (1570, 620), (1570, 680))
    arrow(draw, (1570, 800), (1570, 860))
    arrow(draw, (1570, 980), (1570, 1040))

    # section 3
    boxes3 = {
        "m": (120, 1400, 560, 1520),
        "n": (120, 1590, 560, 1710),
        "o": (120, 1780, 560, 1900),
        "p": (650, 1590, 1090, 1750),
        "q": (650, 1810, 1090, 1980),
    }
    for key in boxes3:
        draw_round_box(draw, boxes3[key], "#ffffff")
    center_text(draw, boxes3["m"], "等待人工审核 / 标注", box_font)
    center_text(draw, boxes3["n"], "标注员 / 审核员在任务内处理样本", box_font)
    center_text(draw, boxes3["o"], "PATCH /api/v1/tasks/{task_id}/samples/{sample_id}", box_font)
    center_text(draw, boxes3["p"], "任务样本进入人工复核后状态\n当前已有样本保存与任务完成推进能力", box_font)
    center_text(draw, boxes3["q"], "再次创建边端验证任务\n建议补 validation_stage=post_manual_review", box_font)
    arrow(draw, (340, 1520), (340, 1590))
    arrow(draw, (340, 1710), (340, 1780))
    arrow(draw, (560, 1840), (650, 1670))
    arrow(draw, (870, 1750), (870, 1810))

    # section 4
    boxes4 = {
        "r": (1320, 1400, 1820, 1540),
        "s": (1320, 1610, 1820, 1750),
        "t": (1320, 1820, 1820, 1960),
        "u": (1900, 1530, 2270, 1830),
    }
    for key in boxes4:
        draw_round_box(draw, boxes4[key], "#ffffff")
    center_text(draw, boxes4["r"], "对方主动发起验证", box_font)
    center_text(draw, boxes4["s"], "建议新增外部接口：\nPOST /api/v1/integrations/strategy-tasks/{external_task_id}/validate\n或 /integrations/strategy-validations", box_font)
    center_text(draw, boxes4["t"], "外部验证编排层\n解析 external_task_id + model_id + 图片范围\n再复用内部 YoloEdgeValidationJob", box_font)
    center_text(draw, boxes4["u"], "结论：\n底层验证能力已具备\n但不能直接把内部 /yolo-edge-validation/jobs\n原样暴露给对方", small_font)
    arrow(draw, (1570, 1540), (1570, 1610))
    arrow(draw, (1570, 1750), (1570, 1820))
    arrow(draw, (1820, 1890), (1900, 1680))

    # section 5
    boxes5 = {
        "v": (130, 2370, 610, 2510),
        "w": (720, 2370, 1210, 2510),
        "x": (1320, 2370, 1810, 2510),
        "y": (1920, 2370, 2280, 2510),
        "z": (250, 2620, 940, 2840),
        "aa": (1080, 2620, 1750, 2840),
        "ab": (1860, 2620, 2230, 2840),
        "ac": (380, 2940, 2030, 3170),
    }
    fills = {
        "v": "#ffffff",
        "w": "#ffffff",
        "x": "#ffffff",
        "y": "#ffffff",
        "z": "#ffffff",
        "aa": "#ffffff",
        "ab": "#ffffff",
        "ac": "#fff7ed",
    }
    for key, box in boxes5.items():
        draw_round_box(draw, box, fills[key])
    center_text(draw, boxes5["v"], "验证结果内部沉淀\nresult_json / report_summary / status", box_font)
    center_text(draw, boxes5["w"], "BM1688 导出页展示", box_font)
    center_text(draw, boxes5["x"], "发布页展示", box_font)
    center_text(draw, boxes5["y"], "边端验证任务详情 / 历史展示", box_font)
    center_text(draw, boxes5["z"], "建议新增回写时机：\nIMPORT_COMPLETED\nAUTO_VALIDATION_COMPLETED", box_font)
    center_text(draw, boxes5["aa"], "建议新增回写时机：\nPARTNER_VALIDATION_COMPLETED\nPOST_REVIEW_VALIDATION_COMPLETED", box_font)
    center_text(draw, boxes5["ab"], "FAILED\n失败原因回写", box_font)
    center_text(draw, boxes5["ac"], "建议对方提供回调接口，LabelU 主动推送阶段结果。\n当前系统内部展示已经基本具备；真正待补的是：外部模型映射、外部门面验证接口、结果回写与验证阶段语义。", box_font)

    arrow(draw, (610, 2440), (720, 2440))
    arrow(draw, (1210, 2440), (1320, 2440))
    arrow(draw, (1810, 2440), (1920, 2440))
    arrow(draw, (370, 2510), (520, 2620))
    arrow(draw, (1380, 2510), (1410, 2620))
    arrow(draw, (2100, 2510), (2050, 2620))
    arrow(draw, (595, 2840), (800, 2940))
    arrow(draw, (1415, 2840), (1205, 2940))
    arrow(draw, (2045, 2840), (1605, 2940))

    img.save(OUTPUT_PATH, format="PNG")
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
