from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT_PATH = Path(
    "D:/PycharmProjects/labelU/docs/01-任务与标注/"
    "实验进化平台任务接入LabelU完整业务流程图.png"
)
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD_PATH = "C:/Windows/Fonts/msyhbd.ttc"

BG = "#f6f1e7"
TEXT = "#233142"
SUBTEXT = "#5b6472"
LINE = "#6f7c8b"
TITLE = "#bd623b"
WHITE = "#ffffff"
NOTE = "#fff4d8"
LANE_1 = "#d8e8f7"
LANE_2 = "#e4f0d6"
LANE_3 = "#f9e0d3"
LANE_4 = "#ece5fb"
LANE_5 = "#efece3"


def load_font(size: int, bold: bool = False):
    return ImageFont.truetype(FONT_BOLD_PATH if bold else FONT_PATH, size)


def box(draw: ImageDraw.ImageDraw, rect, fill, outline=LINE, radius=18, width=2):
    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=width)


def text(draw, xy, content, font, fill=TEXT, spacing=8):
    draw.multiline_text(xy, content, font=font, fill=fill, spacing=spacing)


def center(draw, rect, content, font, fill=TEXT, spacing=8):
    left, top, right, bottom = rect
    bbox = draw.multiline_textbbox((0, 0), content, font=font, spacing=spacing, align="center")
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = left + (right - left - width) / 2
    y = top + (bottom - top - height) / 2
    draw.multiline_text((x, y), content, font=font, fill=fill, spacing=spacing, align="center")


def arrow(draw, start, end, fill=LINE, width=4, arrow_size=12):
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


def elbow(draw, pts, fill=LINE, width=4, arrow_size=12):
    for start, end in zip(pts, pts[1:]):
        draw.line((*start, *end), fill=fill, width=width)
    arrow(draw, pts[-2], pts[-1], fill=fill, width=width, arrow_size=arrow_size)


