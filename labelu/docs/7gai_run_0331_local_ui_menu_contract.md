# RUN-0331 本地平台 UI、菜单和去项目化契约验收（2026-09-21）

## 1. 本轮目标

回归一期平台前端静态契约，确认六个一级模块、中文页面、居中弹窗、两类用户文案、无项目/业务空间入口、退休路由和外部验收门禁没有被近期改动破坏。

## 2. 责任角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品 | WP4/WP5 | 六个一级模块和角色菜单边界 |
| R03 架构 | WP0/WP4 | 去项目化路由兼容和退休路径静态约束 |
| R08 前端/UI | WP4 | 中文文案、居中弹窗、模型/训练操作入口 |
| R09 测试 | WP5 | UI 契约、可见菜单、Shell 和外部门禁模板回归 |
| R10 运维 | OPS | 确认外部地址/硬件缺失项保持跳过或未开始，不误报通过 |

## 3. 验收命令与结果

```text
python -m pytest \
  labelu/tests/e2e/test_7gai_platform_ui_contract_static.py \
  labelu/tests/e2e/test_7gai_visible_menu_no_project_contract.py \
  labelu/tests/e2e/test_7gai_platform_shell_frontend.py \
  scripts/tests/test_local_gate_inventory.py \
  -q --disable-warnings --tb=short
```

结果：

```text
13 passed, 9 skipped, 114 warnings in 0.82s
```

## 4. 已验证行为

- 一级导航仅保留工作台、任务与标注、训练、模型、进化中心、管理六个模块。
- 页面不暴露项目/业务空间、设备发布、柔性工作流和 Provider 注册等退休控制面。
- 关键弹窗使用居中 Modal，不使用旧抽屉结构。
- 管理人员、标注人员、发布模型、下载模型、新建训练作业、开始训练等中文角色/操作文案存在。
- 兼容旧地址时会移除 `project_id`，并跳转到新的平台菜单。
- 外部浏览器、axe、GPU HIL 和 Linux 发布门禁在缺少外部前置时保持跳过/未开始，不会伪造通过。

## 5. 风险与下一依赖

- 静态契约不替代三视口真实浏览器、真实双账号和生产反向代理验收。
- 外部 Playwright、axe、真实 GPU、BM1688、MySQL 和生产部署仍需在对应环境完成。

## 6. 提交与部署状态

本轮未提交、未推送、未部署、未重启生产。继续等待用户明确说“提交代码”。
