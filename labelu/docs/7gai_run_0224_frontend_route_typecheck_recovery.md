# RUN-0224 前端路由类型检查恢复

## 1. 问题

前端任务服务项目操作面清理后执行类型检查，`Navigate/TaskTip` 在调用 `useRouteLoaderData('task')` 时触发 TypeScript `TS2589: Type instantiation is excessively deep and possibly infinite`。页面运行逻辑没有异常，但类型门禁无法通过。

## 2. 角色与交付

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R03 架构 | WP2 | 确认问题来自 React Router 路由映射泛型展开，不是任务服务接口变化 |
| R08 前端/UI | WP4 | 在 `TaskTip` 内增加本地 `(routeId: string) => unknown` 类型边界，再转换为 `TaskLoaderResult`；不改变运行时调用和页面展示 |
| R09 测试 | WP5 | 重新执行前端 TypeScript 类型门禁 |
| R10 运维 | OPS | 确认本轮仅本地前端编译，不涉及生产部署 |

## 3. 验证

```text
pnpm --filter @labelu/frontend type-check
退出码 0

pnpm --filter @labelu/frontend build
退出码 0；4094 个模块转换；built in 28.87s
```

构建仍输出 Ant Design `use client`、`eval` 和大包体提示，属于现有依赖/代码警告，不影响本轮退出码。

## 4. 验收等级与边界

本轮达到本地 **L1**：前端类型检查和生产构建均通过；未提交、未推送、未部署，未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688。

