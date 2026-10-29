# RUN-0171 第三方回流聚合组模型 ID 一致性门禁

**日期**：2026-09-20  
**环境级别**：本地 L1（未提交、未推送、未部署）  
**对应工作包**：WP4 第三方回流与自动进化、WP5 测试验收  
**责任角色**：R01 产品、R03 架构、R04 后端、R06 模型算法、R09 测试；R10 运维协作

## 1. 本轮要解决的问题

第三方问题图片通过 S3 回流时，系统会按 `aggregation_key` 把多个数据包合并为一个回流批次。
回流包中的 `target.model_id` 是 AICM `model_set_id`，同时用于模型血缘、难例聚类、任务创建和
后续训练。如果同一个聚合组同时出现两个不同的模型 ID，继续取第一条记录会把不同模型的问题
图片混入同一个任务，后续无法判断样本对应哪一个已发布模型。

## 2. 已落地改动

### 2.1 聚合前模型 ID 检查

- 在回流聚合组进入正式任务创建前读取所有包的 `target.model_id`。
- 多个非空模型 ID 不一致时，组状态进入 `WAITING_FOR_MODEL`，保存
  `aggregation_model_id_conflict` 和冲突模型 ID 列表。
- 空模型 ID 与非空模型 ID 混合时同样阻断；不猜测模型、不取第一条、不创建正式任务、不混合样本。
- 所有模型 ID 为空的历史类别型回流保持兼容，继续按原有标签/事件语义处理。

### 2.2 与模型中心的关系

- 回流只接受已经在模型中心发布目录登记的 AICM `model_set_id`；本地 `export_id` 不能替代 AICM ID。
- 模型未发布或模型目录未知时仍进入 `WAITING_FOR_MODEL`，待模型目录对账完成后由 Worker 自动重试。
- 模型 ID 一致性通过后，创建的任务、数据集版本、难例批次和训练作业继续沿用同一个模型 ID，供
  标注人员查看和管理人员审计；不新增项目/业务空间菜单。

## 3. 测试库兼容修复

部分持久化 pytest 数据库是在 `task_annotation_contract.dataset_label_contract_id` 加入 ORM 后创建的，
旧表缺少该可空列，导致回流任务在应用标签契约时失败。仅在 `labelu/tests/conftest.py` 增加按需
`ALTER TABLE` 兼容逻辑，使旧测试库与当前 ORM 对齐；没有修改正式 Alembic 迁移、生产数据库或业务
状态机。

## 4. 本地验证

```text
python -m pytest labelu/tests/internal/adapter/routers/test_experiment_dataset_import.py -q --disable-warnings --tb=short
22 passed

python -m pytest labelu/tests/internal/application/service/test_yolo_preannotate_model_selection.py -q --disable-warnings --tb=short
13 passed
```

新增/保留的关键断言：

- 同组 `534` 与 `535` 冲突时返回 `aggregation_model_id_conflict`。
- 同组全部为空时保持类别型历史回流兼容。
- 冲突发生在正式任务创建前，数据库中不产生混合模型任务。

## 5. 生产边界与放行条件

本轮没有连接生产 S3、生产 MySQL、AICM 或第三方平台，没有真实导入图片，也没有执行生产写入。
因此只能证明本地规则和回流代码契约，不能证明生产自动回流已经完成。

生产放行前仍需：

1. 使用真实第三方 `metadata.json` 对账 `target.model_id`、图片对象和包聚合键。
2. 使用 AICM 测试/预生产账号核对 `model_set_id`、本平台模型目录、回流包和创建任务的模型血缘。
3. 在同版本 MySQL 验证 Worker 租约、冲突重试、重复包幂等和 20 并发唯一领取。
4. 生产部署后由管理人员抽查少量冲突包，确认异常进入待处理队列，正常包由 Worker 自动完成，不依赖人工逐包操作。

在上述真实证据和用户明确“提交代码”之前，当前改动保持本地 L1，不提交、不推送、不部署。
