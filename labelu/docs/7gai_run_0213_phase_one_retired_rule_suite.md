# RUN-0213 一期退休规则中心二期隔离

## 责任角色

- R01 产品：确认一期不建设项目绑定、设备发布、规则下发和设备验签主链。
- R03 架构/R04 后端：保持旧规则中心服务层最前置失败关闭。
- R09 测试：将历史规则中心行为套件标记为二期隔离，并补一期退休入口契约。

## 处理

历史 `test_rule_center.py` 覆盖项目规则模板、版本发布、设备签名和设备回传。这些能力依赖已取消的项目/设备发布维度，不能继续作为一期成功回归；模块现在明确标记为二期隔离，不改变生产代码。

新增 `test_phase_one_retired_surfaces.py`，验证规则中心写入口通过服务层直接返回 `403`，不会创建项目规则或设备签名记录。

## 本地验证

```text
python -m pytest labelu/tests/internal/application/service/test_rule_center.py labelu/tests/internal/application/service/test_phase_one_retired_surfaces.py -q --disable-warnings --tb=short
1 passed, 22 skipped
```

## 边界

本轮未恢复规则中心、项目、设备发布或 OTA；未连接生产、未写生产数据、未提交、未推送、未部署。二期若重新启用规则能力，必须另建二期菜单/API/设备证据，不得复用一期放行结论。
