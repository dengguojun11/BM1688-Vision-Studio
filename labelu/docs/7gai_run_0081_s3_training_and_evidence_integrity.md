# RUN-0081 第三方 S3 训练导出与边端证据完整性

## 1. 范围与角色

- 日期：2026-09-18，Asia/Shanghai。
- 工作包：WP2 训练与数据、WP3 BM1688/边端、WP5 并发与验收。
- 主责：R06 模型算法；协作：R04 后端、R07 边端、R09 测试。
- 关联方案：第三方 S3 问题图片回流、冻结训练数据、BM1688 真机验收包、并发领取和制品血缘。

## 2. 实际实现

### 2.1 第三方 S3 图片进入训练导出

新增统一`resolve_attachment_local_path()`，本地上传和`data_source_id`外部对象都从同一入口解析；S3对象复用缓存物化器。`prepare_yolo_dataset()`物化失败直接抛出错误，禁止继续训练并静默减少样本；自动评测校验、自动评测候选/冻结和训练任务冻结也改为复用该入口。成功物化后的文件仍由数据集清单计算内容摘要。边端样本包和板端评估包继续复用同一缓存能力，并在清单记录来源数据源、对象键和数据库摘要（不记录凭证）。

### 2.2 边端包完整性门禁

手工/自动边端包存在任何`skipped`项，或实际复制数量不等于用户选择数量时，不再写入`READY`；执行前门禁拒绝带跳过项的旧清单。清单新增每张图片的源内容 SHA256、打包文件 SHA256、标注 XML SHA256（有标注时）和整体`manifest_sha256`。门禁对带摘要的清单重新计算，摘要不一致时禁止 SSH/BM1688。

### 2.3 BM1688 制品执行签名

导出制品清单新增稳定`execution_signature`，只由数据集签名、类别契约、PT/ONNX/BModel 内容摘要、校准文件摘要和编译器语义配置计算；排除绝对路径、导出编号和生成时间。原`content_sha256`继续保留为包含路径/时间/日志的证据摘要，并新增`evidence_manifest_sha256`，兼容现有模型目录。

### 2.4 并发领取证据

训练 Worker 增加 20 个独立进程同时领取同一待训练作业的专项测试，只有一个进程获得队列租约，其余进程不产生副作用。该测试只证明 SQLite 条件更新行为，不替代 MySQL 锁等待、执行计划和生产压测。

## 3. 测试证据

```text
python -m pytest labelu/tests/internal/application/test_yolo_dataset_datasource.py labelu/tests/internal/application/service/test_yolo_edge_validation_pack.py labelu/tests/internal/common/test_model_asset_manifest.py -q --disable-warnings --tb=short
17 passed

python -m pytest labelu/tests/internal/application/service/test_yolo_train_worker.py -q --disable-warnings --tb=short
4 passed
```

训练队列、fencing和边端专项合并运行结果：`58 passed`；S3数据集/自动评测/训练冻结相关专项：`28 passed`；目标文件`py_compile`和`git diff --check`通过。

## 4. 权限、并发和失败处理

- 标注人员和管理人员使用原有任务/训练权限，本轮未新增登录角色。
- S3 物化失败、源文件不存在、清单摘要漂移均按失败关闭处理，不进入真机执行。
- 包生成在临时目录完成后再提交指针，避免并发读到半成品；执行门禁只读校验摘要，不重复下载同一对象。
- 20 进程测试仅覆盖本地 SQLite；生产 MySQL 仍需锁等待、索引、接管和容量证据。

## 5. 环境边界与未完成

- 已完成：本地代码、隔离 SQLite、本地文件模拟 S3 物化、静态测试。
- 未完成：真实第三方 S3 包、训练冻结快照对账、自动评测集物化、MySQL 正式迁移、TPU-MLIR、BM1688 真机、生产 AICM/S3 联调。
- `training_task.py`和`auto_training_pipeline_runtime.py`仍有独立的“只接受本地文件”路径，下一轮需统一复用同一物化/内容摘要解析器。

## 6. 状态

本轮达到本地 L1；不代表预生产或生产完成。未提交、未推送、未部署，等待用户明确说“提交代码”。
