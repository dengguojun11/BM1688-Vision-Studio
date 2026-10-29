# RUN-0228 接入页面去项目化前端对齐

## 1. 目标

把数据接入页面与一期“无项目/业务空间、按当前账号授权范围工作”的后端契约对齐。接入作业、数据源、上传目标和媒体资产都不再由前端传入或展示项目范围；旧后端项目字段只保留历史兼容，不成为新页面的用户操作入口。

## 2. 角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品 | WP0 | 确认接入页面属于数据治理辅助入口，使用当前账号可见范围 |
| R03 架构 | WP1/WP2 | 复核 `needsProject=false` 与账号范围查询边界 |
| R04 后端 | WP2 | 核对接入作业、数据源、媒体资产接口不要求新请求携带项目参数 |
| R08 前端 | WP4 | 删除项目查询、项目参数和“当前项目”文案，统一居中弹窗 |
| R09 测试 | WP5 | 更新接入页面 Vitest，并增加静态去项目化门禁 |
| R10 运维 | OPS | 记录仅本地验证，生产旧部署未放行 |

## 3. 实现内容

- 接入作业列表、上传目标、数据源和媒体资产查询均使用当前账号授权范围；请求参数不再带 `project_id`。
- 新建接入作业请求不再带 `project_id`，仍保留幂等键和失败重试提示。
- 页面不再加载项目选择器或展示“当前项目”；资源页使用 `needsProject={false}`。
- 新建接入、上传媒体、取消作业等交互统一使用居中弹窗/确认框。
- 保留后端历史字段和兼容接口，不在本轮删除历史数据结构。

## 4. 本地验证

```text
pnpm exec vitest run src/pages/platform/pages/ingestion.test.tsx --reporter=dot
5 passed

python -m pytest labelu/tests/e2e/test_7gai_platform_ui_contract_static.py -q --disable-warnings --tb=short
8 passed, 113 warnings in 0.58s

pnpm --filter @labelu/frontend type-check
退出码 0

pnpm --filter @labelu/frontend build
退出码 0，4094 个模块转换，28.85 秒

仓库后端回归：

```text
python -m pytest labelu/tests -q --disable-warnings --tb=short
2630 passed, 56 skipped, 410 warnings in 332.95s
```
```

## 5. 生产边界

本轮只完成本地前端代码和测试对齐，达到 L1。尚未提交、推送或部署；生产仍需在授权发布后验证实际静态资源、接入作业、数据源和媒体资产接口，并确认旧入口不会重新注入项目参数。
