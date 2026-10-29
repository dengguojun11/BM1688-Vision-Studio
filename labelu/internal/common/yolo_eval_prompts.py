"""YOLO 训练 AI 评估提示词（可编辑，存 BASE_DATA_DIR）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    get_runtime_override,
    read_group_values,
    resolve_value,
    write_group_values,
)
from labelu.internal.common.yolo_train_hyperparams import (
    GARBAGE_EXPOSURE_RECT_PRESET_SLUG,
    iteration_baseline_recommended_train_params,
)


def _recommended_train_params_json_example(*, include_model_weights: bool = False) -> str:
    """54.99 垃圾暴露 SOP 基线，供提示词文末 JSON 示例（程序可解析）。"""
    params = iteration_baseline_recommended_train_params(
        GARBAGE_EXPOSURE_RECT_PRESET_SLUG
    )
    if include_model_weights:
        params = {**params, "model_weights": None}
    return json.dumps({"recommended_train_params": params}, ensure_ascii=False)


_SOP_RECOMMENDED_JSON = _recommended_train_params_json_example()
_SOP_RECOMMENDED_JSON_WITH_WEIGHTS = _recommended_train_params_json_example(
    include_model_weights=True
)

DEFAULT_FULL_ANALYSIS = (
    """# YOLO11 训练全维度分析报告生成提示词（标准版·含指标图）

你是专业的 YOLO 目标检测算法训练调优专家。请结合**训练配置参数 + 训练输出指标图表**，生成一份可直接用于下一轮迭代调参的工程化分析报告。

## 一、输入信息

1. **训练配置参数（args.yaml）**

真实内容由系统在文末「【训练配置 args.yaml】」中自动插入**当前作业**的完整 YAML 文本，请以其为准进行分析；**勿套用任何离线示例中的固定数值**。

2. **训练输出指标图片（多模态附件）**

系统将尽可能附上训练产物目录中的常见图表（若文件存在），包括但不限于：

- results.png：训练曲线（Box/Cls/DFL loss、mAP50、mAP50-95、Precision、Recall）
- confusion_matrix.png / confusion_matrix_normalized.png：验证集混淆矩阵
- F1_curve.png、PR_curve.png：F1 与 P-R 曲线
- R_curve.png、P_curve.png：召回率 / 精确率曲线
- val_batch*.jpg：验证集预测可视化图
- labels.jpg、labels_correlate.jpg：数据集分布与标签相关性

若某类图片未附上或无法辨识，请结合文末 args.yaml 与 results.csv 文本推断，并**明确标注不确定性**。

## 二、报告必须包含模块

1. **训练概况**

模型、epoch、batch、imgsz、优化器、精度模式、早停策略、数据增强概览。

2. **指标曲线分析**

- 收敛状态：是否收敛、震荡、过拟合 / 欠拟合
- 损失变化：box/cls/dfl loss 是否平稳下降
- 精度指标：mAP50、mAP50-95、Precision、Recall 是否达标与饱和

3. **数据集与预测质量分析**

- 标签分布是否均衡
- 混淆矩阵：漏检、错检、难分类类别
- P-R/F1 曲线：最优置信度阈值、小目标 / 遮挡目标表现
- 可视化图：定位不准、重复框、漏框、误报情况

4. **参数配置问题诊断**

- 学习率、优化器、权重衰减合理性
- 数据增强强度是否合适
- 早停、NMS、损失权重是否合理
- AMP、显存、多尺度等效率问题

5. **下一轮迭代优化方向（高→中→低优先级）**

- 学习率与优化器调整
- 数据增强增强 / 减弱
- 早停、epoch、batch、imgsz 调整
- NMS、conf、iou 阈值优化
- 损失函数权重微调
- 混合精度、效率加速

## 三、输出要求

