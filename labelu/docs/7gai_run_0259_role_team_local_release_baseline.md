# RUN-0259 角色团队联合本地发布基线（2026-09-20）

## 目标

按既定 R01-R10 角色团队对当前工作树做一次联合验收，确认菜单、任务标注、训练、模型中心、进化中心、权限、并发失败关闭和文档交付机制没有因各工作包并行开发而互相破坏。本轮只做本地验证，不提交、不推送、不部署，不连接生产数据库。

## 角色与验收范围

| 角色 | 本轮核对内容 | 验收证据 |
|---|---|---|
| R01 产品 | 六个一级菜单、两类用户、无项目维度、标注人员可创建任务和发布模型 | RUN-0254 矩阵、前端契约测试 |
| R02 交互 | 中文按钮、居中弹窗、无抽屉、异常后果提示 | 前端静态/浏览器契约 |
| R03 架构 | 训练、评估、编译、边端、AICM 和回流的血缘与幂等边界 | 后端全量、迁移闸门专项 |
| R04 后端 | 任务、预标注、自动训练、进化租约、模型目录权限 | 后端全量测试 |
| R05 外部平台 | AICM 编号对齐、本平台保留下载、外部发布不回滚 | 模型中心专项 |
| R06 模型算法 | PT→ONNX→FP32/FP16/INT8 BModel 以及固定评估集门禁 | 编译/质量专项 |
| R07 BM1688 边端 | Board Agent 主路径、SSH 备用路径、执行代次与制品验签 | 边端专项；真机仍待验收 |
| R08 前端/UI | 菜单权限、任务入口、模型中心和进化中心操作状态 | 类型检查、生产构建、页面契约 |
| R09 测试 | 全量回归、专项回归、失败关闭证据 | 本文第 3 节 |
| R10 运维 | 迁移、部署、监控和生产放行条件 | 迁移闸门与生产探针；当前失败关闭 |

## 本地验证结果

```text
python -m pytest labelu/tests -q --disable-warnings --tb=short
2648 passed, 56 skipped, 410 warnings

python -m pytest labelu/tests/alembic labelu/tests/internal/common/test_migration_release_gate.py labelu/tests/internal/common/test_openapi_docs.py -q --disable-warnings --tb=short
104 passed, 142 warnings

python -m pytest labelu/tests/internal/application/service/test_strategy_evolution_auto_advance.py labelu/tests/internal/application/service/test_yolo_train_evaluation_dataset_evidence.py labelu/tests/internal/application/service/test_yolo_bm1688_edge_business_flow.py labelu/tests/internal/application/service/test_published_model_catalog.py -q --disable-warnings --tb=short
63 passed, 113 warnings

npm run type-check
通过

npm run build
通过，4094 modules transformed

git diff --check
通过；仅有 Git 对工作树换行符的提示，没有空白错误
```

## 结论

1. 当前工作树达到本地 L1：角色菜单、主流程、模型中心、进化自动推进、BM1688 制品契约和中文页面可以联合回归。
2. 本地测试通过不等于生产完成。异步评估 `operation_id` 正式迁移、MySQL 备份恢复/锁等待/`2/10/20/50` 并发、真实 TPU-MLIR、BM1688 真机、S3、AICM、systemd 和两类生产账号仍未取得证据。
3. 生产当前仍是旧版本，按 RUN-0255 失败关闭；不得在没有用户明确“提交代码”前提交、推送或部署。
4. 任何后续角色产出必须继续新增独立 RUN 文档，并在 `7gai_delivery_ledger.md` 顶部记录代码、测试、生产边界和下一依赖。

## 下一步放行顺序

1. 运维提供隔离同版本 MySQL、脱敏快照、备份恢复和并发证据。
2. 完成异步评估代次的正式迁移与跨进程旧代次拒绝回归。
3. 用户明确“提交代码”后，才执行提交、推送、生产备份、迁移和部署。
4. 部署后按标注人员、管理人员两类账号回放任务、训练、预标注、模型发布/下载、S3 回流和模型禁下载。
5. 使用真实 TPU-MLIR、BM1688 Board Agent 和 AICM 对账补齐 L2/L3/L4 证据。

## 状态

- 代码：当前工作树已有历史角色工作包修改，本轮未新增业务代码。
- 测试：本地通过，证据已记录。
- 生产：未连接、未写入、未部署。
- Git：未提交、未推送，等待用户明确授权。
