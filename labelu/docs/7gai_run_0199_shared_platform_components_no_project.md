# RUN-0199 共享平台组件去项目化与通知按需加载

## 1. 目标与范围

本轮针对一期平台壳中被多个菜单复用的共享组件做收口：页面和一期业务不再以“项目/业务空间”为用户操作维度，同时保留历史接口的最小兼容边界。涉及菜单：工作台、任务与标注、训练中心、模型中心、进化中心、平台设置，以及主布局通知入口。

本轮不删除数据库中的历史 `project_id` 字段，不改生产数据，不连接生产，不提交、不推送、不部署。

## 2. 调用链核验结论

- `ResourcePage` 被训练、数据集、算法、Provider 等历史页面复用；一期页面调用使用 `needsProject=false`，请求、查询键和详情展示均不需要项目。
- `ResourcePage` 的 `needsProject=true`、`usePlatformProject` 和 `projectQueryKey` 仍保留给隐藏/历史兼容页面，不能误删后端或数据库兼容链。
- `NotificationCenter` 由主布局调用；现用告警接口 `/v1/workspace/alerts` 支持无项目参数，通知查询键统一为 `['notifications']`。
- `DeviceSignatureEvidence` 的规则验签后端仍要求历史 `project_id`，因此只在内部 `ruleApiPaths(projectId)` 请求中保留；页面文案和详情字段不展示项目概念。

## 3. 代码与交互变更

### 3.1 资源页

- `needsProject=false` 时，列表查询键为 `[queryKey, page, size]`，不把项目 ID 放进缓存键，也不向页面显示项目字段。
- 详情治理字段、403/404 和空状态文案改为账号/数据权限表述，不再出现“当前项目”“项目成员”“项目权限”等一期可见概念。
- `needsProject=true` 继续走历史兼容查询键，仅供兼容页面内部使用。

### 3.2 通知中心

- 组件改为无参数调用：`<NotificationCenter />`。
- 请求固定为 `/v1/workspace/alerts`，参数仅为 `page`、`size`，不发送 `project_id`。
- 仅在通知弹层打开且存在登录令牌时读取，避免主布局在每个页面常驻请求告警接口。
- 刷新和重试沿用同一个查询，避免重复实现；查询键固定为 `['notifications']`，多个页面挂载时共享缓存。

### 3.3 设备验签证据

- 保留内部历史规则接口所需的 `projectId`，不把它转成可见字段。
- 无权限和空状态改为“当前账号无权查看验签报告”“暂无设备验签报告”。

### 3.4 主布局

- 删除 `project_id` 地址参数解析和向通知中心传递项目 ID。
- 历史带项目链接仍由路由兼容层清理参数后跳转，不恢复项目菜单。

## 4. 菜单与按钮影响

本轮没有新增一级菜单或按钮。通知中心按钮“通知中心”、“刷新通知”、“重试”保持原有功能；资源列表的查询、查看详情和分页按钮只改变请求/文案契约。所有详情使用居中弹窗，不恢复抽屉。

## 5. 并发、缓存与失败关闭

- React Query 使用稳定无项目查询键，避免同一告警被按项目拆成多份缓存；弹层打开时才发起请求，降低主布局常驻压力。
- 刷新按钮复用同一查询实例；后端失败时显示可重试状态，不伪造空数据。
- 历史验签接口缺少项目兼容参数时保持失败关闭，只提示无权/无报告，不把内部兼容字段暴露给用户。
- 本轮未改变后端租约、数据库锁或 Worker 并发控制；生产并发容量仍需由 R10 按现有专项验证。

## 6. 变更文件

- `frontend/labelU-Kit/apps/frontend/src/pages/platform/ResourcePage.tsx`
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/NotificationCenter.tsx`
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/NotificationCenter.test.tsx`
- `frontend/labelU-Kit/apps/frontend/src/layouts/MainLayoutWithNavigation/index.tsx`
- `frontend/labelU-Kit/apps/frontend/src/pages/platform/DeviceSignatureEvidence.tsx`
- `labelu/tests/e2e/test_7gai_platform_ui_contract_static.py`

## 7. 本地验证

| 验证项 | 结果 |
|---|---|
| `pnpm --filter @labelu/frontend type-check` | 通过 |
| `pnpm --filter @labelu/frontend exec vitest run src/pages/platform/NotificationCenter.test.tsx` | 2 passed |
| `python -m pytest labelu/tests/e2e/test_7gai_platform_ui_contract_static.py -q` | 5 passed |
| 生产浏览器、真实账号、MySQL、S3、AICM、TPU-MLIR、BM1688 | 未执行 |

Vitest 仍输出 React/Ant Design 既有弃用警告，但不影响本轮断言；未发现本轮新增失败。

## 8. 角色验收等级与后续依赖

- R08 前端/UI：本地 L1，组件和交互契约完成；真实三视口和真实 API 联调待执行。
- R09 测试：本地 L1，静态与单元专项通过；浏览器回放和并发压测待执行。
- R03/R04 架构/后端：本地 L1，确认项目兼容边界未误删；生产路由和历史接口对账待执行。
- R10 运维：未验收；不得据此连接生产或做数据库变更。

提交状态：未提交、未推送、未部署，等待用户明确“提交代码”。
