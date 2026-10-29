# RUN-0234 管理页面中文文案与测试契约同步

## 1. 轮次目的

管理页面已经按一期规则使用中文优先的分类字段说明，但前端测试仍验证旧的“分类名称填写中文”和“分类标识（英文）”文案，导致测试与当前页面契约不一致。本轮只同步测试断言，不改变业务行为、接口、数据结构或权限。

## 2. 角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品 | WP0 | 确认分类名称用于页面展示，系统标识仅用于接口、训练数据和数据库关联；页面不向用户强调英文概念 |
| R03 架构 | WP2 | 确认测试只依赖稳定的中文业务语义，不绑定历史英文字段文案 |
| R08 前端/UI | WP4 | 保持现有居中弹窗、中文字段名和自动生成系统标识逻辑 |
| R09 测试 | WP5 | 更新管理页面文案断言，覆盖中文说明、系统标识生成和人工调整 |
| R10 运维 | OPS | 记录本地验证与生产未部署边界 |

## 3. 实现内容

- `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.test.tsx`
  - 测试名称从“生成英文键”调整为“生成系统键”。
  - 说明文案断言改为当前页面的“分类名称用于页面展示”。
  - 字段查询从历史“分类标识（英文）”改为当前“分类系统标识”。
- 未修改 `admin.tsx` 的运行逻辑、接口调用、权限判断、弹窗布局或数据库字段。

## 4. 本地验证

在 `frontend/labelU-Kit/apps/frontend` 执行：

```text
pnpm exec vitest run src/pages/platform/pages/admin.test.tsx src/pages/platform/wallboard.test.ts --reporter=dot
2 test files, 7 tests passed

pnpm --filter @labelu/frontend type-check
exit code 0

pnpm --filter @labelu/frontend build
exit code 0; 4094 modules transformed; built in 28.58s

git diff --check
exit code 0
```

Vitest 输出的 React 18、jsdom `window.computedStyle` 和 React Router future flag 为既有测试环境警告，不影响通过结果。工作台测试实际路径为 `src/pages/platform/wallboard.test.ts`。

## 5. 页面/API/数据影响

- 页面：管理人员进入“数据治理-数据集分类-新建数据集分类”时，继续使用居中弹窗；字段和说明保持中文优先。
- API：无新增或修改；仍使用现有数据集分类接口。
- 数据库/制品：无迁移、无模型制品、无数据写入。
- 权限：仍只有管理人员和标注人员两类；本轮不改变按钮权限。
- 并发/失败关闭：无运行时路径变化；分类保存仍由既有接口冲突处理负责。

## 6. 生产边界

本轮仅本地代码和测试验证，未提交、未推送、未部署，未连接生产 MySQL、S3、AICM、TPU-MLIR 或 BM1688。生产旧部署中的路由、角色投影和页面文案仍须在用户明确授权提交后，按部署闸门和两类账号回放流程验证。
