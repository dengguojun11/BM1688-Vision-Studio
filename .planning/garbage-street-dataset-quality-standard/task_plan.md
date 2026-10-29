# Task Plan: garbageStreet 数据集质量提升标准

## Goal
基于当前 `labelU` 项目的真实预设、全局维度、训练链路和生产训练结果，产出一份只针对 `garbageStreet` 的可落地数据集质量标准文档，回答“怎么补、补哪些、如何从现有任务重组”。

## Current Phase
Phase 4

## Phases

### Phase 1: Requirements & Discovery
- [x] 确认用户要求仅做分析，不改业务代码
- [x] 核实必须使用的技能与 MCP
- [x] 重新核对生产预设、全局维度、任务配置、训练结果
- **Status:** complete

### Phase 2: Code Path Review
- [x] 核实训练类别来源于 `rectTool`
- [x] 核实全局维度进入 `tagTool/tagList`
- [x] 核实 `training_ready` / 完整性检查的真实含义
- [x] 核实 `non_garbage` 背景负样本逻辑
- [x] 核实跨任务一致训练池 `consistent_pool` 能力
- **Status:** complete

### Phase 3: Standard Draft
- [x] 定义五个训练价值桶
- [x] 定义六个维度在垃圾暴露场景下的使用规则
- [x] 定义按类补齐、重标、剔除、重组的 SOP
- [x] 生成最终标准文档
- **Status:** complete

### Phase 4: Verification & Delivery
- [x] 复核文档是否与生产事实和代码链路一致
- [x] 向用户交付文档路径与核心结论
- **Status:** complete

## Key Questions
1. 当前项目中的全局维度到底是训练类别，还是数据治理维度？
2. 为什么“不同任务里都有样本”仍然训不出稳定模型？
3. 对 `dump / scattered / can / bulky` 应该如何定义补样、重标和验收标准？

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 只针对 `garbageStreet` 出标准 | 用户连续上下文都聚焦垃圾暴露，且生产训练证据已验证 |
| 用项目真实链路反推标准，而不写泛化论文式建议 | 用户明确拒绝“表面化”答案 |
| 把六个维度定义为治理矩阵，不定义为训练类别 | 代码明确训练类别来自 `rectTool`，维度来自 `tagTool` |
| 把“补”优先定义为重组现有任务，而不是先采新图 | 用户已明确表示数据分散在多个任务里，当前问题不是绝对没数据 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| PowerShell 不支持 `&&` | 1 | 改为 `;` 分隔命令 |
| 误以为本轮未提供 `planning-with-files` | 1 | 在本机技能目录中定位并读取 `planning-with-files/SKILL.md` |

## Notes
- 最终文档必须解释清楚：`training_ready` 不等于“训练覆盖完整”。
- 最终文档必须把“90% 置信度”问题讲成可执行验收规则，而不是一句“做不到”。
