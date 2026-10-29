# RUN-0189 前端详情统一居中弹窗

**日期**：2026-09-20（Asia/Shanghai）  
**环境级别**：本地 L1；未提交、未推送、未部署  
**责任角色**：R01 产品、R08 前端/UI、R09 测试  
**关联工作包**：WP0 菜单主链、WP5 测试验收

## 1. 本轮目标

统一模型编译、训练和第三方调用等详情交互为居中弹窗。页面不再使用“抽屉”
文案或抽屉式样式选择器，窄屏仍通过弹窗内部滚动承载长详情。

## 2. 修改范围

- `frontend/extensions/pages/yolo_bm1688_exports_mockup.html`：移动端参考从底部抽屉改为居中弹窗。
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/workflows.tsx`：将历史兼容页注释统一为详情弹窗语义；不改变隐藏项目兼容页业务行为。
- `frontend/extensions/assets/labelu_toolkit_compact.css`：删除 `drawer-panel` 例外选择器，避免继续暗示抽屉布局。
- 新增 `frontend/extensions/tests/yolo_bm1688_modal_contract.test.mjs`，校验页面只保留中文居中弹窗契约。

## 3. 交互与性能边界

- 弹窗打开只读取当前详情，不在列表页重复加载全量数据。
- 长详情使用弹窗内部滚动，不改变列表布局，不新增轮询和设备请求。
- 关闭、重复打开和窄屏切换必须释放当前详情状态；不保留跨任务的旧详情。

## 4. 本地证据

```text
node frontend/extensions/tests/yolo_bm1688_modal_contract.test.mjs
```

结果：通过。前端既有 `pnpm type-check` 和 `pnpm build` 结果见 RUN-0183。

## 5. 生产边界

未连接生产、未修改生产页面、未提交、未推送、未部署。部署后仍需用标注人员和管理人员
账号分别打开模型中心、训练中心、数据回流详情，确认弹窗、权限和真实 API 数据一致。

