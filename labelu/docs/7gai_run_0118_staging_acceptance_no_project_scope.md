# RUN-0118 去项目化生产验收基线

## 1. 本轮结论

发现 `scripts/staging_acceptance.py` 仍把项目 ID、跨项目隔离、设备发布和柔性工作流当成生产验收依赖。这与一期方案和服务端实际边界冲突：项目/组织、设备发布、OTA、规则设备绑定和柔性工作流属于已退休控制面，项目路由在一期 OpenAPI 中不应暴露。

本轮已将验收脚本改为任务权限和数据集权限驱动的无项目主链验收。

## 2. 代码路径核验

- `labelu/internal/adapter/routers/_phase_one_gate.py`：退休控制面写操作统一返回 403；
- `labelu/internal/adapter/routers/project.py`：项目路由隐藏于 OpenAPI；
- `labelu/tests/internal/adapter/routers/test_phase_one_retired_control_plane.py`：明确要求项目、组织、设备发布和工作流路径不出现在一期 OpenAPI；
- `labelu/internal/adapter/routers/dataset.py`、`training.py`、`model_version.py`、`published_model_catalog.py`：一期主链使用任务、数据集、训练、模型版本、发布目录和下载控制。

原验收脚本与上述真源不一致，属于测试基线缺陷。

## 3. 实际修改

- `scripts/staging_acceptance.py`
  - 移除 `--project-id`、`--foreign-project-id`、`--release-id` 和 `--workflow-id`；
  - OpenAPI 必须包含任务、数据集、算法目录、训练作业、模型版本、BM1688 编译、边端验证、已发布模型目录、第三方 S3 配置和工作台汇总；
  - OpenAPI 重新暴露项目、组织、设备发布、边端代理、设备任务、规则模板或工作流路径时直接失败；
  - 未登录探针改为 `/api/v1/tasks`；
  - 登录后集合探针全部不带项目参数，权限由当前用户和任务可见性决定；
  - 上下文证据改为 `task_and_dataset_permissions`。
- `labelu/tests/scripts/test_staging_acceptance.py`
  - 测试夹具改为一期无项目路径；
  - 新增项目参数被拒绝、必需路径不含项目路径的契约测试；
  - 修正未登录任务接口与已登录任务列表的夹具差异。

## 4. 验证证据

```text
python -m pytest labelu/tests/scripts/test_staging_acceptance.py \
  labelu/tests/internal/adapter/routers/test_phase_one_retired_control_plane.py \
  labelu/tests/internal/adapter/routers/test_training.py \
  -q --disable-warnings --tb=short
```

结果：`15 passed`，无失败。

另有：

- `python -m py_compile scripts/staging_acceptance.py labelu/tests/scripts/test_staging_acceptance.py` 通过；
- 仅本地测试，无生产连接、生产写入、S3/AICM/BM1688 或 systemd 证据。

## 5. 影响、风险与下一依赖

- 不改变任何生产业务 API、数据库表、菜单或用户权限；
- 旧调用若继续传项目参数，会被验收脚本明确拒绝，避免把退休控制面重新当成主流程；
- Provider/PredictionSet 仍可通过可选 ID 做兼容性详情检查，但不再按项目筛选，也不再成为一期主链必需项；
- 生产放行仍必须在真实环境执行脚本，验证后端 OpenAPI、登录、任务/数据集/训练/模型/回流路径以及退休路径隐藏状态；
- 本轮不能证明真实生产服务已符合新基线，只证明验收工具与服务端一期契约一致。

## 6. 角色与交付状态

| 角色 | 工作包 | 结果 |
|---|---|---|
| R01 产品 | WP0/P5 | 明确验收范围不包含项目/业务空间 |
| R03 架构 | WP0 | 将任务与数据集权限作为验收作用域 |
| R04 后端 | WP1/WP2 | 核对实际一期路由和退休控制面门禁 |
| R09 测试 | WP5 | 更新夹具并完成 15 项回归 |
| R10 运维 | P5 | 生产脚本仍待真实主机执行 |

本轮未提交、未推送、未部署，等待用户明确“提交代码”。平台整体仍为本地 L1。
