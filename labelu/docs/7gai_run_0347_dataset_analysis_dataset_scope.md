# RUN-0347 数据集分析改为按数据集范围（2026-09-21，本地）

## 背景

一期方案的日常治理只保留“标签库”和“数据集管理”。旧“数据集分析”页面仍按“数据集分类”选择和查询，容易让用户误以为还需要维护第三个对象。

## 处理结论

- 数据集分析的新入口按逻辑数据集选择，标签汇总和图片样本预览都使用 `dataset_id`。
- 后端 `/v1/tasks/dataset-analysis`、标签汇总和标签图片接口新增数据集范围参数。
- `dataset_category_id`、分类响应字段和旧调用路径保留为只读兼容，不再由一期页面生成或提交。
- 数据集分析遵守现有任务可见范围；非数据集所有者只有在至少拥有一个可见协作任务时才能查询，避免通过数据集编号越权读取。

## 改动范围

- `labelu/internal/application/service/dataset_analysis.py`
- `labelu/internal/application/service/task.py`
- `labelu/internal/adapter/routers/task.py`
- `labelu/internal/application/response/dataset_analysis.py`
- `frontend/labelU-Kit/apps/frontend/src/api/services/datasetAnalysis.ts`
- `frontend/labelU-Kit/apps/frontend/src/api/services/task.ts`
- `frontend/labelU-Kit/apps/frontend/src/loaders/task.loader.ts`
- `frontend/labelU-Kit/apps/frontend/src/pages/training-analysis/index.tsx`

## 验证

- `python -m compileall -q`：通过。
- `python -m pytest labelu/tests/internal/application/service/test_dataset_analysis.py -q`：2 passed。
- `pnpm run type-check`：通过。
- `pnpm exec vitest run src/pages/platform/pages/training.test.tsx src/pages/platform/pages/admin.test.tsx`：37 passed。
- 本轮未连接生产、未修改生产数据库、未提交、未推送、未部署。

## 遗留与下一依赖

- 历史分类字段、旧训练接口、第三方回流和模型血缘仍继续读取分类快照；后续只能在确认历史数据迁移完成后再评估下线。
- 本地浏览器需要重启或刷新 8003 前端进程后验证“数据集分析”选择器的实际渲染；提交代码前不得进入生产发布流程。
