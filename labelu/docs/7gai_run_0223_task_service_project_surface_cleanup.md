# RUN-0223 任务服务项目操作面清理

## 1. 目标

一期平台不提供项目/业务空间。上一轮已移除任务页的项目查询、项目批量赋值和可见控件，但前端任务服务仍导出一个没有调用方的 `assignTasksToProject` 历史函数。本轮清理该死代码，避免后续页面或新功能误复用项目写入口。

## 2. 角色与交付

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品 | WP0 | 明确一期前端服务层不再提供项目赋值能力；历史项目数据和后端兼容接口不等同于一期功能 |
| R03 架构 | WP2 | 保持后端历史字段/接口兼容，不做破坏性删除；前端只撤销无调用方的项目写封装 |
| R04 后端 | WP1 | 无生产后端改动；确认旧接口未被前端服务层继续暴露 |
| R08 前端/UI | WP1/WP4 | 删除 `frontend/labelU-Kit/apps/frontend/src/api/services/task.ts` 中的 `assignTasksToProject`；修复 TaskTip 类型深度错误 |
| R09 测试 | WP5 | 增加任务服务不得导出项目赋值接口的静态契约 |
| R10 运维 | OPS | 记录本轮不涉及数据库、S3、AICM、TPU-MLIR、BM1688 或生产写操作 |

## 3. 改动范围

- 删除前端任务服务的 `assignTasksToProject` 函数及 `/v1/tasks/project-assignment` 调用封装。
- 保留后端历史 `/v1/tasks/project-assignment` 和 `project_id` 字段，供历史数据读取/兼容迁移使用；不允许把它们重新挂到一期菜单。
- 静态契约同时检查任务页和任务服务，防止项目赋值入口回归。
- `frontend/labelU-Kit/apps/frontend/src/components/Navigate/TaskTip/index.tsx` 对路由加载结果和翻译钩子使用显式兼容类型，避免 React Router/i18n 泛型递归展开；不改变页面行为。

## 4. 验证

```text
python -m pytest labelu/tests/e2e/test_7gai_platform_ui_contract_static.py -q --disable-warnings --tb=short
7 passed, 113 warnings

git diff --check
通过（仅有工作树既有 CRLF 提示，无空白错误）

pnpm --filter @labelu/frontend type-check
通过

pnpm --filter @labelu/frontend build
退出码 0；4094 个模块转换；built in 28.39s
```

前端类型检查最初在 `TaskTip` 出现 `TS2589: Type instantiation is excessively deep and possibly infinite`；本轮已用显式兼容类型修复并复验通过。该修复只收窄类型推断，不改变页面运行行为。

## 5. 验收等级与生产边界

本轮达到本地 **L1**：代码与静态门禁完成，专项测试通过；未提交、未推送、未部署，未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688。后端历史接口是否需要最终下线，必须在生产数据盘点和兼容窗口确认后另行决策，不在本轮删除。
