# labelU Skill Index

## 1. 使用方式

当你在 `labelU` 项目里遇到问题时，先不要直接按文件夹找代码，建议先按“问题属于哪个业务闭环”来选 skill。

推荐判断顺序：

1. 先判断问题属于哪个业务阶段
2. 再进入对应 skill
3. 如果问题跨多个阶段，先用通用 skill 做影响面分析
4. 如果问题涉及文档整理，优先走 docs routing

---

## 2. 通用层入口

### `01-code-impact`

适合：

- 分析代码改动会影响哪些模块
- 做需求实现前影响面预判
- 做 PR / review 前的风险扫描

入口文件：

- [skills/common/01-code-impact/SKILL.md](D:/PycharmProjects/labelU/skills/common/01-code-impact/SKILL.md)

### `02-docs-consolidation`

适合：

- 文档去重
- 文档归纳
- 主文档 / 支撑文档 / 归档文档判断

入口文件：

- [skills/common/02-docs-consolidation/SKILL.md](D:/PycharmProjects/labelU/skills/common/02-docs-consolidation/SKILL.md)

---

## 3. labelU 项目层入口

### `01-task-annotation-analysis`

适合：

- 任务、样本、附件、预标注问题
- 标注协作、任务权限问题
- AI 标注、标签体系、标签预设问题

入口文件：

- [01-task-annotation-analysis/SKILL.md](D:/PycharmProjects/labelU/skills/projects/labelU/01-task-annotation-analysis/SKILL.md)

### `02-yolo-train-diagnosis`

适合：

- 训练任务起不来、卡住、自动恢复异常
- 训练页显示异常
- 评估、GT/Pred、训练结果链路问题

入口文件：

- [02-yolo-train-diagnosis/SKILL.md](D:/PycharmProjects/labelU/skills/projects/labelU/02-yolo-train-diagnosis/SKILL.md)

### `03-bm1688-edge-validation`

适合：

- ONNX / bmodel 导出问题
- BM1688 自动恢复问题
- 板端验证、SSH 边端验证、阈值接入问题

入口文件：

- [03-bm1688-edge-validation/SKILL.md](D:/PycharmProjects/labelU/skills/projects/labelU/03-bm1688-edge-validation/SKILL.md)

### `04-quality-policy-analysis`

适合：

- 质量标准
- 达标判定
- 推荐规则来源
- 发布前质量门禁解释不一致

入口文件：

- [04-quality-policy-analysis/SKILL.md](D:/PycharmProjects/labelU/skills/projects/labelU/04-quality-policy-analysis/SKILL.md)

### `05-release-delivery-check`

适合：

- 模型工作流状态不一致
- 发布就绪度异常
- 发布页 / 交付页 / workflow 页状态不一致

入口文件：

- [05-release-delivery-check/SKILL.md](D:/PycharmProjects/labelU/skills/projects/labelU/05-release-delivery-check/SKILL.md)

### `06-docs-routing-check`

适合：

- 新文档该放到哪个模块
- 老文档该保留、合并、归档还是候选删除
- 文档内容是否已经被主文档吸收

入口文件：

- [06-docs-routing-check/SKILL.md](D:/PycharmProjects/labelU/skills/projects/labelU/06-docs-routing-check/SKILL.md)

---

## 4. 按问题类型找 Skill

### 如果你遇到的是“代码改了会影响哪里”

先用：

- `01-code-impact`

如果是 `labelU` 训练 / 验证 / 发布链路里的改动，再进入对应项目 skill。

### 如果你遇到的是“某个功能为什么不对”

按业务闭环选：

- 任务/标注：`01-task-annotation-analysis`
- 训练：`02-yolo-train-diagnosis`
- BM1688/边端验证：`03-bm1688-edge-validation`
- 质量标准：`04-quality-policy-analysis`
- 发布交付：`05-release-delivery-check`

### 如果你遇到的是“文档该怎么整理”

先用：

- `02-docs-consolidation`

在 `labelU` 里落位时，再用：

- `06-docs-routing-check`

---

## 5. 推荐组合

### 改代码前

- `01-code-impact`

### 改完训练链路后

- `01-code-impact`
- `02-yolo-train-diagnosis`

### 改完 BM1688 / 边端验证链路后

- `01-code-impact`
- `03-bm1688-edge-validation`

### 整理文档时

- `02-docs-consolidation`
- `06-docs-routing-check`

### 排查发布状态异常时

- `04-quality-policy-analysis`
- `05-release-delivery-check`

---

## 6. 项目地图入口

如果你先想看项目地图，而不是直接进 skill，看这里：

- [PROJECT.md](D:/PycharmProjects/labelU/skills/projects/labelU/PROJECT.md)
- [maps/code-map.md](D:/PycharmProjects/labelU/skills/projects/labelU/maps/code-map.md)
- [maps/docs-map.md](D:/PycharmProjects/labelU/skills/projects/labelU/maps/docs-map.md)
- [maps/business-flow.md](D:/PycharmProjects/labelU/skills/projects/labelU/maps/business-flow.md)
