# RUN-0344 去掉数据集分类日常入口（2026-09-21，本地）

## 需求

方案的“标注与数据治理”只保留“标签库”和“数据集管理”。当前本地页面仍展示“数据集分类”页签，且新建数据集弹窗还可以选择分类，造成三套对象并列、主流程不一致。

## 处理结论

“数据集分类”是生产历史数据留下的兼容字段和筛选维度，不再作为一期业务对象维护：

- 本地系统管理页面只展示“标签库”“数据集管理”两个页签。
- 新建数据集只填写名称、描述和标签契约，不再选择分类。
- 数据集列表不再展示分类列，用户按数据集和标签契约理解数据来源。
- 数据库中的 `dataset_category_id`、旧接口和历史关联暂不删除，继续用于旧任务、旧训练作业、第三方回流和模型血缘对账；这些字段不提供本地新建、编辑、删除入口。

## 改动范围

- `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.tsx`
  - 删除分类查询、增改停用删除按钮、分类弹窗和分类页签。
  - 保留标签库英文系统值编辑、颜色自动分配和版本校验。
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/datasets.tsx`
  - 删除新建数据集的分类查询、分类选择字段和列表分类列。
  - 创建请求继续复用现有数据集和标签契约接口。
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.test.tsx`
  - 验证管理页面只有两个治理页签；验证新建数据集不出现分类字段。
- `docs/系统方案文档/7GAI进化平台菜单级产品与业务闭环方案.md`
  - 主流程、任务筛选、数据治理页签和字段说明统一为数据集直连标签契约。
  - 增加历史分类只读兼容说明，避免误删生产血缘。

## 本地验证

- `pnpm exec vitest run src/pages/platform/pages/admin.test.tsx src/pages/platform/pages/datasets.test.tsx`
- 结果：`2` 个测试文件、`16` 个测试全部通过。
- 运行中的 Ant Design/jsdom `window.computedStyle`、React 旧 API 警告为既有测试环境告警，不影响断言结果。
- 未连接生产、未修改生产数据库、未提交、未推送、未部署。

