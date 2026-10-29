# RUN-0200 一期车牌矩形框训练入口收口

- 日期和时区：2026-09-20，Asia/Shanghai。
- 责任角色：R06 模型算法、R08 前端/UI、R09 测试；协作角色：R01 产品、R04 后端、R07 BM1688。
- 工作包：WP0 需求边界、WP1 任务标注入口、WP3 模型训练入口、WP5 回归验收。

## 需求结论

一期车牌模型按目标检测处理，只训练“车牌所在位置”和需要单独定位时的“数字位置”矩形框，不训练车牌号码文字，不使用多边形框、四角点或 Pose 标注。

新任务应在“任务与标注”中使用普通目标检测预设和矩形框标签，例如“车牌”“数字位置”。原有 `plate_recognition_v1` 复合车牌链依赖 `polygonTool`/四角点，属于历史兼容能力：可以查看历史任务和继续历史迭代，但不再允许用于一期新任务。

## 真实调用链审计

1. 新任务先通过 `POST /tasks` 创建空任务。
2. 普通任务通过“应用拉框预设”写入 `rectTool` 标签契约。
3. 旧复合车牌预设原来会由 `normalize_plate_recognition_task_config` 同时写入 `rectTool` 和 `polygonTool`，训练中心再进入 Pose/四组件链。
4. 本轮在应用预设和任务配置更新两个写入口加门禁；历史任务通过已有复合配置识别后仍可读写兼容配置。
5. 训练页面把旧复合预设标为“历史兼容，不可新建”，禁用新建勾选；历史迭代快照仍保留原目标选择。

## 代码变更

- `labelu/internal/application/service/annotation_taxonomy.py`
  - 新任务或普通任务不能应用 `plate_recognition_v1` 复合车牌预设。
  - 返回 `409`，明确提示使用普通目标检测矩形框标签。
  - 已存在完整历史复合配置的任务保留原兼容路径。
- `labelu/internal/application/service/task.py`
  - 普通任务 PATCH 不得首次引入复合车牌/多边形配置。
  - 历史复合任务的旧客户端配置更新仍可兼容，不改写历史语义。
- `frontend/extensions/pages/yolo_training.html`
  - 复合车牌预设在选择框和复选框中显示“历史兼容，不可新建”并禁用。
  - 新建训练时阻断历史复合链，提示返回“任务与标注”使用普通目标检测矩形框标签。
  - 历史迭代保留原入口和历史结构说明，不误删历史任务展示。
- `labelu/tests/internal/adapter/routers/test_apply_label_preset.py`
  - 覆盖新任务应用复合车牌预设返回 `409`。
  - 覆盖普通任务 PATCH 首次引入复合多边形配置返回 `409`。
- `labelu/tests/e2e/test_yolo_training_plate_detection_frontend.py`
  - 增加一期入口禁用、历史兼容文案和矩形框引导契约。

## 并发、权限和失败关闭

- 门禁发生在服务层，不能通过绕过前端按钮的 HTTP 请求创建新复合车牌任务。
- 失败使用 `409`，不写入任务配置，不创建训练任务、不触发预标注或自动训练事件。
- 历史任务识别只读取已有配置，不把普通任务的标签、任务名或模型名猜测成车牌复合任务。
- 本轮不删除历史样本、历史 PT、ONNX、BModel 或 AICM 发布记录。

## 本地验证

```text
python -m pytest labelu/tests/internal/adapter/routers/test_apply_label_preset.py labelu/tests/internal/adapter/routers/test_task.py -q --disable-warnings --tb=short
36 passed, 127 warnings in 3.83s

python -m pytest labelu/tests/internal/common/test_plate_recognition_profile.py labelu/tests/internal/application/service/test_training_task.py labelu/tests/internal/application/service/test_plate_recognition_train.py -q --disable-warnings --tb=short
54 passed, 116 warnings in 11.24s

python -m pytest labelu/tests/e2e/test_yolo_training_plate_detection_frontend.py -q --disable-warnings --tb=short
8 passed, 112 warnings in 0.55s
```

另行通过：修改文件 `py_compile` 和 `git diff --check`。本轮未连接生产 MySQL、第三方 S3、AICM、TPU-MLIR 或 BM1688 真机，未提交、未推送、未部署。

## 验收等级与后续依赖

- 本地：L1，通过后端门禁、历史兼容、训练兼容和前端静态契约。
- 预生产/生产：未验收。需要 R10 使用两类账号确认新任务只能选普通矩形框标签，并用一条真实车牌任务完成训练、BM1688 编译、真机验证和下载目录核对。
- 历史复合车牌链不属于一期新训练主链；其后续是否继续维护由产品另行决定，不得作为一期矩形框需求的验收证据。

