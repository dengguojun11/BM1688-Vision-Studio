# RUN-0323 本地浏览器菜单与 API 只读验收（2026-09-21）

## 1. 本轮范围

本轮由主导人执行本地服务启动和浏览器只读验收，目标是确认当前工作树能够从项目入口启动，并且登录后可以看到 7GAI 平台菜单与模型训练入口。

本轮不连接生产，不提交、不推送、不部署，不创建任务、数据集、训练作业或模型发布记录。

## 2. 环境与启动方式

- 启动入口：`labelu --host 127.0.0.1 --port 8002 --media-host http://127.0.0.1:8002`
- 启动时通过进程级环境变量强制使用：`sqlite:///./.tmp/labelu-dev.sqlite`
- 本地媒体地址：`http://127.0.0.1:8002`
- 监听进程：`127.0.0.1:8002`，本轮验收时保持监听
- 未修改 `.env`，未使用生产 MySQL 配置

## 3. 责任角色与工作包

| 角色 | 工作包 | 本轮交付 |
|---|---|---|
| R01 产品 | WP5 | 确认登录后平台菜单和模型中心入口可达 |
| R03 架构 | WP2/WP5 | 确认本地服务、文档和 OpenAPI 响应链路可用 |
| R04 后端 | WP2 | 确认认证接口和页面后端响应可用 |
| R08 前端/UI | WP4 | 确认 7GAI 外壳、主导航、训练嵌入页可见 |
| R09 测试 | WP5 | 执行本地浏览器只读验收用例 |
| R10 运维 | OPS | 确认本地 SQLite 启动边界，避免误连生产 |

## 4. 验收命令与结果

```text
$env:LABELU_PLATFORM_LIVE_URL='http://127.0.0.1:8002'
$env:LABELU_PLATFORM_LIVE_USERNAME='admin@123.com'
$env:LABELU_PLATFORM_LIVE_PASSWORD='Admin@123'
python -m pytest labelu/tests/e2e/test_7gai_platform_local_live_frontend.py -q --disable-warnings --tb=short
```

结果：

```text
2 passed, 114 warnings in 20.87s
```

模型中心专项回归：

```text
python -m pytest labelu/tests/internal/adapter/routers/test_yolo_bm1688_exports.py labelu/tests/internal/application/service/test_catalog_performance_paths.py -q --disable-warnings --tb=short
43 passed, 1 skipped, 119 warnings in 6.71s
```

该回归覆盖模型中心列表、独立摘要接口、筛选口径、摘要跳过参数和大数据量查询路径。

覆盖内容：

1. 本地登录并确认首页跳转到工作台。
2. 访问工作台、数据集、数据接入、标注复核、算法、训练、模型、发布、进化、工作流、管理、任务等平台菜单，确认主导航和 7GAI 中文外壳存在。
3. 访问 `/training?embedded=1`，确认模型训练嵌入页可见。
4. 只读检查 `/docs` 和 `/openapi.json`，确认中文 API 文档、数据集和模型版本路由存在，项目路由未出现在最终契约中。

## 5. 页面入口

- 登录：[http://127.0.0.1:8002/login](http://127.0.0.1:8002/login)
- 工作台：[http://127.0.0.1:8002/workspace](http://127.0.0.1:8002/workspace)
- 训练：[http://127.0.0.1:8002/yolo-training](http://127.0.0.1:8002/yolo-training)
- 模型中心：[http://127.0.0.1:8002/yolo-bm1688-exports](http://127.0.0.1:8002/yolo-bm1688-exports)

## 6. 影响、风险与遗留项

- 数据/API/页面影响：本轮仅读取认证、页面和 OpenAPI；没有业务写入。
- 风险：本地浏览器通过不等于真实 MySQL、S3、AICM、TPU-MLIR、GPU 或 BM1688 验收通过。
- 遗留：真实训练、PT→ONNX→FP32/FP16/INT8 BModel、BM1688 真机、真实 S3/AICM、同版本 MySQL 并发和生产部署证据仍未完成。
- 下一依赖：提供可达的预生产 MySQL、训练节点、TPU-MLIR 和 BM1688 设备后，再执行 L2/HIL 验收。

## 7. 提交与部署状态

本轮未修改业务代码，未提交、未推送、未部署、未重启生产。继续等待用户明确说“提交代码”。
