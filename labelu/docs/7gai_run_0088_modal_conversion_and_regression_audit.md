# RUN-0088 详情抽屉统一改为居中弹窗与回归审计

## 1. 需求与代码路径结论

方案要求所有详情、编辑和确认交互使用居中弹窗。回归核对发现边端设备配置页仍保留右侧详情抽屉，AICM 和边端端到端测试也仍引用旧的 `detailDrawerBackdrop`。LabelU-Kit 的任务标签属性配置组件还直接使用 Ant Design `Drawer`。

## 2. 本轮实现

- `frontend/extensions/pages/edge_device_registry.html`
  - 详情区域由右侧抽屉改为居中 `detailModalBackdrop/detail-modal`。
  - 详情、设备体检刷新、设为当前、遮罩关闭和编辑跳转统一使用弹窗状态。
  - 保留设备详情、凭据、BM1688 环境检查、运行参数、最近状态和操作按钮，不改变接口或权限。
- `frontend/labelU-Kit/apps/frontend/src/pages/tasks.[id].edit/partials/AnnotationConfig/formConfig/customFancy/ListAttribute.fancy/AttributeConfiguration.tsx`
  - Ant Design `Drawer` 改为居中 `Modal`，保留属性新增、校验、保存、取消和未保存确认逻辑。
- `labelu/tests/e2e/test_aicm_publish_environments_frontend.py`
  - AICM 详情测试改用 `detailModalBackdrop`，测试名称和断言同步弹窗语义。
- `labelu/tests/e2e/test_edge_device_registry_frontend.py`
  - 边端详情测试改用 `detailModalBackdrop .detail-modal`，补齐窄屏详情弹窗边界验证。

## 3. 验证结果

```text
python -m pytest labelu/tests/e2e/test_aicm_publish_environments_frontend.py labelu/tests/e2e/test_edge_device_registry_frontend.py -q --disable-warnings --tb=short
10 passed
```

生产页面与主前端源码扫描确认没有 `Drawer`、`StyledDrawer`、`drawer-backdrop` 或 `detailDrawer` 残留。前端全量 `type-check` 仍在既有 `useTranslation()` 类型推导位置报告 `TS2589`，该错误需要单独处理，不能把本轮弹窗改造写成类型检查已通过。

## 4. 边界与遗留

- 未新增 API、数据库字段、权限或业务状态；接口调用、设备租约和 BM1688 验证逻辑未改变。
- 未连接生产浏览器、BM1688 设备或生产数据库；本轮仅进行本地浏览器端到端测试和源码契约检查。
- `yolo_bm1688_exports_mockup.html` 仅为历史设计稿，不是生产入口；其文字仍描述底部抽屉，但不参与生产路由，后续清理设计资产时再处理。
- 未提交、未推送、未部署；等待用户明确“提交代码”。

结论：生产入口的抽屉残留改造达到本地 L1；前端全量类型检查、生产浏览器验收和生产放行仍未完成。