- 语言：**中文**
- 风格：**工程化、可落地、无废话**
- 结构：分模块、分点、关键参数加粗
- 用途：**算法工程师直接用于下一轮训练迭代**
- **文末**必须单独给出 **一个** JSON 代码块（Markdown fenced，标记为 json），供程序解析；请尽量填写具体数值，仅在无法从上下文推断时使用 null。
- **街道垃圾 / 垃圾暴露（garbageExposure）**：JSON 须包含 `batch`、`patience`、`workers`、`amp`、`optimizer`、`cos_lr`、`lr0`、`lrf`、`weight_decay`、`dropout`、`mosaic`、`mixup`、`cutmix`、`degrees`、`flipud`、`fliplr`、`hsv_h`、`hsv_s`、`hsv_v`、`erasing`、`rect`、`multi_scale`、`box`、`cls`、`dfl`、`iou`、`conf`、`max_det` 等键；仅当 metrics 明确支持时才偏离下列 54.99 基线数值。
示例：
```json
"""
    + _SOP_RECOMMENDED_JSON
    + """
```

---
【best.pt 路径】{{WEIGHTS_PATH}}
【metrics_summary】{{METRICS_SUMMARY}}

---
【训练配置 args.yaml】
{{ARGS_YAML}}

---
【results.csv 末尾】
{{RESULTS_TAIL}}

---
【系统补充】上述占位符段落与多模态附图共同构成完整输入；若图文与文本摘要不一致，以 args.yaml 与 results.csv 为准并在报告中说明理由。
"""
)

DEFAULT_NEXT_ITERATION = (
    """# YOLO11 下一次迭代训练建议方案提示词

## 一、标准提示词

角色：你是一个专业的计算机视觉算法工程师。

任务：基于 YOLO11 **上一轮训练相关证据**（等价于训练报告 + 测试/验证指标摘要 + 数据与配置线索），针对**当前训练任务**（由下方配置、指标与数据路径反映），生成**下一次迭代训练参数分析报告**。若实际业务为**街道垃圾检测**等具体场景，请在「数据集现状」「核心问题」「优化方向」中结合类别分布、误检/漏检类型与样本难点展开，不必拘泥于通用表述。

系统中已提供的材料对应关系说明（无需假设本地存在 metrics.md / dataset_report.md 文件）：
- **metrics 类信息**：见文末「【metrics_summary】」；
- **训练与数据配置线索**：见「【args.yaml】」（含 data、imgsz、增强等）；
- **训练过程数值摘要**：见「【results.csv 末尾】」；
- **上一轮更完整的文字评估**：见「【上一轮全文评估摘要】」（若有）。

要求：
1. **明确上一轮迭代模型为自定义权重**：必须使用文末「【best.pt 路径】」给出的权重路径（当前作业实际上一轮 best.pt），**不得**随意改写为其它虚构路径。
2. **下一轮训练必须基于该权重继续微调**，不从随机初始化或无关官方权重重头训练（除非上下文明确证明上一轮权重不可用并在报告中说明理由）。
3. **规避 Ultralytics 8.x** 常见坑：命令行参数失效、强制重复下载模型、不当 resume 导致权重覆盖等；建议在报告中提醒采用可控方式。
4. **采用 Python API / Python 脚本方式** 描述或给出训练入口思路，保证稳定、可复现（可给出示例 train 调用要点或伪代码级 train.py 结构，无需完整依赖工程）。
5. **修正参数层面的明显错误或不一致**（如 multi_scale、amp、device、batch 与显存、增强与任务特性不匹配等），并在报告中写明修正逻辑。
6. **报告结构正式、工程化、可直接交付**给算法同事执行。

## 输出语言（必须遵守）

- 语言：**简体中文**（正文、标题、列表、诊断与建议均用中文）
- 允许保留英文：YAML/JSON 键名、指标缩写（mAP50、mAP50-95）、文件路径、模型文件名等
- **禁止**用英文撰写大段分析段落