def main():
    width, height = 2900, 4800
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(54, bold=True)
    subtitle_font = load_font(24)
    lane_font = load_font(30, bold=True)
    box_font = load_font(23)
    small_font = load_font(19)
    tiny_font = load_font(17)

    text(
        draw,
        (70, 42),
        "实验进化平台 -> LabelU 完整业务流程图",
        title_font,
        fill=TITLE,
    )
    text(
        draw,
        (76, 110),
        "按当前已经确认的完整建议版协议整理。包含任务接入、异步实验、模型映射、人工审核、结果查询与可选回调增强。",
        subtitle_font,
        fill=SUBTEXT,
    )

    lanes = [
        ((50, 180, 2850, 930), LANE_1, "1. 外部任务接入与数据导入"),
        ((50, 970, 2850, 1960), LANE_2, "2. 自动验证 / 对方主动验证 / 异步实验执行"),
        ((50, 2000, 2850, 2740), LANE_3, "3. 人工审核、复核后再验证"),
        ((50, 2780, 2850, 4050), LANE_4, "4. 结果查询、异步轮询与结果返回"),
        ((50, 4090, 2850, 4700), LANE_5, "5. 后续增强项：主动回调、补发、审计"),
    ]
    for rect, fill, title in lanes:
        box(draw, rect, fill=fill, radius=26, width=3)
        text(draw, (rect[0] + 24, rect[1] + 16), title, lane_font, fill=TITLE)

    # Lane 1
    l1 = {
        "a": (110, 280, 520, 410),
        "b": (610, 280, 1060, 410),
        "c": (1150, 280, 1640, 410),
        "d": (1730, 280, 2220, 410),
        "e": (2310, 250, 2780, 440),
        "f": (180, 520, 2700, 830),
    }
    for key, rect in l1.items():
        box(draw, rect, WHITE if key != "a" else "#fff7ed")
    center(draw, l1["a"], "01\n对方系统发起任务创建\n传入 external_task_id、dataset、labeling、target、experiment", box_font)
    center(draw, l1["b"], "02\nLabelU 接口层接收请求\n校验 schema、字段完整性、external_task_id 幂等", box_font)
    center(draw, l1["c"], "03\n解析数据集位置\n拆解 dataset.s3_uri、image_prefix、metadata_key\n复用或创建 S3 / MinIO datasource", box_font)
    center(draw, l1["d"], "04\n创建或更新内部任务\n生成 Task、导入图片、生成样本、落预标注", box_font)
    center(draw, l1["e"], "05\n同步返回受理结果\n告诉对方：任务已被接管\n返回 labelu_task_id、导入状态、样本数、预标注数", box_font)
    center(
        draw,
        l1["f"],
        "06 这一步内部具体在做什么\n"
        "a. 只导入 dataset.image_prefix 下图片，避免把 metadata.json 当样本导入\n"
        "b. 读取 metadata.json，把 target_boxes 转成 TaskPreAnnotation\n"
        "c. 保存 StrategyTaskIntegration 映射，记录 external_task_id -> labelu_task_id -> datasource_id\n"
        "d. 这里的接口成功，表示“任务被受理并开始导入”，不代表最终实验结果已经出来",
        small_font,
    )
    arrow(draw, (520, 345), (610, 345))
    arrow(draw, (1060, 345), (1150, 345))
    arrow(draw, (1640, 345), (1730, 345))
    arrow(draw, (2220, 345), (2310, 345))

    # Lane 2
    l2 = {
        "a": (120, 1080, 640, 1230),
        "b": (760, 1050, 1320, 1200),
        "c": (760, 1260, 1320, 1410),
        "d": (1450, 1030, 2050, 1205),
        "e": (1450, 1240, 2050, 1415),
        "f": (2170, 1030, 2740, 1205),
        "g": (2170, 1240, 2740, 1415),
        "h": (160, 1490, 2730, 1880),
    }
    for rect in l2.values():
        box(draw, rect, WHITE)
    center(draw, l2["a"], "07\n导入完成后进入业务分流判断\n是否要立刻自动验证？\n或者等待对方主动发起验证？", box_font)
    center(draw, l2["b"], "08A 自动验证路径\n如果任务具备预标注或符合规则\n系统自动准备发起一轮验证", box_font)
    center(draw, l2["c"], "08B 对方主动验证路径\n对方后续调用验证接口\n按 external_task_id + model_id + 图片范围发起实验", box_font)
    center(draw, l2["d"], "09\n模型映射层处理\n先把外部 model_id 映射到内部 export_id\n这里优先按“发布到线上时固化保存的模型映射”查", box_font)
    center(draw, l2["e"], "10\n创建外部验证编排记录\n确定 validation_stage、trigger_source、sample_scope、mode\n再转成内部 YoloEdgeValidationJob", box_font)
    center(draw, l2["f"], "11\n异步实验执行开始\n排队、打包输入、发送到边端\n边端开始推理 / 量化 / 结果生成", box_font)
    center(draw, l2["g"], "12\n内部状态持续推进\nPENDING -> RUNNING\nQUEUED -> EDGE_INFERENCING -> QUANTIZING -> PULLING_RESULTS -> BUILDING_REPORT", box_font)
    center(
        draw,
        l2["h"],
        "13 异步实验阶段到底在做什么\n"
        "a. PACKING：把样本、模型、阈值、上下文整理成边端可执行输入\n"
        "b. EDGE_INFERENCING：边端推理，产出基础推理结果\n"
        "c. QUANTIZING：如果流程需要量化，生成量化结果和比对数据\n"
        "d. PULLING_RESULTS：把边端结果、board_report、bmcv、bmodel_sop 等回拉到 LabelU\n"
        "e. BUILDING_REPORT：把原始结果整理成 report_summary、结构化指标、结果文件地址\n"
        "f. 如果模型映射失败、边端失败、结果解析失败，就进入 FAILED 并保留 error.code / error.stage",
        small_font,
    )
    arrow(draw, (640, 1155), (760, 1125))
    arrow(draw, (640, 1155), (760, 1335))
    arrow(draw, (1320, 1125), (1450, 1120))
    arrow(draw, (1320, 1335), (1450, 1330))
    arrow(draw, (2050, 1120), (2170, 1120))
    arrow(draw, (2050, 1330), (2170, 1330))

    # Lane 3
    l3 = {
        "a": (120, 2120, 650, 2280),
        "b": (790, 2120, 1330, 2280),
        "c": (1470, 2120, 1990, 2280),
        "d": (2120, 2095, 2740, 2305),
        "e": (210, 2400, 2690, 2640),
    }
    for rect in l3.values():
        box(draw, rect, WHITE if rect != l3["e"] else NOTE)
    center(draw, l3["a"], "14\n如果任务需要人工标注 / 审核\n任务进入 LabelU 工作台\n由标注员、审核员处理样本", box_font)
    center(draw, l3["b"], "15\n人工保存结果\n通过任务样本保存接口写入人工修正结果\n推进任务完成状态", box_font)
    center(draw, l3["c"], "16\n人工审核完成后\n触发再验证\n这是一轮新的验证，不是覆盖原验证", box_font)
    center(draw, l3["d"], "17\n再验证必须带清晰语义\nvalidation_stage=post_manual_review\n并记录 validation_round、sample_version、trigger_source", box_font)
    center(
        draw,
        l3["e"],
        "18 这一步为什么要单独定义\n"
        "a. 自动验证结果和人工复核后的验证结果，业务意义完全不同\n"
        "b. 查询历史时必须知道哪次是自动初检，哪次是人工审核后的最终结果\n"
        "c. 如果原模型已删除，但只是查历史结果，不需要重新猜模型；直接返回历史验证记录\n"
        "d. 如果要重新发起新验证，默认走严格模式，找不到原模型就报错，不默认切最新模型",
        small_font,
    )
    arrow(draw, (650, 2200), (790, 2200))
    arrow(draw, (1330, 2200), (1470, 2200))
    arrow(draw, (1990, 2200), (2120, 2200))

    # Lane 4
    l4 = {
        "a": (110, 2910, 650, 3080),
        "b": (780, 2910, 1320, 3080),
        "c": (1450, 2910, 1990, 3080),
        "d": (2120, 2910, 2760, 3080),
        "e": (120, 3170, 2760, 3460),
        "f": (180, 3540, 860, 3940),
        "g": (1080, 3540, 1780, 3940),
        "h": (2010, 3540, 2720, 3940),
    }
    for key, rect in l4.items():
        box(draw, rect, WHITE if key not in {"e", "f", "g", "h"} else (NOTE if key == "e" else WHITE))
    center(draw, l4["a"], "19\n对方查询任务总览\nGET /strategy-tasks/{external_task_id}\n看任务是否已导入、是否已完成验证、是否已人工审核", box_font)
    center(draw, l4["b"], "20\n对方查询任务最新结果\nGET /strategy-tasks/{external_task_id}/result\n最适合第一期主方案", box_font)
    center(draw, l4["c"], "21\n对方查询某次验证详情\nGET /strategy-validations/{validation_id}\n看验证阶段、轮次、模型、样本范围", box_font)
    center(draw, l4["d"], "22\n对方查询某次验证结果\nGET /strategy-validations/{validation_id}/result\n查精确结果，适合对账和追溯", box_font)
    center(
        draw,
        l4["e"],
        "23 如果对方在实验还没完成时来查询\n"
        "不要报错，而是正常返回当前状态：\n"
        "status=PENDING / RUNNING，status_stage=QUEUED / EDGE_INFERENCING / QUANTIZING ...\n"
        "同时返回 progress_percent、recommended_poll_after_seconds、estimated_remaining_seconds\n"
        "也就是说：接口语义是“结果未就绪，但任务在正常执行中”",
        small_font,
    )
    center(
        draw,
        l4["f"],
        "24 摘要结果层\nresult_summary\n给对方快速看：\n是否通过、总评分、结论、最新轮次状态", box_font)
    center(
        draw,
        l4["g"],
        "25 结构化明细层\nresult_detail\n给对方程序化消费：\n推理精度、量化结果、评估指标、耗时、环境信息", box_font)
    center(
        draw,
        l4["h"],
        "26 制品与报告层\nartifacts\n给对方拿文件地址：\nboard_report、bmcv、bmodel_sop、raw_result", box_font)
    arrow(draw, (650, 2995), (780, 2995))
    arrow(draw, (1320, 2995), (1450, 2995))
    arrow(draw, (1990, 2995), (2120, 2995))
    elbow(draw, [(980, 3080), (980, 3170), (850, 3170)])
    elbow(draw, [(1750, 3080), (1750, 3170), (1450, 3170)])
    arrow(draw, (900, 3460), (520, 3540))
    arrow(draw, (1440, 3460), (1430, 3540))
    arrow(draw, (2080, 3460), (2360, 3540))

    # Lane 5
    l5 = {
        "a": (130, 4200, 760, 4450),
        "b": (910, 4200, 1540, 4450),
        "c": (1690, 4200, 2320, 4450),
        "d": (2470, 4200, 2770, 4450),
        "e": (260, 4510, 2640, 4635),
    }
    for key, rect in l5.items():
        box(draw, rect, WHITE if key != "e" else NOTE)
    center(draw, l5["a"], "27 后续增强：主动回调\n如果对方以后补接口\n可按事件主动推送 IMPORT_COMPLETED / VALIDATION_COMPLETED", box_font)
    center(draw, l5["b"], "28 回调失败重试与补发\n记录 callback log\n失败后自动重试，支持人工补发", box_font)
    center(draw, l5["c"], "29 审计与追踪\n记录 requested_model_id、resolved_model_id、mapping_mode、payload 版本", box_font)
    center(draw, l5["d"], "30 运营视图\n谁卡在模型映射\n谁卡在量化\n谁结果未回写", box_font)
    center(
        draw,
        l5["e"],
        "31 这张图的最终业务闭环\n对方推任务 -> LabelU 接入并导入 -> 自动或人工触发实验 -> 边端异步执行 -> LabelU 汇总结果 -> 对方按任务或验证单号查询结果。\n第一期先以“查询式取结果”为主；如果后续对方补回调接口，再增加主动推送能力。",
        tiny_font,
    )
    arrow(draw, (760, 4325), (910, 4325))
    arrow(draw, (1540, 4325), (1690, 4325))
    arrow(draw, (2320, 4325), (2470, 4325))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT_PATH, format="PNG")
    print(str(OUTPUT_PATH))


if __name__ == "__main__":
    main()
