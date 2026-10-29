# RUN-0336 本地独立工具页六菜单导航收口（2026-09-21，本地）

## 1. 需求与结论

独立工具页仍使用旧的“平台核心 / 数据准备 / 训练与评估 / 算能部署”导航，普通用户会看到已经取消的“工作流”和过时的设备部署分组。现已将共享顶栏统一为方案规定的六个一级菜单：

1. 工作台
2. 任务与标注
3. 训练中心
4. 模型中心
5. 进化中心
6. 系统管理

模型编译、模型列表和发布记录归入“模型中心”；“发布记录”表示外部平台发布和本平台下载控制，不表示设备下发。

## 2. 改动范围

| 责任角色 | 工作包 | 文件 | 影响 |
|---|---|---|---|
| R01 产品 | WP0 | `frontend/extensions/assets/labelu_portal.js` | 统一六菜单和中文菜单语义 |
| R08 前端/UI | WP4 | `frontend/labelU-Kit/apps/frontend/public/assets/labelu_portal.js` | 让本地 8002 实际加载的静态资源与源码一致 |
| R09 测试 | WP5 | `frontend/extensions/tests/labelu_portal_navigation.test.mjs` | 增加六菜单和退休入口禁用契约 |

## 3. 页面/API影响

- 只改变共享顶栏的展示和导航链接，不改变任务、训练、编译、发布或回流 API。
- 保留旧路由兼容跳转，但不在主导航暴露 `/workflows` 或“算能部署”。
- “边端验证设备”保留在系统管理，作为 BM1688 真机验证配置，不作为设备发布入口。
- “发布记录”保留在模型中心，仍用于 AICM 发布、本平台模型列表和下载控制。

## 4. 验收证据

- 导航专项 Node 测试通过：`node frontend/extensions/tests/labelu_portal_navigation.test.mjs`。
- `extensions/assets/labelu_portal.js` 与 `apps/frontend/public/assets/labelu_portal.js` SHA256 一致：
  `130D3B6CD22723BE9E147283433415C94281B128772C1FA970BD0B40399D0DC6`。
- 浏览器无缓存参数访问：`http://127.0.0.1:8002/yolo-bm1688-exports?local_nav_check=20260921`，已看到六组中文导航；页面仍显示 BM1688 编译列表和筛选按钮。
- 本地统一门禁结果为 `blocking: 0`；证据：`.tmp/evidence/local-nav-0336/local-gates-20260921T054228Z.json`。

## 5. 遗留与边界

- 已打开旧页面的浏览器标签可能缓存旧 JavaScript，需要刷新或使用新的资源版本；新打开页面加载新导航。
- 真实 BM1688 编译、真机验证、AICM 和第三方 S3 仍按各自真实环境门禁验收，不能由导航测试替代。
- 本轮未提交、未推送、未部署。
