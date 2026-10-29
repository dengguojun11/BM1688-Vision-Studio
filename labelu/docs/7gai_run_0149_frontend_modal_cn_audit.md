# RUN-0149 前端弹窗居中与中文状态展示审计

## 1. 本轮范围

- 责任角色：R01 产品、R08 前端、R09 测试；R03 架构、R04 后端协作。
- 目标：把平台页面的 Ant Design `Modal` 统一为居中弹窗；把模型中心、数据回流中的内部状态转换为中文；保留 BM1688、PT、ONNX、BModel、FP32、FP16、INT8、SHA256、模型 ID、设备 ID、AICM 编号等必要技术标识。
- 用户角色边界：系统仍只有“标注人员”和“管理人员”。标注人员可以创建任务、确认标注并在质量门禁通过后发布模型；管理人员处理发布目录、下载开关和异常数据。没有新增项目、业务空间、设备发布、OTA 或模型回滚菜单。

## 2. 代码交付

### 2.1 弹窗居中

以下页面中的 Ant Design `Modal` 已统一补充 `centered`：

`DeviceSignatureEvidence.tsx`、`ResourcePage.tsx`、`RuleCenter.tsx`、`pages/admin.tsx`、`pages/algorithms.tsx`、`pages/annotationReview.tsx`、`pages/deployments.tsx`、`pages/evolution.tsx`、`pages/ingestion.tsx`、`pages/models.tsx`、`pages/providers.tsx`、`pages/training.tsx`、`pages/workflows.tsx`、`pages/tasks/index.tsx`。

新增静态契约测试：

`frontend/labelU-Kit/apps/frontend/src/pages/platform/modalCentering.test.ts`

测试按页面统计 `Modal` 与 `centered` 属性数量，防止后续新增未居中弹窗。旧扩展页 `frontend/extensions/pages/yolo_bm1688_exports.html` 使用已有遮罩层居中弹窗，本轮未重复改造。

### 2.2 中文状态和制品名称

模型中心新增 `modelStatusLabel` 与 `modelArtifactTypeLabel`，仅转换页面显示值，接口原始枚举保持不变：

- `PUBLISHED`、`PROCESSING`、`READY`、`VALIDATED`、`CANDIDATE`、`FAILED` 等显示为中文状态。
- `pt`、`onnx`、`bmodel`、`manifest` 显示为“训练权重 PT”“中间模型 ONNX”“BM1688 模型（BModel）”“模型说明清单”。
- `models.tsx` 继续明确发布前必须同时具备 READY 的 BM1688 FP32、FP16、INT8 三种 BModel；三种精度是同一模型版本的边端制品，不是三个业务模型。

数据回流页面将审核状态、训练状态、数据集版本状态、工作流状态、难例类型映射为中文，内部状态值仍用于接口判断。

## 3. 验收证据

本地前端目录执行：

```text
pnpm exec vitest run src/pages/platform/pages/models.test.tsx src/pages/platform/pages/evolution.test.tsx src/pages/platform/modalCentering.test.ts
3 个测试文件，13 项测试通过
pnpm run type-check
通过
```

Ant Design/jsdom 的 `window.computedStyle` 警告属于测试环境提示，不影响退出码 0。

本轮没有增加 HTTP 请求、轮询、数据库锁、队列消费者或外部副作用；未连接生产、S3、AICM、TPU-MLIR、BM1688，未提交、未推送、未部署，验收等级为本地 L1。

## 4. 未闭合问题和边界

数据回流页面仍依赖旧的 `usePlatformProject`、`project_id` 和项目参数，后端 `edge_feedback`、`active_learning` 及其服务也仍要求项目作用域。这不是前端默认填值可以解决的问题，不能伪造项目 ID 或删除参数后宣称去项目化闭环完成。后续由 R03 架构、R04 后端、R09 测试统一改造接口、数据表和权限链路，前端再移除项目选择提示。

本轮只完成页面可用性和状态可读性，不代表数据回流自动导入、模型发布并发租约、真实 BM1688 编译/真机验证或生产放行完成。

## 5. 后续验收条件

1. 后端完成回流接口去项目化并补充标注人员、管理人员权限测试。
2. 在同版本 MySQL、真实 S3/AICM、TPU-MLIR 和 BM1688 环境补齐端到端证据。
3. 生产部署前重新执行本轮三项前端测试和页面冒烟；生产 Worker 继续保持关闭，直到迁移、并发和真机证据齐全。
