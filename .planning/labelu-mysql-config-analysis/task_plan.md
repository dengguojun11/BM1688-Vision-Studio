# LabelU MySQL and Config Analysis

## Goal
基于当前仓库真实代码，盘点 LabelU 的业务数据与配置数据，分析哪些适合落 MySQL、哪些应保留在环境层，并形成“字段都有中文解释”的落地方案。

## Phases
| Phase | Status | Notes |
|---|---|---|
| 1. Gather tool and repo context | complete | 已实际调用 sequential-thinking、context7，并读取 planning-with-files 与 code-review 指引 |
| 2. Inventory business data and config data | complete | 已核对 domain models、alembic、config.py、common JSON/file stores、router/page 入口 |
| 3. Classify config persistence strategy | complete | 已固化 7 组文件配置的 DB 主存 / DB overlay / env-only 边界 |
| 4. Design MySQL landing and data dictionary approach | complete | 已固化配置表、历史表、数据字典表、COMMENT 与字典双轨方案 |
| 5. Assess code impact and rollout plan | complete | 已固化迁移批次、验证与回滚方式、精确文件影响范围 |

## Constraints
- 当前阶段只做分析，不改业务代码。
- 结论必须基于当前仓库真实实现。
- 输出必须覆盖用户要求的六大章节。

## Open Questions
- “可入库配置”的最终业务边界，需要先按当前代码形态给出建议，再由用户确认。
