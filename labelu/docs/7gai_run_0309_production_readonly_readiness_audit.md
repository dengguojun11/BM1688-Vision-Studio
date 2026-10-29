# RUN-0309 生产只读放行准备审计（2026-09-20）

## 1. 审计范围

本轮只使用 `GET` 和登录接口进行生产核验，不创建任务、不上传文件、不触发训练/编译/发布、不修改配置。目标是确认当前线上路由、认证边界、运行开关和 BM1688 设备状态，为后续提交和预生产放行提供事实依据。

生产地址：`http://113.141.72.175:10000`。

## 2. 未登录只读探针

| 路径 | 结果 | 结论 |
|---|---:|---|
| `/health` | `200 text/html` | 返回前端单页，不是后端 JSON 健康证明 |
| `/openapi.json` | `200 application/json` | 线上路由可读取 |
| `/api/v1/tasks` | `401` | 普通业务接口需要登录 |
| `/api/v1/published-models` | `401` | 模型目录需要登录 |
| `/internal/v1/experiment-datasets` | `200`，返回真实数据 | **阻断项：内部回流接口未登录可读** |
| `/api/v1/edge-device-jobs` | `GET 404` | 该路径只登记了 POST，不能用 GET 结果判断 POST 已退休 |

OpenAPI 进一步确认：

- `/internal/v1/experiment-datasets` 的 GET/POST 没有安全声明；
- `/api/v1/edge-device-jobs`、`/claim`、`/{job_id}/result` 仍登记为 POST，并声明 `HTTPBearer`；
- `/api/v1/board-agent/jobs/next` 仍登记在线；
- `/api/v1/published-models` 需要 `HTTPBearer`。

本地代码已在 `experiment_dataset_import.py` 和 `strategy_evolution.py` 为内部写入接口增加服务鉴权依赖，但当前生产 OpenAPI/行为尚未体现该边界，说明线上仍是旧部署或配置漂移。

## 3. 管理员只读核验

使用生产默认管理员账号登录，仅读取当前账号和列表/配置摘要，不输出令牌、密码或密钥：

| 路径 | 结果 | 关键观察 |
|---|---:|---|
| `/api/v1/users/me` | `200` | 管理员账号可正常认证 |
| `/api/v1/tasks` | `200` | 当前任务总数 `132` |
| `/api/v1/published-models` | `200` | 当前发布模型总数 `17` |
| `/api/v1/yolo-bm1688-exports` | `200` | 当前编译记录总数 `68`，列表摘要显示有 `1` 条运行中记录 |
| `/api/v1/edge-devices` | `200` | 存在活动设备 ID，但不代表设备可执行 |
| `/api/v1/edge-devices/active` | `GET 404` | OpenAPI 显示该路径只支持 PUT |

## 4. 线上运行开关与设备状态

| 配置 | 生产只读结果 | 放行判断 |
|---|---|---|
| 自动训练设置 | `schema_ready=False`、`outbox_enabled=False`、`pool_enabled=False`、`scheduler_enabled=False`、`train_enabled=False`、`bm1688_enabled=False`、`edge_validation_enabled=False` | 自动链路未放量，符合失败关闭要求 |
| AICM 发布配置 | `ready_to_publish=True` | 仅表示配置检查就绪，不代表真实发布已验收 |
| BM1688 编译配置 | `bmodel_basename=yolov11n`、`calibration_count_mode=adaptive` | 需要与本地版本和真实 TPU-MLIR 对账 |
| SSH 边端配置 | `enabled=True`、`probe_status=fail`、`auto_run_ready=False` | 设备当前不可自动执行，不能作为 L3 证据 |
| S3 回流配置 | 活动连接为 `env_default` | 未执行真实对象下载、去重和三方对账 |
| 质量策略 | 存在活动策略 | 未取得真实固定评估集和 BM1688 三精度证据 |

## 5. 阻断项与下一依赖

1. 生产内部实验数据接口必须先部署服务鉴权，并用未登录、错误令牌、有效服务令牌三组请求验证；在此之前不能开放第三方回流写入口。
2. 必须先提交并部署与当前本地代码一致的版本，再重新核对 OpenAPI、路由方法和配置开关，不能把当前旧版本读数当作新代码证据。
3. BM1688 开关继续保持关闭，直到真实 TPU-MLIR、三精度 BModel、固定评估集、Board Agent/SSH 设备加载推理和断网重启接管完成。
4. AICM 发布必须先做测试环境发布、AICM `model_set_id`、本地下载目录和回流 metadata 三方对账，再进入生产小流量。
5. 本轮没有执行任何生产写入、迁移、Worker 启停或模型发布。

## 6. 验收等级

本轮为 **生产 L0 只读审计**，不是 L2/L3/L4。证据文件只证明当前线上状态和阻断项，不能证明本地改动已经部署。相关结果已同步到 `7gai_role_delivery_plan.md` 和 `7gai_delivery_ledger.md`。

