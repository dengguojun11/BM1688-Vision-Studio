# RUN-0337 本地模型中心文案与入口对齐（2026-09-21，本地）

## 1. 问题

模型发布页在发布环境未配置时仍提示旧菜单“辅助入口 / 配置”，与六菜单导航不一致，用户无法按当前菜单定位配置入口。

## 2. 改动

| 责任角色 | 工作包 | 文件 | 改动 |
|---|---|---|---|
| R01 产品 | WP0 | `frontend/extensions/pages/yolo_model_publish.html` | 将提示改为“系统管理 → 外部发布环境” |
| R08 前端/UI | WP4 | `frontend/extensions/assets/labelu_portal.js`、`frontend/labelU-Kit/apps/frontend/public/assets/labelu_portal.js` | 将训练页辅助入口改为“其他设置”，说明按六菜单推进 |
| R09 测试 | WP5 | `frontend/extensions/tests/labelu_portal_navigation.test.mjs` | 增加旧文案禁用和新文案契约 |

## 3. 验收

- 导航与文案专项测试通过：`node frontend/extensions/tests/labelu_portal_navigation.test.mjs`。
- 浏览器页面：`http://127.0.0.1:8002/yolo-model-publish?local_publish_check=20260921`。
- 页面实际显示：`当前还没有配置发布环境……请先到「系统管理 → 外部发布环境」维护。`
- 两份共享脚本 SHA256 一致：`00C9323CC6B20D51376533E5731A56844C1CC43586AA6C38D3B652503BFD28D2`。

## 4. 边界

- 发布记录仍属于模型中心，表示 AICM 发布和本平台下载目录，不表示设备下发。
- 本轮没有改变发布接口、门禁规则或数据库；未连接生产、未提交、未推送、未部署。
