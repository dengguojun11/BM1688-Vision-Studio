# labelU Business Flow Map

## 1. Main Closure

`任务与标注 -> 训练与评估 -> 质量标准 -> 边端验证与BM1688 -> 发布与交付 -> 运维与协作`

## 2. Module Meaning

### 任务与标注

- task management
- sample and attachment handling
- manual labeling
- AI labeling
- taxonomy and labeling preset support

### 训练与评估

- training job creation and scheduling
- training reports
- GT/Pred comparison
- model import or model asset management

### 质量标准

- scenario policy
- pass/fail thresholds
- rule source
- evaluation gate logic

### 边端验证与BM1688

- ONNX export
- BM1688 compile/export
- board-side or edge-side validation
- threshold strategy access

### 发布与交付

- model readiness
- release gate
- delivered model workflow view
- publish-related integration

### 运维与协作

- local development
- deployment
- environment checks
- logs and troubleshooting
- development collaboration

## 3. Skill Adaptation Hint

When a common skill is used in `labelU`, always try to map findings onto one or more of these business-flow stages.

That makes the output easier to route into:

- docs modules
- project reviews
- release discussions
- implementation planning
