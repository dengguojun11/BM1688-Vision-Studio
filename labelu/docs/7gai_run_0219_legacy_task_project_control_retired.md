# RUN-0219 任务页历史项目操作下线

## 1. 需求与问题

一期不提供项目/业务空间。核验发现开源 LabelU 主任务页 `/tasks` 仍保留批量“归属项目”控件，并在用户选择任务后请求 `/v1/projects`，可调用 `/v1/tasks/project-assignment`。这与一期菜单和数据权限口径冲突。

## 2. 角色与工作包

| 角色 | 工作包 | 交付 |
|---|---|---|
| R01 产品 | WP0 | 明确任务列表只能按创建者、协作者、标签和数据集分类使用，不显示项目归属 |
| R03 架构 | WP2 | 保留后端历史 `project_id` 字段和兼容接口，不把历史兼容等同于一期功能 |
| R04 后端 | WP1 | 本轮不删除后端接口，避免影响历史数据迁移；禁止通过前端继续触发旧项目写链 |
| R08 前端/UI | WP1/WP4 | 移除任务页项目查询、项目批量赋值函数和“归属项目”选择器 |
| R09 测试 | WP5 | 新增任务页静态契约，防止项目查询和项目赋值回归 |
| R10 运维 | OPS | 生产部署后需重做 `/tasks` 页面只读浏览器核验 |

## 3. 改动文件与影响

- `frontend/labelU-Kit/apps/frontend/src/pages/tasks/index.tsx`
  - 删除 `/v1/projects` 查询；
  - 删除 `assignTasksToProject` 调用；
  - 删除“归属项目”批量选择器；
  - 保留任务批量分类、取消选择和正常标注入口。
- `labelu/tests/e2e/test_7gai_platform_ui_contract_static.py`
  - 增加任务页不得出现项目查询、项目赋值接口和“归属项目”文案的静态门禁。
- `frontend/labelU-Kit/apps/frontend/src/routes.tsx`
  - `/rules` 兼容跳转到 `/admin`；
  - `/annotation-review` 兼容跳转到 `/evolution`；
  - 保留源码和历史接口，但不再作为一期可进入页面。
- `frontend/extensions/pages/yolo_training.html`
  - 旧训练页保留兼容函数名，但不再读取或转发 URL 中的 `project_id`。

后端项目兼容表、历史字段、迁移审计和未公开兼容接口未删除；本轮只撤销用户可见项目操作，避免误删历史数据或破坏旧数据读取。

## 4. 验证

```text
python -m pytest labelu/tests/e2e/test_7gai_platform_ui_contract_static.py -q --disable-warnings --tb=short
7 passed

pnpm --filter @labelu/frontend type-check
通过
```

前端生产构建在本轮代码变更后仍需执行；真实生产浏览器尚未验收。

补充验证：

```text
pnpm --filter @labelu/frontend build
退出码 0；4094 个模块转换；built in 29.81s
```

构建中的 Ant Design `use client`、`eval` 和大包体提示属于现有依赖/构建警告，不影响本轮退出码；生产浏览器仍需部署后验证。

## 5. 风险与遗留

- 直接访问 `/rules`、`/providers` 等历史技术路由仍由兼容路由保留，是否继续对外可达需下一轮统一退休策略；
- 后端 `/api/v1/projects` 和 `/v1/tasks/project-assignment` 保留是历史兼容，不代表一期重新启用项目维度；
- 本轮未提交、未推送、未部署。
