# RUN-0277 车牌训练前端测试契约与一期矩形框规则对齐（2026-09-20）

## 1. 触发原因

本地全量回归首次结果为 `2661 passed, 56 skipped, 1 failed`。失败测试仍要求新建训练展示“车牌框+四角点、4 个 PT 和组合 BModel”，与 RUN-0200 已确认的一期规则冲突：一期只训练车牌位置/数字位置矩形框，不使用多边形、四角点或 Pose；复合车牌仅保留历史兼容查看和历史迭代。

## 2. 调整内容

修改：`labelu/tests/e2e/test_yolo_training_plate_detection_frontend.py`。

将旧测试 `test_create_modal_exposes_both_plate_training_targets` 改为验证历史兼容契约：

- 页面保留历史复合目标字段，避免历史迭代数据无法展示；
- 历史复合预设标记“历史兼容，不可新建”；
- 新建一期训练提示使用普通目标检测矩形框标签；
- 历史复合训练仅用于兼容查看，不作为一期新训练入口。

没有恢复四角点、多边形、Pose、OCR、颜色或类型训练，也没有修改生产数据和业务接口。

## 3. 验证

```text
python -m pytest labelu/tests/e2e/test_yolo_training_plate_detection_frontend.py -q --disable-warnings --tb=short
8 passed, 113 warnings

git diff --check
通过（仅有工作树既有 CRLF 转换提示）
```

此前全量回归的唯一失败已定位为旧测试契约；调整后重新执行后结果为：

```text
python -m pytest labelu/tests -q --disable-warnings --tb=short
2662 passed, 56 skipped, 410 warnings in 497.74s
```

## 4. 交付边界

- 主责：R06 模型算法、R08 前端/UI、R09 测试；协作：R01 产品、R04 后端。
- 本地验收：L1，专项和全量回归均通过。
- 生产、AICM、S3、TPU-MLIR、BM1688 和两类账号：本轮未连接、未写入、未部署。
- 提交、推送、部署：未执行，等待用户明确“提交代码”。
