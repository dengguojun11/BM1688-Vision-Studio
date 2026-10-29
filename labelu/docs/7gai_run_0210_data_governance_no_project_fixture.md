# RUN-0210 数据治理版本校验测试迁移到无项目夹具（2026-09-20）

## 责任角色

- R09 测试：迁移旧测试夹具并补充一期 API 契约断言。
- R04 后端：确认不修改数据集/版本生产路由。
- R03 架构、R01 产品协作：确认数据治理一期按全局数据集和创建者归属，不使用项目参数。

## 发现

`test_dataset_version_validation_endpoint` 仍先调用 `POST /projects`，再把
`project_id` 放进数据集 URL。当前一期项目写入口已失败关闭，导致测试在真正进入
数据集版本校验前就收到 `403`，不能证明版本清单校验行为。

## 处理

- 改为直接调用 `POST /api/v1/datasets` 创建全局数据集。
- 增加响应契约断言：数据集响应不包含 `project_id`。
- 使用返回的数据集 ID 创建版本，保留原有缺失清单文件、摘要格式错误和校验接口
  `valid == false` 断言。
- 未修改生产代码、项目路由或历史兼容列。

## 本地验证

```text
python -m pytest \
  labelu/tests/internal/adapter/routers/test_data_governance.py::test_dataset_version_validation_endpoint \
  labelu/tests/internal/adapter/routers/test_dataset.py \
  labelu/tests/internal/adapter/routers/test_dataset_label_contract.py -q
13 passed
```

## 放行边界

- 仅本地 L1 测试契约迁移；没有连接生产数据库、第三方 S3、AICM 或执行迁移。
- 不代表数据治理、S3 回流和真实 MySQL 已生产验收。
- 未提交、未推送、未部署。