输出结构（按顺序撰写）：
1. **上一轮迭代信息**（模型规格、权重路径、关键指标、训练轮次是否充分/早停等）
2. **数据集现状**（规模、类别、难点；可结合配置与指标推断）
3. **核心问题定位**（收敛、过拟合/欠拟合、增强、学习率、类别混淆等）
4. **下一轮优化方向**（分优先级，可表格）
5. **最终版 train 配置建议**：以 **YAML 片段** 形式给出「建议的 ultralytics 训练超参集合」（可与 args.yaml 对照增量修改说明）；不必与真实文件名绑定，但键名应与 ultralytics `YOLO.train()` 常用参数一致。
6. **稳定训练脚本要点**：给出 **train.py** 级别要点（导入、加载权重路径、model.train(...) 关键参数、device/workers 等），避免仅给不可靠 CLI 一行命令。
7. **预期效果**（可量化的改进期望与验证方式）
8. **文末单独一个 JSON 代码块**（Markdown fenced，标记为 json），键为 `recommended_train_params`，供本平台程序解析并回填表单。**请尽量填写具体数值**；无法推断时使用 null。正文表格/列表应与 JSON 含义一致。
- **街道垃圾 / 垃圾暴露**：须输出完整超参键（含 `workers`、`amp`、`multi_scale`、`cutmix`、`box`、`cls`、`dfl` 等）；首轮或指标未明显优于基线时，数值宜与下列 54.99 `train.yaml` 基线一致，再按 metrics 微调。
```json
"""
    + _SOP_RECOMMENDED_JSON_WITH_WEIGHTS
    + """
```
说明：JSON 中 **model_weights** 请填写「下一轮应继续微调所依据的权重」，应与【best.pt 路径】一致或为其规范相对/绝对路径字符串；**勿**用 `.env` 或服务器环境变量代替 `workers`/`amp`；**notes** 简述取舍。

---
【best.pt 路径】{{WEIGHTS_PATH}}
【metrics_summary】{{METRICS_SUMMARY}}

---
【args.yaml】
{{ARGS_YAML}}

---
【results.csv 末尾】
{{RESULTS_TAIL}}

---
【上一轮全文评估摘要（若有）】
{{PRIOR_REPORTS}}
"""
)


# 提交给视觉大模型时追加，避免 args.yaml / 图表英文导致整篇报告变英文
EVAL_OUTPUT_LANG_SUFFIX = """
---
【输出语言（必须遵守）】
- 报告正文、章节标题、列表说明、诊断与建议：**一律使用简体中文**。
- 允许保留：YAML/JSON 键名、指标缩写（mAP50、mAP50-95）、文件路径、模型文件名等业界通用英文/符号。
- **禁止**用英文撰写大段分析段落（除上述例外）。
"""

# v2 prompt: evidence-first and task-independent.  Keep the old templates
# above for backward compatibility with existing saved prompt configurations;
# new installations and an explicit reset use these defaults.
PROMPT_V2_FULL_ANALYSIS = """# YOLO 训练评估报告（证据优先版）

你是 YOLO 目标检测训练评估工程师。请只根据本次作业提供的结构化指标、args.yaml、results.csv、数据集统计和图表进行分析。报告服务于算法工程师下一轮训练决策，不写泛泛的教程。

## 证据规则（必须遵守）

1. `STRUCTURED_METRICS` 和 `metrics_summary` 是数值事实来源；训练配置以 `args.yaml` 为准；图表只能用于补充趋势，不能替代缺失数值。
2. 严格区分 train、val、test。只能比较同一指标在同一数据划分上的数值；没有指标就写“未提供”，不能估算。
3. 类别名称、类别顺序、样本数必须照输入原样引用。不得新增 background、负样本、混淆矩阵数值或类别。
4. 不得使用固定业务阈值、网上经验值或“通常应达到”的目标值评价本次作业，除非输入中明确提供了验收阈值；没有阈值时只描述相对表现和风险。
5. 每一个问题都必须给出对应证据（字段名、split、类别或图表文件名）。证据不足时明确写“证据不足，不能判断”。
6. 不要把相关性写成因果关系；把“观察到的现象”和“建议验证的假设”分开。

## 报告结构（按顺序输出）

