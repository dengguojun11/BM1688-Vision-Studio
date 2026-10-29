# RUN-0220 标签契约、自动预标注与按标签训练回归

## 1. 目标

验证一期主流程中“先有标签契约，再创建任务；任务可自动选择兼容预标注模型；人工确认后冻结数据版本；训练可使用全量标签或当前数据集中的单个标签”这一条复用链路。

一期仍只允许普通目标检测矩形框。车牌任务使用“车牌”“数字位置”等普通矩形框标签，不启用多边形、四角点、Pose 或 OCR 字段。历史复合标注数据只读兼容，不作为新任务入口。

## 2. 角色与范围

| 角色 | 工作包 | 验收点 |
|---|---|---|
| R01 产品 | WP0 | 标签来源、任务创建、预标注、冻结和训练按钮的顺序清晰 |
| R02 数据契约/体验 | WP1 | 标签契约版本、工具类型、标签顺序和按标签子集可追溯 |
| R04 后端 | WP1/WP3 | 标签契约校验、预标注幂等、数据版本绑定和训练参数校验 |
| R06 模型算法 | WP3 | 预标注模型与图片/工具/标签契约兼容；训练标签子集不改变检测框语义 |
| R08 前端/UI | WP1/WP4 | 新建任务使用契约标签；自动预标注按钮和状态中文显示 |
| R09 测试 | WP5 | 路由、服务、进度、模型筛选和数据版本子集联合回归 |

## 3. 本轮验证

```text
python -m pytest \
  labelu/tests/internal/adapter/routers/test_yolo_preannotate_router.py \
  labelu/tests/internal/application/service/test_auto_sample_preannotate.py \
  labelu/tests/internal/application/service/test_yolo_preannotate_model_selection.py \
  labelu/tests/internal/application/service/test_yolo_preannotate_progress.py \
  labelu/tests/internal/adapter/routers/test_dataset.py \
  labelu/tests/internal/adapter/routers/test_dataset_label_contract.py \
  labelu/tests/internal/application/service/test_dataset_version_label_subset.py \
  -q --disable-warnings --tb=short

41 passed, 123 warnings in 5.41s
```

覆盖内容包括：

- 标签契约创建、读取、冻结和权限边界；
- 自动预标注任务创建、重复点击幂等、进度和失败状态；
- 按图片类型、标注工具、标签契约选择兼容模型；
- 任务确认后生成数据版本，训练只读取冻结版本；
- 全量标签训练和单个标签子集训练的契约校验；
- 不允许通过训练参数绕过普通矩形框和标签契约。

## 4. 并发与失败关闭

- 预标注按钮重复提交必须复用同一任务作业，不重复处理样本；
- 标签契约版本冻结后不可原地修改，训练引用版本号；
- 预标注模型不兼容时返回失败状态，不降级为未声明模型；
- 未人工确认或未冻结的数据不能进入训练；
- 单标签训练只生成数据版本的标签子集，不修改原始任务标注；
- 生产自动预标注 Worker 在真实外部模型服务和容量证据前继续关闭。

## 5. 环境边界与验收等级

本轮仅本地测试数据库和模拟服务，未连接生产 S3、AICM、TPU-MLIR、GPU 或 BM1688，未提交、未推送、未部署。达到 **L1**，不代表自动预标注生产吞吐、真实模型精度或边端验证通过。

下一步需在部署授权后用两类生产账号回放：任务创建选择契约标签、自动预标注、人工确认、冻结数据版本、全量/单标签训练；再补真实预标注模型服务和生产并发证据。

