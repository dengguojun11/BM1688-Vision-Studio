# Findings & Decisions

## Requirements
- 只做分析，不改业务代码。
- 必须使用并说明 `mcp__sequential_thinking`、`planning-with-files`、`code-review`、`mcp__context7`。
- 不能只讲原则，要给出能直接执行的垃圾暴露数据集质量标准。
- 需要基于项目真实预设、全局维度、训练链路和生产训练结果。

## Research Findings
- 生产预设 `garbageStreet` 的拉框类别为：`garbage_street_dump`、`garbage_street_pack`、`garbage_street_scattered`、`object_box`、`garbage_street_can`、`non_garbage`、`garbage_bulky`。
- 生产全局维度为：`sceneType`、`lighting`、`weather`、`quality`、`angle`、`source`。
- 任务 `43` 已挂载 `garbageStreet` 预设，`rectTool` 和 `tagTool` 都配置完整，`annotation_audit` 为全通过。
- 任务完整性全通过只说明“每张图的 tag/rect/geometry 形式上合格”，不说明“各类和各场景足够可训练”。
- `garbageStreet` 的训练类别从 `rectTool.attributes` 提取；`tagTool` 维度不会变成 YOLO 类别。
- `non_garbage` 在 `garbageStreet` 下会从训练类别中剔除，但可以作为背景负样本使用。
- 仓库已有 `consistent_pool` 机制，支持把多个任务里的样本收拢成“训练筛选一致池”，并保留 `source_task_id/source_sample_id` 来源。

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| 用“训练价值分桶 + 维度覆盖矩阵 + 拆分验收线”组织文档 | 这是最能回答“怎么补、如何补”的结构 |
| 将维度分为 `must-have / should-have / review-first` | 六维全笛卡尔积太大，必须压成优先级矩阵 |
| 为每张图定义“单一主桶”并给出优先级 | 用户明确要求“每张图都要知道属于哪个桶” |
| 把“90% 置信度”改写成“锁定测试集上的阈值选择规则” | `conf` 是推理过滤阈值，不是训练质量目标 |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| `planning-with-files` 本轮没有显式 MCP 工具 | 按技能要求使用项目内计划文件方式落地 |
| Context7 对“空标注图像”返回的官方说明不够直接 | 只引用其对数据集结构和 `conf` 作为推理阈值的官方说明，空标注部分以项目代码事实为主 |

## Resources
- `labelu/internal/application/service/annotation_taxonomy.py`
- `labelu/internal/application/service/task.py`
- `labelu/internal/application/service/yolo_dataset.py`
- `labelu/internal/common/task_annotation_completeness.py`
- `labelu/internal/application/service/yolo_quality_assessment.py`
- `labelu/internal/common/config.py`
- `labelu/internal/common/yolo_train_hyperparams.py`
- `labelu/internal/application/service/yolo_train_auto_iteration.py`
- `labelu/internal/application/service/yolo_train_consistent_pool.py`
- `labelu/scripts/sop_eval/evaluation_utils.py`

## Visual/Browser Findings
- 生产任务 `43` 的 `annotation_audit` 为 `1091/1091` 通过，但这并没有阻止同任务训练 `207/208` 在 `bulky` 上几乎没有有效支持。
- 训练作业 `207`：总体 `mAP50-95=0.5050`，但 `garbage_bulky` 的 `train/val/test` 实例数为 `4/1/0`，`val` 侧该类指标为 `0.0`。
- 训练作业 `208`：在与 `207` 相同数据分布下继续训练，整体反而降到 `mAP50-95=0.4876`，说明问题不在“多训几轮”。
- 合并训练作业 `198`：虽然合并了 11 个任务、总体样本大幅上升，但 `garbage_street_scattered` 的 `test mAP50-95` 仍只有 `0.3036`，`garbage_street_can` 为 `0.4582`，`garbage_street_dump` 为 `0.4347`，说明“任务多”不等于“结构对”。
