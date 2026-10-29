# RUN-0201 居中弹窗 Esc 关闭与真实浏览器回归

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R08 前端/UI、R09 测试；R01 产品协作。
- 工作包：WP0 菜单交互、WP5 浏览器验收。
- 环境级别：本地 L1；使用隔离 Vite 前端 `127.0.0.1:3004`，接口全部由 Playwright 内存路由模拟；未连接生产后端或生产数据库。

## 发现与修改

真实浏览器首次回归发现：模型详情弹窗内包含两个默认关闭的子弹窗，通用 `get_by_role("dialog")` 会误选；模型详情、数据回流审核和设备验签详情的 Esc 关闭在当前页面焦点状态下没有真正关闭。

本轮处理：

- 模型详情、数据回流审核、设备验签详情显式启用 `keyboard`，并在打开状态注册捕获阶段的 Esc 关闭监听；模型登记子弹窗打开时不关闭外层详情。
- 浏览器测试改用可见居中弹窗作用域，发布按钮使用精确名称，数据回流断言与实际产品标题“审核难例”一致。
- 未改变后端接口、数据库、发布门禁或模型文件。

## 修改文件

- `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/models.tsx`
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/evolution.tsx`
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/DeviceSignatureEvidence.tsx`
- `labelu/tests/e2e/test_7gai_platform_shell_frontend.py`

## 本地验证

- `LABELU_PLATFORM_URL=http://127.0.0.1:3004 python -m pytest labelu/tests/e2e/test_7gai_platform_shell_frontend.py -q --disable-warnings --tb=short`：`9 passed`。
- 覆盖管理人员/标注人员菜单、模型详情桌面与窄屏、模型发布按钮、数据回流居中弹窗、旧路径跳转和无项目参数。
- `pnpm --filter @labelu/frontend type-check`：通过。
- 隔离 Vite 服务启动、回归完成后已停止；`git diff --check` 未见差异错误。

## 放行边界

- 本地浏览器达到 L1；未执行预生产/生产浏览器、真实账号、AICM、S3、BM1688 或 MySQL 并发验证。
- 未提交、未推送、未部署；等待用户明确“提交代码”并完成生产放行证据后再进入交付流程。
