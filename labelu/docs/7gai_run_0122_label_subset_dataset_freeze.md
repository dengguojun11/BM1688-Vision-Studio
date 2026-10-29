# RUN-0122 统一训练标签子集与不可变数据版本

## 1. 轮次信息

- 日期和时区：2026-09-19，Asia/Shanghai。
- 工作包：WP1 任务与标注、WP2 训练主链。
- 角色：R02 标注与数据契约、R04 后端主责，R06 模型算法、R09 测试协作。
- 关联方案：主方案第 7 章任务与预标注、第 8 章训练中心、第 9 章固定评估集与边端制品。
- 前置轮次：RUN-0121 Provider 预标注去项目化收口。

## 2. 发现的问题

统一训练接口允许提交 `label_ids`，但此前只把它写入 `selection_filter`，自动生成的数据版本仍然是任务的全量标签快照，Worker 也按任务配置生成全量类别。这会让用户选择“只训练车牌框”时，实际仍可能训练其他类别，和页面选择不一致。

旧的“标签维度训练任务”链路已有自己的标签快照，但不能把旧链路当成统一 `/api/v1/training/jobs` 主流程已经完成。统一主流程必须自己保存可复现的标签子集版本。

## 3. 本轮实现

### 3.1 数据版本生成

- `ensure_training_version_for_task()` 新增可选 `selected_label_ids`。
- 训练提交未指定 `dataset_version_id` 时，把本次 `label_ids` 传入自动冻结流程。
- `create_version_from_tasks()` 校验所选标签属于任务绑定的数据集标签契约，并按契约顺序生成子集 `labels_snapshot_json`、类别顺序摘要和标签集合摘要。
- manifest 的 `annotation_contract` 记录子集标签和类别顺序；每个样本的 `rectTool` 只保留所选标签的框，其他标签框从冻结清单中排除，原始人工标注不被改写。
- 兼容生产历史样本 `{"result":"{...rectTool...}"}` 和新样本内嵌对象两种存储形态；冻结时只改写 manifest 副本，不回写 `task_sample.data`。
- 版本 metadata 记录 `selected_label_ids`、`selection_mode` 和每个样本的标注内容摘要；只有任务、样本集合、标签集合和标注摘要都一致时才复用原冻结版本，修改标注后会生成新的不可变版本，不覆盖旧版本。

### 3.2 Worker 导出

- 统一训练作业的 `dataset_manifest.labels_snapshot_json` 作为类别子集的唯一输入。
- `prepare_yolo_dataset()` 新增 `selected_class_names`，存在冻结子集时只生成这些类别的 `data.yaml` 和标签文件；旧作业没有该字段时保持原兼容逻辑。
- 负样本仍需人工明确确认，只有被选类别的框进入正样本；其他类别框不会错误映射成选中类别。

### 3.3 页面和权限行为

- 页面仍使用“新建训练”弹窗选择全量或部分标签，不新增第二套数据集维护入口。
- 标注人员只能从自己有权的任务创建训练；管理人员可处理全局任务。标签选择必须来自任务冻结契约，非法标签返回 409，不启动 Worker。
- DatasetVersion、manifest 和训练作业均保留任务、标签契约、模型和制品血缘，便于模型中心按模型 ID回流匹配。

## 4. 本地验证

执行命令：

```text
python -m pytest labelu/tests/internal/application/test_yolo_explicit_negative_samples.py -q --disable-warnings --tb=short
python -m pytest labelu/tests/internal/application/service/test_training_task.py labelu/tests/internal/application/service/test_training_contract_freeze.py -q --disable-warnings --tb=short
python -m pytest labelu/tests/internal/adapter/routers/test_training.py labelu/tests/internal/application/service/test_training_worker_persistence.py -q --disable-warnings --tb=short
```

结果分别为 `9 passed`、`23 passed`、`20 passed`。新增回归验证：同一张图片含两个框类别时，选择一个类别只生成一个类别的标签文件和类别编号；原有负样本、训练任务快照和训练 Worker 持久化回归均通过。Python 编译和 `git diff --check` 通过。

新增服务级冻结回归：

```text
python -m pytest labelu/tests/internal/application/service/test_dataset_version_label_subset.py -q --disable-warnings --tb=short
```

结果为 `1 passed`。覆盖双标签任务的单标签冻结、历史 `result` 字符串解析、全量/子集生成不同不可变版本、原始标注不变和旧 manifest 不受后续修改影响。

## 5. 尚未完成的验收

- 本轮只解决标签子集是否真正进入冻结 manifest 和训练导出，尚未实现同源分组、近重复去重、固定测试集和 INT8 校准集隔离。现有完整质量审计对部分历史嵌套 `result` 字符串仍未统一，需单独纳入后续兼容工作包。
- 未执行真实 GPU/远程训练节点、MySQL 双 Worker、systemd、S3、AICM、TPU-MLIR 或 BM1688。
- 生产 `113.141.72.175:10000` 尚未部署本轮代码；生产数据和已发布模型没有被修改。

## 6. 验收结论

- 标签全量/单标签训练的一致性达到本地 L1：选择结果、不可变版本、manifest 类别和 Worker 导出契约已闭合。
- L2 需要同版本 MySQL 双进程测试，确认同一标签子集并发提交只产生一个 DatasetVersion 和一个训练作业。
- L3/L4 仍需真实训练节点、真实数据规模、固定评估集、BM1688 和生产发布证据。

## 7. 交付状态

未提交、未推送、未部署，等待用户明确说“提交代码”。
