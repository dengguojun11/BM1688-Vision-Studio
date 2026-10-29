# RUN-0334 标签类型中文语义与一期模型边界（2026-09-21，本地）

## 1. 目标与需求编号

- 需求编号：`LABEL-TYPE-UI-01`、`LABEL-TYPE-UI-02`。
- 目标：让标签库用户理解 `bbox` 的业务含义，明确一期只支持 YOLO11 目标检测的矩形框，不改变后端存储和训练契约。
- 范围：标签库列表、新建标签居中弹窗、前端回归测试；不扩展分类、分割或关键点训练能力。

## 2. 角色与工作包

| 责任角色 | 工作包 | 交付 |
|---|---|---|
| R01 产品 | WP4 | 将技术值 `bbox` 的页面语义定义为“矩形框（目标检测）”，并在弹窗说明一期边界 |
| R08 前端/UI | WP4 | 标签列表使用中文标识；新建标签弹窗展示只读标注类型说明 |
| R09 测试 | WP5 | 增加中文类型显示、弹窗类型值、自动颜色和创建请求回归 |

## 3. 改动与影响

- 改动文件：
  - `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.tsx`
  - `frontend/labelU-Kit/apps/frontend/src/pages/platform/pages/admin.test.tsx`
- 数据库：无表结构、字段和数据变更。
- API：无路由或请求结构变更；新建标签仍提交 `annotation_kind=bbox`，颜色仍由后端自动分配。
- 训练/边端：无逻辑变更；YOLO11、ONNX、BModel 和 BM1688 链路继续使用矩形框契约。

## 4. 页面行为

- 标签列表中的 `bbox` 显示为“矩形框（目标检测）”。
- 新建标签弹窗显示只读“矩形框（目标检测）”。
- 弹窗说明：一期训练链路使用 YOLO11 目标检测并部署到 BM1688，只支持矩形框；系统内部仍使用 `bbox` 保存。
- 未来分类、分割、关键点等类型不在本期页面开放，必须等对应数据导出、训练、预标注、编译和真机验证链路闭合后再开放。

## 5. 验收证据

- 前端专项：`npx vitest run src/pages/platform/pages/admin.test.tsx --reporter=dot`，`5 passed`。
- 类型检查：`npm run type-check`，退出码 `0`。
- 浏览器：`http://127.0.0.1:8002/admin?view=governance`，列表和新建标签居中弹窗均显示“矩形框（目标检测）”。
- 本地服务：健康检查和就绪检查均为 `200`。
- 本地统一放行门禁：`python scripts/verify_staging_and_hil.py --environment local --run-local --check --evidence-root .tmp/evidence/run0334`，`blocking: 0`；证据为 `.tmp/evidence/run0334/local-gates-20260921T035827Z.json`。
- 浏览器操作未点击“创建”，无业务测试数据写入。

## 6. 风险、遗留项与下一依赖

- 风险：后台已预留 `class_label`、`polygon`、`keypoints` 等训练配方，但当前完整生产链尚未闭合，不能仅增加下拉选项宣称可用。
- 遗留：真实 GPU/TPU-MLIR、FP32/FP16/INT8 BModel、BM1688 真机、AICM/S3 和生产 MySQL 并发证据仍缺失。
- 下一依赖：先完成生产版本对齐和真实 HIL，再按独立训练配方评估是否开放其他标注类型。

## 7. 提交与部署状态

- 本轮仅本地代码和本地验证。
- 未提交、未推送、未部署、未连接生产。
