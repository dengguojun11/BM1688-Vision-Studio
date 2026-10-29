from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT_PATH = Path(
    "D:/PycharmProjects/labelU/docs/01-任务与标注/"
    "实验进化平台任务接入LabelU完整业务流程图-1920x1080.png"
)
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD_PATH = "C:/Windows/Fonts/msyhbd.ttc"

W, H = 1920, 1080

BG = "#f6f1e7"
TEXT = "#223142"
SUBTEXT = "#5e6877"
LINE = "#6b7888"
TITLE = "#bb623e"
WHITE = "#ffffff"
LANE1 = "#d8e8f7"
LANE2 = "#e2efd5"
LANE3 = "#f8dfd2"
LANE4 = "#ece4fb"
NOTE = "#fff4d7"


def font(size: int, bold: bool = False):
    return ImageFont.truetype(FONT_BOLD_PATH if bold else FONT_PATH, size)


def rr(draw: ImageDraw.ImageDraw, rect, fill, outline=LINE, radius=16, width=2):
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=width)


def mtext(draw, xy, s, f, fill=TEXT, spacing=6):
    draw.multiline_text(xy, s, font=f, fill=fill, spacing=spacing)


def ctext(draw, rect, s, f, fill=TEXT, spacing=6):
    l, t, r, b = rect
    bbox = draw.multiline_textbbox((0, 0), s, font=f, spacing=spacing, align="center")
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = l + (r - l - w) / 2
    y = t + (b - t - h) / 2
    draw.multiline_text((x, y), s, font=f, fill=fill, spacing=spacing, align="center")


def wrap_text(draw, content, f, max_width):
    lines = []
    for raw in str(content).split("\n"):
        words = raw.split(" ")
        if len(words) == 1:
            current = ""
            for ch in raw:
                probe = current + ch
                if draw.textlength(probe, font=f) <= max_width or not current:
                    current = probe
                else:
                    lines.append(current)
                    current = ch
            lines.append(current or "")
            continue
        current = ""
        for word in words:
            probe = word if not current else current + " " + word
            if draw.textlength(probe, font=f) <= max_width or not current:
                current = probe
            else:
                lines.append(current)
                current = word
        lines.append(current or "")
    return "\n".join(lines)


def arrow(draw, start, end, fill=LINE, width=3, arrow_size=10):
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


def draw_step(draw, rect, num, title, fill=WHITE):
    rr(draw, rect, fill=fill)
    l, t, r, b = rect
    badge = (l + 12, t + 10, l + 62, t + 48)
    rr(draw, badge, "#fff7e8", outline=TITLE, radius=10, width=2)
    ctext(draw, badge, f"{num:02d}", font(18, True), fill=TITLE)
    ctext(draw, (l + 70, t + 8, r - 12, b - 8), title, font(17), fill=TEXT, spacing=4)


def draw_bullets(draw, origin, items, col_width, row_gap=8):
    x, y = origin
    cur_y = y
    for item in items:
        wrapped = wrap_text(draw, item, font(13), col_width)
        mtext(draw, (x, cur_y), wrapped, font(13), fill=TEXT, spacing=3)
        bbox = draw.multiline_textbbox((x, cur_y), wrapped, font=font(13), spacing=3)
        cur_y = bbox[3] + row_gap
    return cur_y


