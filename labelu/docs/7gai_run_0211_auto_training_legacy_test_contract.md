# RUN-0211 自动训练旧测试契约迁移与二期隔离（2026-09-20）

## 责任角色

- R09 测试：迁移本地评估和持久化测试，补齐一期失败关闭断言。
- R04 后端：确认不移除 `ensure_auto_training_plan_writes_open()`，不改变生产默认开关。
- R03 架构、R06 模型算法协作：确认本地评估器属于服务实现验证，人工计划状态机属于二期隔离契约。

## 发现

`test_auto_training_local_evaluator.py` 原来复用评估数据集写入和评估请求物化服务；
`test_auto_training_persistence.py` 原来直接调用 `reserve_active_round()`。这些服务都受
`AUTO_TRAINING_PLAN_WRITES_ENABLED` 保护，而一期生产默认保持关闭。旧测试因此把“测试夹具
需要打开写服务”和“生产入口应该开放”混在一起，容易误导全量回归结论。

## 处理

- 本地评估器测试增加文件级、测试专用的显式开关，只验证本地评估器的请求验签、质量门禁、
  幂等和篡改拒绝；没有修改配置默认值，也没有恢复一期菜单/API。
- `reserve_active_round` 拆成两条契约：
  - 一期默认关闭时，在访问数据库前返回 `403`、错误码 `57122`；
  - 显式打开测试开关时，单独验证二期状态机的比较并交换（CAS）和版本递增。
- 保留 Outbox、冻结样本不可变、当前人工复核哈希匹配等一期自动回流底层契约。
- 未恢复任何人工计划写入口，未删除失败关闭保护。

## 本地验证

```text
python -m pytest \
  labelu/tests/internal/application/service/test_auto_training_local_evaluator.py \
  labelu/tests/internal/application/service/test_auto_training_persistence.py \
  -q --disable-warnings --tb=short
11 passed
```

## 放行边界

- 仅完成本地 L1 测试契约迁移；没有连接生产 MySQL、第三方 S3、AICM、TPU-MLIR 或 BM1688。
- 二期显式开关只存在于测试夹具，不能作为生产开关配置或发布验收证据。
- 未提交、未推送、未部署；生产自动回流仍需真实 S3 包、Worker、租约和重启接管证据。
