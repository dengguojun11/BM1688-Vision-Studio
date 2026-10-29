# RUN-0321 退休旧设备发布页面与模型回滚代码清理（2026-09-21）

## 1. 本轮目标

按一期方案“无设备发布、无模型回滚、无项目/业务空间”清理不可达的旧前端部署页面和旧工作流页面。当前 `/deployments` 路由仍保留兼容跳转到模型中心，`/workflows` 路由仍保留兼容跳转到系统管理，但不应继续保留可被直接导入、维护或旧 bundle 复用的项目化写入页面实现。

本轮只修改本地前端源码和测试，不连接生产，不提交、不推送、不部署。

## 2. 代码路径核验

- `routes.tsx` 的 `/deployments` 已经是 `RetiredPlatformRoute`，目标为 `/models`，没有加载旧部署页面。
- `routes.tsx` 的 `/workflows` 已经是 `RetiredPlatformRoute`，目标为 `/admin`，没有加载旧工作流页面。
- `platformPageLoaders`、六个可见一级菜单和模型中心页面均没有导入这两份旧页面。
- 旧页面唯一引用来自自身测试和居中弹窗契约测试；部署页包含 `/v1/deployment-releases`、`project_id`、边端设备选择、设备发布状态流转和 `/rollback` 请求；工作流页包含 `/v1/workflows` 项目化创建、更新、启停和删除请求。
- 因此删除页面不会改变当前可见主流程，只会消除不可达的错误实现入口。进化中心仍保留对 `/v1/workflows` 的只读运行状态汇总，不属于旧工作流页面写入口。

## 3. 本轮交付

| 角色 | 工作包 | 交付 | 验收 |
|---|---|---|---|
| R01 产品 | WP0/WP4 | 固化一期不提供设备发布和模型回滚；历史链接仅兼容跳转模型中心 | 规则确认 |
| R03 架构 | WP4 | 核对当前路由与页面导入关系，确认删除不影响主路由 | 路由静态契约通过 |
| R08 前端/UI | WP4 | 删除 `pages/deployments.tsx`、`pages/workflows.tsx` 和过时测试；保留两个兼容重定向 | 构建通过 |
| R09 测试 | WP5 | 更新居中弹窗清单，验证六菜单、无项目页面和兼容重定向 | Vitest `7 passed`；全量 Vitest `246 passed`；后端静态契约 `10 passed` |
| R10 运维 | OPS | 保持生产版本不变，记录未部署边界 | 未连接生产 |

## 4. 验证结果

```text
pnpm run type-check
通过

pnpm exec vitest run src/pages/platform/modalCentering.test.ts src/pages/platform/modules.test.ts
7 passed

python -m pytest \
  labelu/tests/e2e/test_7gai_visible_menu_no_project_contract.py \
  labelu/tests/e2e/test_7gai_platform_ui_contract_static.py \
  -q --disable-warnings --tb=short
10 passed, 114 warnings

pnpm run build
通过，构建耗时 40.37s

pnpm exec vitest run
53 个测试文件，246 个测试全部通过
```

构建中的 Ant Design `use client` 和大分包提示是既有构建警告，退出码为 `0`；本轮没有新增 TypeScript 错误。

## 5. 影响和边界

- `/deployments` 历史地址仍可访问，但只会跳转到模型中心，不会展示设备发布按钮或模型回滚按钮。
- `/workflows` 历史地址仍可访问，但只会跳转到系统管理，不会展示项目化工作流创建、启停、更新或删除按钮。
- 本轮不删除规则中心的规则版本回退能力；规则配置回退与模型回滚不是同一业务动作，后续按规则中心是否继续保留再单独评估。
- 进化中心对 `/v1/workflows` 的读取只用于展示自动回流运行状态，不恢复工作流页面或项目维度。
- 后端历史部署实体和退休接口仍由既有失败关闭门禁保护，本轮不改变数据库和服务端兼容字段。

## 6. 交付状态

本轮达到本地前端 L1 验收。未连接生产、未执行生产写入、未提交、未推送、未部署。下一依赖是提交后预生产部署，复验 `/deployments` 重定向、模型中心页面和生产静态资源版本。
