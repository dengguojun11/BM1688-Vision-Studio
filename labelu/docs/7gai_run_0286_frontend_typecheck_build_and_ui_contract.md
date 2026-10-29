# RUN-0286 前端类型检查、生产构建与平台界面契约验收

日期：2026-09-20（Asia/Shanghai）  
责任角色：R03 架构、R08 前端/UI、R09 测试、R10 运维  
工作包：WP4/WP5

## 1. 本轮改动

`frontend/labelU-Kit/apps/frontend/src/pages/404.tsx` 中 `useTranslation()` 的返回类型触发 TypeScript `TS2589` 深层类型递归。参照项目已有页面的兼容边界，增加显式 `TranslationHook` 类型和 `useTranslationCompat` 别名；只限制类型推导范围，不改变运行时行为、路由、文案或页面布局。

## 2. 验证命令与结果

| 验证项 | 命令/范围 | 结果 |
|---|---|---|
| 前端类型检查 | `pnpm run type-check`（`frontend/labelU-Kit/apps/frontend`） | 通过，退出码 `0` |
| 前端生产构建 | `pnpm run build`（同目录） | 通过，`✓ built in 1m 59s`，退出码 `0` |
| 一期平台/UI静态契约 | `python -m pytest labelu/tests/e2e/test_7gai_platform_ui_contract_static.py labelu/tests/e2e/test_7gai_visible_menu_no_project_contract.py -q` | `10 passed` |
| 扩展页面契约 | `node --test frontend/extensions/tests/7gai_platform_entrypoints.test.mjs frontend/extensions/tests/ai_labeling_settings_modal.test.mjs frontend/extensions/tests/labelu_portal_navigation.test.mjs frontend/extensions/tests/yolo_training_create_wizard.test.mjs frontend/extensions/tests/yolo_model_catalog_confirm_modal.test.mjs` | `5 passed` |
| Vitest | `pnpm exec vitest run --config vitest.config.ts` | 未执行成功：当前依赖环境未提供 `vitest` 可执行文件；不计入通过，不影响上述类型、构建和 Node/Pytest 契约结果 |

## 3. 构建提示边界

- Vite 输出的是依赖包既有的 `use client` 忽略提示、项目既有 `eval` 风险提示和大体积分包提示，均未形成构建失败。
- 本轮未修改第三方依赖、未改变分包策略，也未把警告标记为已解决。

## 4. 真实环境边界

- 以上为本地工作树验收，不证明生产已部署。
- 未连接生产、MySQL、第三方 S3、AICM、TPU-MLIR 或真实 BM1688；未执行迁移、提交、推送或部署。
- 生产两类账号、窄屏/桌面真实浏览器验收和线上 OpenAPI 仍需在获得发布授权及真实环境后执行。

## 5. 状态

本地 L1；未提交、未推送、未部署。下一依赖仍为 `WP3-HIL-01` 真机三精度编译/推理证据，以及同版本 MySQL 并发演练和生产只读复核。

