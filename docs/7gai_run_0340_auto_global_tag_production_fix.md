# 自动补全全局标签生产修复记录

日期：2026-10-21

## 问题

生产环境新增图片后，`7gai-auto-preannotation` Worker 能够接收任务，但样本还没有 `result` 标注结果时，完整性审计在解析空标注对象时抛出 `AttributeError: 'NoneType' object has no attribute 'get'`，任务停留在 `NEW`，没有继续调用已配置的 Qwen 全局标签模型。

## 修复

在 `labelu/internal/application/service/yolo_dataset.py` 的 `_parse_sample_annotation` 增加对象类型检查。刚上传、只有文件信息、尚未生成标注结果的样本现在按“暂无标注”处理，自动 Worker 可以继续执行 AI 全局标签和 YOLO 预标注流程。

## 本地验证

- `python -m pytest labelu/tests/internal/application/service/test_auto_sample_preannotate.py -q`
- 结果：15 passed；仅有项目原有 Pydantic/SQLAlchemy 弃用警告。
- 新增回归用例覆盖“新样本没有 result 字段”的解析路径。

## 生产验证

- 部署目录：`/data/7G_AI/app`。
- Web 与 `7gai-auto-preannotation` 已重启并保持 `RUNNING`。
- `/readyz` 返回 HTTP 200。
- 真实新增图片自动生成任务 `job_id=40`：`COMPLETED`，`processed_count=1`，`success_count=1`，`failed_count=0`。
- 该图片的 6 个全局标签维度已写入正式样本数据。
- 任务 1 当前图片总数 1809，全局标签完整 1809，缺失 0。

## 交付结论

生产新增图片会自动进入全局标签补全流程；已有任务 1 的全部图片已经补全。后续新增图片由 Worker 自动处理，不需要再次手动点击批量按钮。
