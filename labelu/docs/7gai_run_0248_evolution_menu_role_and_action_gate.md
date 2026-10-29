# RUN-0248 进化中心菜单权限与写操作互斥

- 日期：2026-09-20（Asia/Shanghai）
- 责任角色：R01 产品、R03 架构、R08 前端/UI、R09 测试、R10 运维
- 工作包：WP0（产品与权限）、WP4（回流与外部平台）、WP5（测试与生产放行）
- 涉及菜单：进化中心；兼容路由 `/annotation-review`
- 验收等级：本地 L1；未提交、未推送、未部署、未连接生产

## 1. 本轮结论

一期第五个一级菜单固定为“进化中心”，“数据回流”只表示其业务来源，不再作为页面、导航或面包屑名称。标注人员可以进入进化中心查看本人有权访问的问题样本和难例详情，但不能提交全局审核，也不能进入回流编排；管理人员可以审核难例并执行聚类、批次、接受和冻结操作。

进化中心的难例详情继续复用居中弹窗，没有新增抽屉。页面不查询项目列表、不显示项目选择器、不发送 `project_id`。

## 2. 真实缺口与修复

| 项目 | 修改前 | 修改后 |
|---|---|---|
| 一级菜单名称 | 主导航、页头和面包屑显示“数据回流” | 统一显示“进化中心” |
| 标注人员入口 | 进化中心入口被整体隐藏 | 显示进化中心，可查看本人权限范围内的难例 |
| 标注人员按钮 | 直接打开地址时能看到管理端“回流编排”页签 | 只显示“难例审核”页签；详情只读，不显示“提交审核” |
| 管理人员按钮 | 可审核和编排 | 保持可用；管理端接口权限继续由后端校验 |
| 并发重复点击 | 重建、生成批次、接受批次、冻结版本只各自显示忙碌态，其他写操作仍可同时触发 | 任一编排写操作进行时，统一禁用阈值、配额、聚类选择和全部写按钮 |
| 弹窗 | 难例详情为居中弹窗 | 保持居中弹窗和 `Esc` 关闭，不引入抽屉 |
| 项目维度 | 当前页面已去项目化 | 保持无项目入口、无项目参数 |

## 3. 菜单与按钮权限

| 页面/按钮 | 标注人员 | 管理人员 | 忙碌态 |
|---|---|---|---|
| 进化中心一级菜单 | 显示 | 显示 | 不适用 |
| 查看难例 | 按后端任务/创建者权限只读查看 | 全局查看 | 详情加载显示加载状态 |
| 提交难例审核 | 不显示 | 可用 | 提交中弹窗确认按钮加载并禁用 |
| 回流编排页签 | 不显示 | 可用 | 不适用 |
| 重建聚类 | 不显示 | 可用 | 与其他编排写操作互斥 |
| 生成训练批次 | 不显示 | 有可用聚类时可用 | 与其他编排写操作互斥 |
| 接受批次并创建工作流 | 不显示 | 批次待处理时可用 | 与其他编排写操作互斥 |
| 校验并冻结 | 不显示 | 数据版本待评审时可用 | 与其他编排写操作互斥 |
| 系统管理一级菜单 | 不显示 | 显示 | 不适用 |

前端禁用只负责交互和减少重复请求，不能替代后端权限、幂等键、状态条件更新和事务检查。后端拒绝或状态冲突仍按失败处理，不把前端忙碌态视为并发真源。

## 4. 改动文件

| 文件 | 变更 |
|---|---|
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/modules.ts` | 第五菜单及页面标题统一为“进化中心” |
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/layout.tsx` | 标注人员显示进化中心，仍隐藏系统管理 |
| `frontend/labelU-Kit/apps/frontend/src/routes.tsx` | 进化中心面包屑和历史审核兼容提示中文统一 |
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/evolution.tsx` | 两类用户页签权限；管理端编排写操作统一忙碌态 |
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/modules.test.ts` | 六菜单名称契约 |
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/layout.test.tsx` | 标注人员五个工作入口和系统管理隐藏契约 |
| `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/evolution.test.tsx` | 只读权限、管理页签和重复点击互斥测试 |
| `labelu/tests/e2e/test_7gai_platform_shell_frontend.py` | 两类用户真实浏览器导航契约 |

本轮未修改数据库、迁移、后端 API、S3、AICM、BM1688、systemd 或生产配置。

## 5. 本地验证证据

```text
cd frontend/labelU-Kit/apps/frontend
pnpm exec vitest run src/pages/platform/modules.test.ts src/pages/platform/layout.test.tsx src/pages/platform/pages/evolution.test.tsx
3 files passed, 13 tests passed

cd frontend/labelU-Kit
pnpm --filter @labelu/frontend run type-check
退出码 0

pnpm --filter @labelu/frontend run build
4094 modules transformed
built in 1m 18s
退出码 0

python -m pytest labelu/tests/e2e/test_7gai_visible_menu_no_project_contract.py -q --disable-warnings --tb=short
2 passed, 113 warnings

$env:LABELU_PLATFORM_URL='http://127.0.0.1:3014'
python -m pytest labelu/tests/e2e/test_7gai_platform_shell_frontend.py -k "manager_sees_the_six_primary_modules_without_project_scope or labeling_user_sees_read_only_evolution_but_not_system_management or evolution_detail_is_a_centered_modal_without_project_query" -q --disable-warnings --tb=short
3 passed, 6 deselected, 113 warnings

git diff --check -- <本轮文件>
退出码 0
```

浏览器用例使用本地 Vite 页面和 Playwright 路由内存响应，不连接生产 API。它证明：管理人员导航为六项；标注人员可见进化中心且不可见系统管理；难例详情是居中弹窗；没有 `.ant-drawer`；页面地址不含 `project_id`。测试中的对象编号 `14`、模型编号 `12` 均为内存夹具，不是生产对象。

Vitest 输出包含 React 18 旧渲染 API 和 `jsdom` 未实现伪元素样式的既有警告，但三组测试退出码为 `0`。构建包含既有大分块警告，不影响本轮构建成功，后续仍需单独处理前端包体性能。

## 6. 生产与并发边界

- 本轮未使用生产账号、生产 API、真实第三方 S3 包或生产数据库；没有生产请求编号、任务 ID、回流批次 ID 和截图证据。
- 没有执行生产多浏览器、多进程或网络抖动下的重复点击；后端主动学习批次幂等、事务条件和 MySQL 并发仍以对应后端工作包证据为准。
- 未验证生产反向代理是否已提供当前静态资源；根据 RUN-0241，生产仍为旧部署。
- 未提交、未推送、未部署。收到用户明确“提交代码”前保持本地版本，不进入生产验收。

## 7. 后续验收

提交并部署后，R09/R10 必须使用标注人员和管理人员两个真实账号复验：菜单数量、本人难例可见范围、管理页签隔离、居中弹窗、无项目参数、快速重复点击只发出一次有效写请求，以及后端在重复请求或状态变化时返回稳定结果。未完成这些证据前，本轮只能判定本地 L1。
