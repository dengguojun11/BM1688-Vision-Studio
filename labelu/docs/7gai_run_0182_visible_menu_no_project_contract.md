# RUN-0182 默认菜单与无项目主链契约

## 1. 目标

根据 7GAI 方案核对当前前端主导航、路由和页面请求，确保默认业务主链不依赖项目/业务空间，同时保留历史地址的安全兼容跳转。

## 2. 核对结论

- 默认导航固定为六个业务入口：工作台、任务与标注、训练中心、模型中心、数据回流、系统管理。
- `deployments` 只跳转到模型中心，不进入设备发布链；`workflows` 只跳转到系统管理，不恢复项目工作流。
- 默认可见页面 `workspace/training/models/evolution/admin` 不加载项目列表、不读取 `project_id`、不使用项目缓存键。
- 数据回流组件移除未使用的 `projectId` 属性，继续按全局难例和 AICM 模型 ID查询。
- 隐藏的高级技术页面仍可能保留历史项目参数，属于兼容/退休面，不得进入默认导航或一期验收主链；后续入口统一工作包继续处理。

## 3. 代码与测试

- 修改：`frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/evolution.tsx`。
- 新增契约测试：`labelu/tests/e2e/test_7gai_visible_menu_no_project_contract.py`。
- 测试：

```text
python -m pytest labelu/tests/e2e/test_7gai_visible_menu_no_project_contract.py -q --disable-warnings --tb=short
2 passed
```

## 4. 生产边界与遗留

- 未连接生产、未写数据库、未提交、未推送、未部署。
- 生产 OpenAPI、Nginx 和隐藏技术页面仍需部署后复核；不能用本地静态契约代替线上证据。
- 同步清理隐藏技术页面的项目兼容参数必须与对应后端退休路由和迁移证据一起进行，避免先改页面造成线上接口不兼容。

## 5. 验收等级

本轮达到本地 L1；预生产/生产仍未完成。