1. **结论摘要**：最多 5 条，标出当前最重要的风险和是否建议直接迭代。
2. **作业与训练配置**：权重路径、实际 epoch、模型、imgsz、batch、device、optimizer、学习率、数据增强和早停；缺失项写未提供。
3. **数据集审计**：train/val/test 图片数、类别顺序、逐类实例数、类别不平衡、空标注/负样本证据；没有证据不下结论。
4. **总体指标**：分别列出 val 和 test 的 mAP50、mAP50-95、precision、recall，并说明两者差异；不要混用最后训练行和 test 评估结果。
5. **逐类指标**：表格列出每个类别的实例数、val/test 指标、最严重的缺口；指标缺失保留为 null/未提供。
6. **训练过程与错误模式**：只引用 results.csv 趋势、混淆矩阵或预测图中可确认的现象，区分过拟合、欠拟合、定位误差、类别混淆、漏检和误检。
7. **下一轮建议**：按高/中/低优先级列出不超过 6 项。每项必须包含“证据、建议改动、预期影响、风险、验证方法”，并且只建议当前训练框架实际支持的参数或数据动作。
8. **训练执行要点**：给出可复现的 Python API/train.py 级别要点；不要只给无法核对的 CLI 命令。
9. **最终参数 JSON**：全文最后只能有一个 `json` fenced code block，格式如下。只能填写有证据支持的参数，其余使用 null；参数名必须是平台支持的键。

```json
{
  "recommended_train_params": {
    "model_weights": null,
    "epochs": null,
    "imgsz": null,
    "batch": null,
    "patience": null,
    "workers": null,
    "amp": null,
    "optimizer": null,
    "cos_lr": null,
    "lr0": null,
    "lrf": null,
    "weight_decay": null,
    "mosaic": null,
    "mixup": null,
    "cutmix": null,
    "multi_scale": null,
    "box": null,
    "cls": null,
    "dfl": null,
    "iou": null,
    "conf": null,
    "max_det": null
  }
}
```

## 本次输入

---
【结构化指标 STRUCTURED_METRICS】
{{STRUCTURED_EVIDENCE}}

---
【权重路径 WEIGHTS_PATH】
{{WEIGHTS_PATH}}

---
【训练配置 ARGS_YAML】
{{ARGS_YAML}}

---
【results.csv 最近记录 RESULTS_TAIL】
{{RESULTS_TAIL}}

---
【metrics_summary 原始记录】
{{METRICS_SUMMARY}}
"""

PROMPT_V2_NEXT_ITERATION = """# YOLO 下一轮迭代训练建议（证据优先版）

你是负责 YOLO 训练迭代的算法工程师。请基于当前作业的真实指标和上一轮报告，设计一份可执行、可回滚的下一轮方案。当前权重必须作为微调起点，不得无证据改成随机初始化或其他权重。

## 硬性约束

1. `STRUCTURED_METRICS`、`metrics_summary` 和 `args.yaml` 是事实来源；上一轮报告只是待验证的假设，不能直接当作事实。
2. 严格区分 val/test，逐类核对；不得补造指标、样本数、混淆矩阵数字、类别或验收阈值。
3. 每个建议必须写明：当前证据、改动、预期影响、风险、验证指标和停止条件。
4. 不超过 5 个核心改动，优先处理证据最充分的问题；没有证据支持的参数保持不变。
5. `model_weights` 必须填写输入中的权重路径，或填写与其等价的 `trained:<job_id>` 标识；不得使用虚构路径。
6. 最终 JSON 只能出现平台支持的训练参数，不能把解释文字放进 JSON 数值字段。

## 输出结构

1. **上一轮结果复核**：指出上一轮报告哪些结论被当前证据支持、哪些不支持。
2. **迭代目标**：只列出可由当前数据验证的目标，不写无依据的绝对目标值。
3. **建议方案**：按优先级列出数据、标注、训练参数和评估阈值的改动；每项包含证据、动作、风险、验证和停止条件。
4. **执行顺序**：先做数据/标注修正，再做训练参数调整，最后说明对比实验方式。
5. **最终参数 JSON**：全文最后只能有一个 `json` fenced code block。

