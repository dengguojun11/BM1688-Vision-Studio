# RUN-0233 用户角色投影与管理页面一致性

## 1. 问题确认

生产只读核验发现：管理员通过 `/api/v1/users/me` 返回 `is_task_superuser=true`，但 `/api/v1/users` 列表由旧服务直接构造 `UserResponse(id, username)`，默认把所有列表用户返回为 `is_task_superuser=false`。管理页面据此会把管理员显示成“标注人员”，与真实权限和两类角色方案不一致。

## 2. 角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品 | WP0 | 明确管理员列表身份和当前登录身份必须使用同一角色真源 |
| R03 架构 | WP1/WP2 | 统一 `is_task_superuser(user)` 作为角色计算入口 |
| R04 后端 | WP2 | 修正用户列表响应，不改变认证、权限和数据库结构 |
| R08 前端 | WP4 | 复用现有管理页面角色标签，不新增第三种角色 |
| R09 测试 | WP5 | 增加 `/users` 与 `/users/me` 角色一致性回归 |
| R10 运维 | OPS | 记录生产旧版本风险，待部署后复验 |

## 3. 实现内容

- `labelu/internal/application/service/user.py` 的用户列表映射统一调用 `is_task_superuser(user)`。
- 管理人员和标注人员仍只有两类；未修改登录、权限判断、用户表或历史数据。
- 新增测试，验证同一账号在 `/users` 和 `/users/me` 中返回相同管理员身份。

## 4. 本地验证

```text
python -m pytest labelu/tests/internal/adapter/routers/test_user.py -q --disable-warnings --tb=short
12 passed, 116 warnings in 1.90s

仓库后端全集复验：

```text
python -m pytest labelu/tests -q --disable-warnings --tb=short
2631 passed, 56 skipped, 410 warnings in 334.14s
```
```

## 5. 生产边界

本轮只修复本地代码并通过专项测试；生产当前仍是旧部署，管理员列表投影问题尚未线上修复。未提交、未推送、未部署。用户明确授权提交后，需在两类账号浏览器验收中核对管理页面角色标签。
