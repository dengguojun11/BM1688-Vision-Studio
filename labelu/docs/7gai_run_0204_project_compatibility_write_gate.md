# RUN-0204 数据集项目兼容字段与新写链门禁（2026-09-20）

## 责任角色

- R03 架构：项目维度退役后的数据库外键闭包、历史兼容边界。
- R04 后端：数据集和自动训练计划的新建写入口门禁。
- R09 测试：迁移范围、参数校验和相关路由回归。
- R01 产品协作：确认一期不新增项目/业务空间菜单和创建流程。

## 本轮问题

一期已经取消项目/业务空间，但 `dataset`、`dataset_version` 的历史 `project_id`
仍被 ORM 声明为 `ForeignKey("project.id")`。这会让空库建表闭包继续依赖已退役的
`project` 表；同时自动训练计划的创建命令仍能接收非空 `project_id`，未来误开启
自动训练写入时会重新产生项目维度。

## 实施规则

1. `dataset.project_id`、`dataset_version.project_id` 保留为可空整数列，保留生产
   历史数据读取和迁移审计；一期 ORM 不再声明新项目外键。
2. 数据集新建、任务自动生成数据集和数据集版本生成均不写入项目字段，归属使用
   创建者、任务、数据集和不可变版本血缘。
3. `CreateAutoTrainingPlanCommand`、`PreviewAutoTrainingPlanCommand` 保留空值字段
   兼容旧客户端序列化；传入非空项目 ID 直接参数校验失败，不进入数据库和服务层。
4. `task.project_id`、自动训练历史计划和项目审计查询暂不删除，作为历史只读兼容
   入口继续纳入后续迁移清单，不能被一期新菜单调用。

## 修改范围

- `labelu/internal/domain/models/dataset.py`
  - 两个历史兼容列去掉新的 `project` 外键声明，列和索引保留。
- `labelu/internal/application/command/auto_training.py`
  - 自动训练计划创建/预览命令拒绝非空 `project_id`。
- `labelu/tests/alembic/test_phase_one_schema_scope.py`
  - 增加数据集兼容列保留、但不形成项目外键的结构契约。
- `labelu/tests/internal/application/service/test_auto_training_phase_one_gate.py`
  - 增加创建和预览命令的项目维度拒绝回归。

## 本地验证

```text
python -m pytest labelu/tests/alembic/test_phase_one_schema_scope.py \
  labelu/tests/alembic/test_phase_one_dataset_project_fk_rebuild.py \
  labelu/tests/internal/adapter/routers/test_training.py -q
14 passed

python -m pytest labelu/tests/internal/application/service/test_auto_training_phase_one_gate.py \
  labelu/tests/alembic/test_phase_one_schema_scope.py -q
45 passed

python -m pytest labelu/tests/internal/adapter/routers/test_auto_training.py -q
120 passed
```

## 未完成与放行边界

- 本轮没有删除生产列，没有执行 Alembic DDL，没有修改历史 `task`/自动训练计划
  的项目数据，也没有连接生产 MySQL。
- 正式生产迁移仍需在备份和同版本 MySQL 预演后确认历史外键约束是否存在；若存在，
  只能用独立兼容迁移解除约束，不能直接删列。
- `task.project_id`、自动训练历史读取接口、旧项目分配路由仍需后续角色审计，当前
  结论是“历史可读、一期新写不进入”，不是“项目表已从生产删除”。
- 本轮状态：本地 L1，未提交、未推送、未部署。
