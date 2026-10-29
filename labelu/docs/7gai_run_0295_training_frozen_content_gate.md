# RUN-0295 训练冻结数据逐样本内容校验

## 1. 目标与菜单按钮

- 一级菜单：任务与标注、训练中心。
- 关联按钮：冻结数据集、新建训练、重试训练、查看训练日志。
- 两类用户均可创建任务、冻结数据和创建训练；管理人员负责异常处置和规则治理。
- 主责：R02 数据契约、R03 架构、R04 后端、R06 模型算法、R09 测试；协作 R10 运维。

## 2. 本轮发现与修复

此前训练 Worker 只从冻结清单读取 `sample_id`，随后回查当前任务样本；冻结后若图片或标注被修改，可能把新内容带入训练。本轮改为：

1. 生成 DatasetVersion 清单时保存每个样本的图片 `asset_sha256`、当前标注 `annotation_sha256` 和原始标注 `source_annotation_sha256`。
2. 训练 Worker 读取并校验冻结清单摘要；清单缺失、不可读或摘要不一致时失败关闭。
3. 训练前按 `sample_id` 找到当前样本，仅用于取得文件对象，然后重新计算图片和标注摘要。
4. 图片摘要、标注摘要、样本归属任一不一致时拒绝训练，不使用修改后的内容。
5. 标签子集训练同时保留“投影标注摘要”和“原始标注摘要”，兼容按单个标签训练而不放宽冻结约束。
6. 没有冻结清单摘要的历史作业保留旧兼容路径；新统一数据版本不允许回退到可变任务数据。

## 3. 修改范围

- `labelu/internal/application/service/dataset.py`
- `labelu/internal/application/service/yolo_train.py`
- `labelu/tests/internal/application/service/test_yolo_train_frozen_manifest.py`

未新增菜单、项目或数据库迁移；复用现有 DatasetVersion manifest 和训练作业 `hyperparams_json.dataset_manifest` 引用。

## 4. 并发与失败关闭

- 摘要校验发生在训练数据导出前，失败不会启动 Ultralytics 训练、不会生成新模型制品、不会进入编译队列。
- 计算摘要为每个冻结样本一次顺序读取；不持有长事务，不增加数据库锁等待。
- 多 Worker 同时读取同一冻结清单只产生只读校验，不产生重复写入；后续训练租约/fencing 继续负责作业状态写入。
- 数据文件找不到、摘要字段为空、附件内容变化和标注变化均明确失败关闭。

## 5. 验证证据

- `python -m pytest labelu/tests/internal/application/service/test_yolo_train_frozen_manifest.py labelu/tests/internal/application/service/test_yolo_train_create.py labelu/tests/internal/application/service/test_training_task.py -q --disable-warnings --tb=short`：28/28 通过。
- `python -m compileall -q labelu/internal/application/service/yolo_train.py labelu/internal/application/service/dataset.py`：通过。
- 覆盖清单摘要不匹配、清单不可读、历史兼容、冻结后图片变化、冻结后标注变化、标签子集源标注摘要。

## 6. 环境边界与验收等级

- 本地：L1，SQLite/测试文件摘要校验通过。
- 预生产：未完成，未使用真实 MySQL、对象存储、训练节点和并发 Worker。
- 生产：未连接、未登录、未写入、未迁移、未提交、未推送、未部署；现有生产仍为旧版本。
- 不能用本地 28 项测试替代真实冻结数据迁移、训练节点重启接管和大文件摘要吞吐验证。

## 7. 下一依赖

1. 在同版本 MySQL 和真实对象存储快照上验证冻结清单读取、迁移和备份恢复。
2. 用一个真实 YOLO 训练作业验证冻结后人工修改会在训练前失败关闭，并核对无 PT/BModel 副作用。
3. 用户明确“提交代码”前保持未提交、未推送、未部署。