```json
{
  "recommended_train_params": {
    "model_weights": null,
    "epochs": null,
    "imgsz": null,
    "batch": null,
    "patience": null,
    "workers": null,
    "amp": null,
    "optimizer": null,
    "cos_lr": null,
    "lr0": null,
    "lrf": null,
    "weight_decay": null,
    "mosaic": null,
    "mixup": null,
    "cutmix": null,
    "multi_scale": null,
    "box": null,
    "cls": null,
    "dfl": null,
    "iou": null,
    "conf": null,
    "max_det": null
  }
}
```

---
【结构化指标 STRUCTURED_METRICS】
{{STRUCTURED_EVIDENCE}}

---
【权重路径 WEIGHTS_PATH】
{{WEIGHTS_PATH}}

---
【训练配置 ARGS_YAML】
{{ARGS_YAML}}

---
【results.csv 最近记录 RESULTS_TAIL】
{{RESULTS_TAIL}}

---
【上一轮报告 PRIOR_REPORTS】
{{PRIOR_REPORTS}}

---
【metrics_summary 原始记录】
{{METRICS_SUMMARY}}
"""


def prompts_file_path() -> Path:
    return Path(settings.BASE_DATA_DIR) / "yolo_eval_prompts.json"


def load_yolo_eval_prompts_legacy() -> dict[str, str]:
    p = prompts_file_path()
    if not p.is_file():
        return {
            "full_analysis": PROMPT_V2_FULL_ANALYSIS,
            "next_iteration": PROMPT_V2_NEXT_ITERATION,
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "full_analysis": PROMPT_V2_FULL_ANALYSIS,
            "next_iteration": PROMPT_V2_NEXT_ITERATION,
        }
    if not isinstance(raw, dict):
        return {
            "full_analysis": PROMPT_V2_FULL_ANALYSIS,
            "next_iteration": PROMPT_V2_NEXT_ITERATION,
        }
    full = raw.get("full_analysis")
    nxt = raw.get("next_iteration")
    return {
        "full_analysis": str(full) if full else PROMPT_V2_FULL_ANALYSIS,
        "next_iteration": str(nxt) if nxt else PROMPT_V2_NEXT_ITERATION,
    }


def _legacy_prompts_to_db_items(data: dict[str, str]) -> dict[str, str]:
    return {
        "full_analysis": str(data.get("full_analysis") or PROMPT_V2_FULL_ANALYSIS)[:100000],
        "next_iteration": str(data.get("next_iteration") or PROMPT_V2_NEXT_ITERATION)[:100000],
    }


def _write_legacy_yolo_eval_prompts(data: dict[str, str]) -> None:
    p = prompts_file_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_yolo_eval_prompts() -> dict[str, str]:
    legacy = load_yolo_eval_prompts_legacy()
    db_values = read_group_values("yolo_eval_prompts")
    return {
        "full_analysis": str(
            resolve_value(
                runtime_value=get_runtime_override("yolo_eval_prompts", "full_analysis"),
                db_value=db_values.get("full_analysis", legacy["full_analysis"]),
                default_value=legacy["full_analysis"],
            )
            or PROMPT_V2_FULL_ANALYSIS
        )[:100000],
        "next_iteration": str(
            resolve_value(
                runtime_value=get_runtime_override("yolo_eval_prompts", "next_iteration"),
                db_value=db_values.get("next_iteration", legacy["next_iteration"]),
                default_value=legacy["next_iteration"],
            )
            or PROMPT_V2_NEXT_ITERATION
        )[:100000],
    }


def save_yolo_eval_prompts(
    *,
    full_analysis: str | None = None,
    next_iteration: str | None = None,
) -> dict[str, str]:
    cur = load_yolo_eval_prompts()
    if full_analysis is not None:
        cur["full_analysis"] = str(full_analysis)[:100000]
    if next_iteration is not None:
        cur["next_iteration"] = str(next_iteration)[:100000]
    write_group_values(
        "yolo_eval_prompts",
        _legacy_prompts_to_db_items(cur),
        value_source="db",
        change_reason="save yolo eval prompts",
    )
    _write_legacy_yolo_eval_prompts(cur)
    return cur
