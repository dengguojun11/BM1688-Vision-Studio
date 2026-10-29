from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


OUTPUT_PATH = Path(
    "D:/PycharmProjects/labelU/docs/01-\u4efb\u52a1\u4e0e\u6807\u6ce8/"
    "\u5b9e\u9a8c\u8fdb\u5316\u5e73\u53f0\u4efb\u52a1\u63a5\u5165LabelU\u4e1a\u52a1\u903b\u8f91\u6d41\u7a0b\u56fe.png"
)
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD_PATH = "C:/Windows/Fonts/msyhbd.ttc"

BG = "#f7f2e7"
TEXT = "#243041"
SUBTEXT = "#5b6472"
LINE = "#708090"
TITLE = "#bf613a"
BOX_FILL = "#ffffff"
BOX_ALT = "#fff6ea"
NOTE_FILL = "#fff3d6"
LANE_EXTERNAL = "#dbe9f6"
LANE_PLATFORM = "#e5efd8"
LANE_MANUAL = "#f8dfd4"
LANE_RESULT = "#ede4fb"


def load_font(size: int, bold: bool = False):
    path = FONT_BOLD_PATH if bold else FONT_PATH
    return ImageFont.truetype(path, size)


def draw_round_box(draw: ImageDraw.ImageDraw, box, fill, outline=LINE, radius=18, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_multiline_center(draw, box, text, font, fill=TEXT, spacing=8):
    left, top, right, bottom = box
    bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center")
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = left + (right - left - width) / 2
    y = top + (bottom - top - height) / 2
    draw.multiline_text((x, y), text, font=font, fill=fill, spacing=spacing, align="center")


def draw_text(draw, xy, text, font, fill=TEXT, spacing=8):
    draw.multiline_text(xy, text, font=font, fill=fill, spacing=spacing)


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


def elbow_arrow(draw, points, fill=LINE, width=4, arrow_size=12):
    for start, end in zip(points, points[1:]):
        draw.line((*start, *end), fill=fill, width=width)
    x1, y1 = points[-2]
    x2, y2 = points[-1]
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


def main():
    width, height = 2500, 2100
    image = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(image)

    title_font = load_font(50, bold=True)
    subtitle_font = load_font(24)
    lane_font = load_font(28, bold=True)
    box_font = load_font(24)
    small_font = load_font(20)

    draw_text(
        draw,
        (70, 45),
        "\u5b9e\u9a8c\u8fdb\u5316\u5e73\u53f0 -> LabelU \u4e1a\u52a1\u903b\u8f91\u8bf4\u660e\u6d41\u7a0b\u56fe",
        title_font,
        fill=TITLE,
    )
    draw_text(
        draw,
        (75, 112),
        "\u6309\u5df2\u7ecf\u4e0e\u5bf9\u65b9\u786e\u8ba4\u7684\u5b8c\u6574\u5efa\u8bae\u7248\u5b57\u6bb5\u548c\u63a5\u53e3\u6574\u7406\uff0c"
        "\u805a\u7126\u201c\u4efb\u52a1\u8fdb\u6765\u540e\u6574\u4e2a\u4e1a\u52a1\u95ed\u73af\u600e\u4e48\u8dd1\u201d",
        subtitle_font,
        fill=SUBTEXT,
    )

    lanes = [
        ((60, 180, 2440, 520), LANE_EXTERNAL, "1. \u4efb\u52a1\u63a5\u5165\u4e0e\u6570\u636e\u5165\u573a"),
        ((60, 560, 2440, 1010), LANE_PLATFORM, "2. \u81ea\u52a8\u9a8c\u8bc1\u4e0e\u5bf9\u65b9\u4e3b\u52a8\u9a8c\u8bc1"),
        ((60, 1050, 2440, 1440), LANE_MANUAL, "3. \u4eba\u5de5\u5ba1\u6838\u4e0e\u518d\u9a8c\u8bc1"),
        ((60, 1480, 2440, 2010), LANE_RESULT, "4. \u7ed3\u679c\u5c55\u793a\u4e0e\u56de\u4f20\u5bf9\u65b9"),
    ]

    for lane_box, fill, title in lanes:
        draw_round_box(draw, lane_box, fill=fill, radius=26, width=3)
        draw_text(draw, (lane_box[0] + 24, lane_box[1] + 18), title, lane_font, fill=TITLE)

    # lane 1
    l1 = {
        "a": (110, 280, 510, 390),
        "b": (610, 280, 1030, 390),
        "c": (1130, 280, 1540, 390),
        "d": (1640, 250, 2320, 420),
    }
    for key, box in l1.items():
        fill = BOX_ALT if key == "a" else BOX_FILL
        draw_round_box(draw, box, fill=fill)
    draw_multiline_center(draw, l1["a"], "\u5bf9\u65b9\u53d1\u8d77\u5b9e\u9a8c\u4efb\u52a1\n\u4f20\u5165\u6570\u636e\u96c6\u3001\u9884\u6807\u6ce8\u3001\u76ee\u6807\u6a21\u578b\u4e0a\u4e0b\u6587", box_font)
    draw_multiline_center(draw, l1["b"], "LabelU \u63a5\u53d7\u4efb\u52a1\n\u505a\u5e42\u7b49\u6821\u9a8c\u3001\u5916\u90e8\u4efb\u52a1\u6620\u5c04", box_font)
    draw_multiline_center(draw, l1["c"], "\u521b\u5efa\u5185\u90e8\u4efb\u52a1\n\u5bfc\u5165\u56fe\u7247\u3001\u751f\u6210\u6837\u672c\u3001\u843d\u9884\u6807\u6ce8", box_font)
    draw_multiline_center(draw, l1["d"], "\u540c\u6b65\u8fd4\u56de\u53d7\u7406\u7ed3\u679c\nexternal_task_id \u5df2\u88ab LabelU \u63a5\u7ba1\n\u8fd4\u56de labelu_task_id\u3001\u5bfc\u5165\u6570\u91cf\u3001\u9884\u6807\u6ce8\u6570\u91cf", box_font)
    arrow(draw, (510, 335), (610, 335))
    arrow(draw, (1030, 335), (1130, 335))
    arrow(draw, (1540, 335), (1640, 335))

    # lane 2
    l2 = {
        "a": (130, 690, 650, 820),
        "b": (770, 620, 1290, 760),
        "c": (770, 810, 1290, 950),
        "d": (1410, 620, 1910, 760),
        "e": (1410, 810, 1910, 950),
        "f": (2030, 690, 2320, 880),
    }
    for key, box in l2.items():
        draw_round_box(draw, box, fill=BOX_FILL)
    draw_multiline_center(draw, l2["a"], "\u5bfc\u5165\u7ed3\u675f\u540e\u8fdb\u5165\u4e1a\u52a1\u5224\u65ad\n\u662f\u5426\u9700\u8981\u7acb\u5373\u505a\u4e00\u6b21\u9a8c\u8bc1\uff1f", box_font)
    draw_multiline_center(draw, l2["b"], "\u8def\u5f84 A\uff1a\u81ea\u52a8\u9a8c\u8bc1\n\u6709\u9884\u6807\u6ce8\u65f6\uff0c\u7cfb\u7edf\u4f18\u5148\u7528\n\u9884\u6807\u6ce8 + \u5bf9\u5e94\u6a21\u578b\u505a\u4e00\u6b21\u8fb9\u7aef\u9a8c\u8bc1", box_font)
    draw_multiline_center(draw, l2["c"], "\u8def\u5f84 B\uff1a\u5bf9\u65b9\u4e3b\u52a8\u9a8c\u8bc1\n\u5bf9\u65b9\u540e\u7eed\u4e5f\u53ef\u4ee5\u6309 external_task_id\n\u81ea\u5df1\u6307\u5b9a\u56fe\u7247\u8303\u56f4 + \u6a21\u578b\u53bb\u9a8c\u8bc1", box_font)
    draw_multiline_center(draw, l2["d"], "LabelU \u7edf\u4e00\u7f16\u6392\n\u89e3\u6790\u6a21\u578b\u6620\u5c04\u5173\u7cfb\n\u521b\u5efa\u8fb9\u7aef\u9a8c\u8bc1\u4efb\u52a1", box_font)
    draw_multiline_center(draw, l2["e"], "\u590d\u7528\u5185\u90e8\u73b0\u6709\u80fd\u529b\nSSH / Agent \u5230\u8fb9\u7aef\u6267\u884c\n\u62c9\u56de result_json / report_summary / \u72b6\u6001", box_font)
    draw_multiline_center(draw, l2["f"], "\u5173\u952e\u4e1a\u52a1\u524d\u63d0\n\u5bf9\u65b9 target.model_id\n\u9700\u8981\u5148\u80fd\u6620\u5c04\u5230\n\u6211\u4eec\u5185\u90e8\u53ef\u9a8c\u8bc1\u7684\u6a21\u578b", small_font)
    arrow(draw, (650, 755), (770, 690))
    arrow(draw, (650, 755), (770, 880))
    arrow(draw, (1290, 690), (1410, 690))
    arrow(draw, (1290, 880), (1410, 880))
    arrow(draw, (1910, 690), (2030, 760))
    arrow(draw, (1910, 880), (2030, 810))

    # lane 3
    l3 = {
        "a": (150, 1170, 650, 1290),
        "b": (800, 1170, 1330, 1290),
        "c": (1490, 1170, 1980, 1290),
        "d": (2060, 1140, 2330, 1320),
    }
    for key, box in l3.items():
        draw_round_box(draw, box, fill=BOX_FILL)
    draw_multiline_center(draw, l3["a"], "\u5982\u679c\u8fd9\u6279\u4efb\u52a1\u8fd8\u8981\u4eba\u5de5\u6807\u6ce8\u6216\u5ba1\u6838\n\u5219\u8fdb\u5165 LabelU \u4efb\u52a1\u5185\u5de5\u4f5c\u53f0", box_font)
    draw_multiline_center(draw, l3["b"], "\u6807\u6ce8\u5458 / \u5ba1\u6838\u5458\u5904\u7406\u6837\u672c\n\u4fdd\u5b58\u4eba\u5de5\u4fee\u6b63\u540e\u7684\u7ed3\u679c", box_font)
    draw_multiline_center(draw, l3["c"], "\u4eba\u5de5\u5ba1\u6838\u5b8c\u6210\u540e\n\u518d\u53d1\u8d77\u4e00\u6b21\u9a8c\u8bc1\n\u751f\u6210\u201c\u4eba\u5de5\u590d\u6838\u540e\u9a8c\u8bc1\u7ed3\u679c\u201d", box_font)
    draw_multiline_center(draw, l3["d"], "\u8fd9\u4e00\u8f6e\u9700\u8981\u6709\nvalidation_stage \u8bed\u4e49\n\u533a\u5206\u81ea\u52a8\u521d\u68c0 / \u4e3b\u52a8\u9a8c\u8bc1 / \u4eba\u5de5\u590d\u6838\u540e\u9a8c\u8bc1", small_font)
    arrow(draw, (650, 1230), (800, 1230))
    arrow(draw, (1330, 1230), (1490, 1230))
    arrow(draw, (1980, 1230), (2060, 1230))

    # lane 4
    l4 = {
        "a": (120, 1610, 620, 1740),
        "b": (720, 1610, 1220, 1740),
        "c": (1320, 1610, 1820, 1740),
        "d": (1920, 1610, 2380, 1740),
        "e": (280, 1820, 2220, 1945),
    }
    for key, box in l4.items():
        fill = NOTE_FILL if key == "e" else BOX_FILL
        draw_round_box(draw, box, fill=fill)
    draw_multiline_center(draw, l4["a"], "\u9a8c\u8bc1\u7ed3\u679c\u5148\u5728 LabelU \u5185\u90e8\u5c55\u793a\n\u4efb\u52a1\u9875\u3001BM1688 \u5bfc\u51fa\u9875\u3001\u53d1\u5e03\u9875\u90fd\u8981\u80fd\u770b", box_font)
    draw_multiline_center(draw, l4["b"], "\u5982\u679c\u662f\u81ea\u52a8\u9a8c\u8bc1\n\u56de\u4f20 AUTO_VALIDATION_COMPLETED\n\u5e76\u9644\u4e0a\u8fd9\u4e00\u6b21\u7684\u9a8c\u8bc1\u7ed3\u679c", box_font)
    draw_multiline_center(draw, l4["c"], "\u5982\u679c\u662f\u5bf9\u65b9\u4e3b\u52a8\u9a8c\u8bc1\n\u56de\u4f20 PARTNER_VALIDATION_COMPLETED\n\u8fd4\u56de\u5bf9\u65b9\u8fd9\u6b21\u8bf7\u6c42\u7684\u7ed3\u679c", box_font)
    draw_multiline_center(draw, l4["d"], "\u5982\u679c\u662f\u4eba\u5de5\u5ba1\u6838\u540e\u518d\u9a8c\u8bc1\n\u56de\u4f20 POST_REVIEW_VALIDATION_COMPLETED\n\u4f53\u73b0\u201c\u4eba\u5de5\u590d\u6838\u540e\u6700\u7ec8\u7ed3\u679c\u201d", box_font)
    draw_multiline_center(draw, l4["e"], "\u4e1a\u52a1\u95ed\u73af\u7684\u6838\u5fc3\u4e0d\u53ea\u662f\u201c\u4efb\u52a1\u521b\u5efa\u6210\u529f\u201d\uff0c\u800c\u662f\u201c\u6570\u636e\u8fdb\u6765 -> \u81ea\u52a8\u6216\u4eba\u5de5\u5904\u7406 -> \u751f\u6210\u9a8c\u8bc1\u7ed3\u679c -> \u5185\u5916\u90e8\u540c\u6b65\u770b\u5230\u540c\u4e00\u4efd\u7ed3\u679c\u201d\u3002", box_font)
    arrow(draw, (620, 1675), (720, 1675))
    arrow(draw, (1220, 1675), (1320, 1675))
    arrow(draw, (1820, 1675), (1920, 1675))
    elbow_arrow(draw, [(930, 1740), (930, 1820), (840, 1820)])
    elbow_arrow(draw, [(1570, 1740), (1570, 1820), (1250, 1820)])
    elbow_arrow(draw, [(2150, 1740), (2150, 1820), (1690, 1820)])

    draw_text(
        draw,
        (95, 520),
        "\u4e1a\u52a1\u89c6\u89d2\uff1a\u4ece\u201c\u5bf9\u65b9\u6d3e\u53d1\u4efb\u52a1\u201d\u5f00\u59cb\uff0c\u5230\u201c\u6211\u4eec\u5185\u90e8\u6d88\u5316\u5e76\u53ef\u80fd\u89e6\u53d1\u9a8c\u8bc1\u201d\u7ed3\u675f",
        small_font,
        fill=SUBTEXT,
    )
    draw_text(
        draw,
        (95, 1008),
        "\u4e1a\u52a1\u89c6\u89d2\uff1a\u8fd9\u91cc\u6709\u4e24\u4e2a\u5165\u53e3\uff0c\u4e00\u4e2a\u662f\u81ea\u52a8\u89e6\u53d1\uff0c\u4e00\u4e2a\u662f\u5bf9\u65b9\u4e3b\u52a8\u53d1\u8d77",
        small_font,
        fill=SUBTEXT,
    )
    draw_text(
        draw,
        (95, 1438),
        "\u4e1a\u52a1\u89c6\u89d2\uff1a\u4eba\u5de5\u5ba1\u6838\u540e\u7684\u9a8c\u8bc1\uff0c\u8981\u88ab\u5f53\u6210\u4e00\u7c7b\u5355\u72ec\u7684\u6700\u7ec8\u7ed3\u679c",
        small_font,
        fill=SUBTEXT,
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT_PATH, format="PNG")
    print(str(OUTPUT_PATH))


if __name__ == "__main__":
    main()
