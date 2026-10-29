# RUN-0148 训练提交链不可变数据版本与清单完整性

- 日期和时区：2026-09-19，Asia/Shanghai。
- 工作包/角色：WP2 训练与数据冻结；R03 架构主责，R04 后端、R06 模型算法、R09 测试协作。
- 本轮范围：审计统一训练提交链是否在创建训练作业时自动生成/绑定不可变 `DatasetVersion`，以及 YOLO Worker 是否真正按冻结样本、标签快照和划分执行。
- 生产边界：只读本地代码和 SQLite 测试；未连接生产数据库，未执行迁移、训练、GPU、TPU-MLIR、BM1688、S3、AICM 或部署。

## 1. 审计结论

统一 `/training/jobs` 提交链已具备以下闭环：

1. 未显式传入数据版本时，路由调用 `ensure_training_version_for_task`，按当前任务中已完成、质量合格且有媒体文件的样本生成或复用冻结版本。
2. 版本清单包含来源任务、样本 ID、媒体摘要、最终标注快照、训练/验证/测试划分及比例、标签契约快照、类别顺序摘要和清单 SHA256；原始人工标注不被改写。
3. 选择一个或多个标签时，版本使用新的标签集合和类别顺序摘要，只在冻结清单中保留对应矩形框；全量训练仍保留全量类别。
4. 重复提交相同任务、样本集合、标签集合和样本标注摘要时复用同一冻结版本；样本或标注发生变化时生成新的版本，旧作业继续指向旧版本。
5. 训练作业保存 `dataset_version_id`、清单路径、清单 SHA256、标签契约修订和冻结时间；Worker 在普通 YOLO 路径按清单中的 sample_id 筛选任务样本，并按冻结标签快照导出类别。

## 2. 本轮修复

发现 Worker 原先只按清单路径读取内容，未核对作业中保存的 `manifest_sha256`。清单被替换、截断或不可读时，读取函数可能返回空集合并回退到可变任务数据，破坏不可变数据版本语义。

本轮修改：

- `labelu/internal/application/service/yolo_train.py`
  - 读取统一作业的冻结清单后，使用 UTF-8 内容计算 SHA256，并与作业快照中的 `manifest_sha256` 比较。
  - 摘要不一致、清单不可读、缺少 `samples` 或样本集合为空时直接抛出阻断错误，不再回退到任务当前数据。
  - 无冻结清单摘要的历史作业仍保留兼容读取行为；新统一训练作业必须带摘要并失败关闭。
- `labelu/tests/internal/application/service/test_yolo_train_frozen_manifest.py`
  - 增加正确摘要读取、摘要不一致拒绝、带摘要清单不可读拒绝测试。

## 3. 本地验证

- `python -m pytest labelu/tests/internal/application/service/test_yolo_train_frozen_manifest.py labelu/tests/internal/application/service/test_dataset_version_label_subset.py -q --disable-warnings --tb=short`
  - `5 passed`。
- 上述专项覆盖：冻结清单样本 ID、SHA256 完整性、不可读失败关闭、单标签矩形框过滤、原始标注不变、全量标签版本和源数据修改后的新版本生成。
- 训练 Worker、运行时超参、持久化和运行时恢复联合回归：`42 passed`。本轮复核未再复现此前一次旧夹具向已退役 `dataset.project_id NOT NULL` 结构写入导致的失败；迁移基线仍需在正式 MySQL 沙箱中单独验收。
- 本轮未提交、未推送、未部署，等待用户明确“提交代码”。

## 4. 尚未完成的验收

- 同版本 MySQL 上的 DatasetVersion/训练作业并发创建、唯一摘要冲突、锁等待和双 Worker 领取。
- 真实 GPU 训练、TPU-MLIR、BM1688 三精度制品和真机推理；本地 SQLite/单元测试不代表生产可用。
- 真实对象存储故障、清单大文件读取、第三方 S3/AICM 回流和生产训练 Worker 接管。

在以上证据完成前，生产训练 Worker 保持关闭，平台整体仍按本地 L1 处理。