def main():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    title_f = font(28, True)
    sub_f = font(14)
    lane_f = font(18, True)
    note_f = font(14)

    mtext(draw, (26, 18), "实验进化平台 -> LabelU 完整业务流程图（1920x1080）", title_f, fill=TITLE)
    mtext(
        draw,
        (28, 54),
        "为避免放大后文字重叠，本版改为“流程节点编号 + 下方分栏详细说明”。",
        sub_f,
        fill=SUBTEXT,
    )

    # Flow area
    flow_rect = (20, 86, 1900, 520)
    rr(draw, flow_rect, "#f7f4eb", radius=22, width=3)
    mtext(draw, (38, 100), "流程主图", lane_f, fill=TITLE)

    lanes = [
        ((40, 140, 1880, 255), LANE1, "A. 接入与导入"),
        ((40, 275, 1880, 420), LANE2, "B. 验证编排与异步实验"),
        ((40, 440, 1880, 505), LANE3, "C. 人工审核后再验证"),
    ]
    for rect, fill, label in lanes:
        rr(draw, rect, fill=fill, radius=18, width=2)
        mtext(draw, (rect[0] + 12, rect[1] + 8), label, font(16, True), fill=TITLE)

    # Row A
    a1 = (60, 176, 360, 236)
    a2 = (400, 176, 700, 236)
    a3 = (740, 176, 1040, 236)
    a4 = (1080, 176, 1380, 236)
    a5 = (1420, 176, 1720, 236)
    for args in [
        (a1, 1, "对方创建任务"),
        (a2, 2, "LabelU 接收并校验"),
        (a3, 3, "解析 dataset 与 datasource"),
        (a4, 4, "创建任务并导入样本"),
        (a5, 5, "同步返回受理结果"),
    ]:
        draw_step(draw, *args)
    arrow(draw, (360, 206), (400, 206))
    arrow(draw, (700, 206), (740, 206))
    arrow(draw, (1040, 206), (1080, 206))
    arrow(draw, (1380, 206), (1420, 206))

    # Row B
    b1 = (60, 320, 250, 385)
    b2 = (300, 290, 530, 345)
    b3 = (300, 355, 530, 410)
    b4 = (580, 290, 810, 345)
    b5 = (580, 355, 810, 410)
    b6 = (860, 290, 1090, 345)
    b7 = (860, 355, 1090, 410)
    b8 = (1140, 290, 1370, 410)
    b9 = (1420, 320, 1650, 385)
    b10 = (1700, 320, 1860, 385)
    for args in [
        (b1, 7, "导入完成后分流"),
        (b2, 8, "自动验证路径"),
        (b3, 9, "对方主动验证路径"),
        (b4, 10, "模型映射"),
        (b5, 11, "创建验证编排记录"),
        (b6, 12, "边端异步执行"),
        (b7, 13, "状态持续推进"),
        (b8, 14, "结果回拉与报告生成"),
        (b9, 19, "查任务 / 最新结果"),
        (b10, 21, "查验证详情 / 结果"),
    ]:
        draw_step(draw, *args)
    arrow(draw, (250, 352), (300, 318))
    arrow(draw, (250, 352), (300, 382))
    arrow(draw, (530, 318), (580, 318))
    arrow(draw, (530, 382), (580, 382))
    arrow(draw, (810, 318), (860, 318))
    arrow(draw, (810, 382), (860, 382))
    arrow(draw, (1090, 318), (1140, 350))
    arrow(draw, (1370, 350), (1420, 350))
    arrow(draw, (1650, 350), (1700, 350))

    # Row C
    c1 = (120, 455, 500, 490)
    c2 = (560, 455, 940, 490)
    c3 = (1000, 455, 1380, 490)
    c4 = (1440, 447, 1820, 498)
    for args in [
        (c1, 15, "进入人工标注 / 审核"),
        (c2, 16, "保存人工修正结果"),
        (c3, 17, "人工审核完成后再验证"),
        (c4, 18, "记录 post_manual_review 语义"),
    ]:
        draw_step(draw, *args)
    arrow(draw, (500, 472), (560, 472))
    arrow(draw, (940, 472), (1000, 472))
    arrow(draw, (1380, 472), (1440, 472))

    # Notes panel bottom
    notes_rect = (20, 540, 1900, 1060)
    rr(draw, notes_rect, WHITE, radius=22, width=3)
    mtext(draw, (38, 554), "分步说明", lane_f, fill=TITLE)

    top_note = (38, 586)
    mtext(
        draw,
        top_note,
        "说明：节点里只保留短标题；详细解释放在下方三栏，适合 1920x1080 查看与放大。",
        note_f,
        fill=SUBTEXT,
    )

    detail_rect = (32, 620, 1888, 980)
    rr(draw, detail_rect, "#fcfbf7", radius=16, width=1)

    col1_x, col2_x, col3_x = 52, 660, 1268
    col_top = 640
    mtext(draw, (col1_x, col_top - 24), "接入 / 导入", font(15, True), fill=TITLE)
    mtext(draw, (col2_x, col_top - 24), "验证 / 人工", font(15, True), fill=TITLE)
    mtext(draw, (col3_x, col_top - 24), "查询 / 增强", font(15, True), fill=TITLE)

    left_items = [
        "01 对方传 external_task_id、dataset、labeling、target、experiment 创建任务。",
        "02 LabelU 校验 schema、字段完整性、external_task_id 幂等，避免重复建任务。",
        "03 解析 s3_uri、image_prefix、metadata_key，复用或创建 S3/MinIO datasource。",
        "04 创建内部 Task；导入图片生成附件和样本；读取 metadata 并落预标注。",
        "05 同步返回“任务已被接管”的受理结果，而不是最终实验结果。",
        "06 这里只导入 images，不把 metadata.json 当样本；同时保存 external_task_id 到内部任务映射。",
        "07 导入完成后进入业务分流：自动验证或等待对方主动发起验证。",
        "08 自动验证：符合规则时由系统发起第一轮验证。",
    ]
    mid_items = [
        "09 对方主动验证：按 external_task_id + model_id + 图片范围发起实验。",
        "10 先做模型映射：优先按“发布到线上时固化保存的 model_id -> export_id”查。",
        "11 创建外部验证编排记录，确定 validation_stage、trigger_source、sample_scope、mode。",
        "12 边端异步执行：排队、打包输入、发送到边端、开始推理。",
        "13 状态推进：QUEUED -> EDGE_INFERENCING -> QUANTIZING -> PULLING_RESULTS -> BUILDING_REPORT。",
        "14 回拉 board_report、bmcv、bmodel_sop 等结果，整理成 summary/detail/artifacts。",
        "15 如果需要人工标注或审核，任务进入 LabelU 工作台由人工处理。",
        "16 人工保存修正结果，推进任务状态。",
        "17 审核完成后再发起一轮新验证，不覆盖自动初检。",
        "18 该轮必须记录 post_manual_review、validation_round、sample_version 等语义。",
    ]
    right_items = [
        "19 对方可查任务总览：是否已导入、是否已完成验证、是否已人工审核。",
        "20 对方可查任务最新结果：这是第一期最推荐的主查询接口。",
        "21 对方可查某次验证详情：看轮次、阶段、模型、样本范围。",
        "22 对方可查某次验证结果：最适合对账与追溯。",
        "23 如果实验未完成，不报错；返回 RUNNING/PENDING、当前阶段、进度、建议多久后再查。",
        "24 result_summary：快速返回是否通过、评分、结论。",
        "25 result_detail：返回推理精度、量化结果、评估指标、耗时、环境信息。",
        "26 artifacts：返回 board_report、bmcv、bmodel_sop、raw_result 地址。",
        "27 后续可增强主动回调，但第一期先以对方查询为主。",
        "28 需要记录回调失败重试、补发、审计和运营视图。",
        "29 原模型删除时默认不静默切最新模型；如允许替代，必须显式记录 replacement。",
        "30 整体闭环：推任务 -> 导入 -> 异步实验 -> 结果汇总 -> 对方查询结果。",
    ]

    draw_bullets(draw, (col1_x, col_top), left_items, 540, row_gap=7)
    draw_bullets(draw, (col2_x, col_top), mid_items, 540, row_gap=7)
    draw_bullets(draw, (col3_x, col_top), right_items, 540, row_gap=7)

    foot = (32, 992, 1888, 1050)
    rr(draw, foot, NOTE, radius=12, width=1)
    ctext(
        draw,
        foot,
        "第一期主方案：对方推任务给我们；实验异步执行；对方按 external_task_id / validation_id 查询状态与结果。",
        font(15),
        fill=TEXT,
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUTPUT_PATH, format="PNG")
    print(str(OUTPUT_PATH))


if __name__ == "__main__":
    main()
