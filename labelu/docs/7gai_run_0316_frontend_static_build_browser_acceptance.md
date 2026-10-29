# RUN-0316 前端静态构建与浏览器菜单验收（2026-09-20）

## 1. 目标

验证七大 AI 进化平台方案的前端源码已经同步到实际服务使用的静态目录，并在隔离本地服务中通过真实浏览器检查：

- 六个一级菜单及固定顺序；
- 模型中心、进化中心详情使用居中弹窗，不使用抽屉；
- 历史扩展路径只做兼容跳转，不重新成为一级菜单；
- 无项目/业务空间主链路；
- 中文页面与管理人员登录后的主链路访问。

## 2. 执行环境

| 项目 | 值 |
|---|---|
| 数据库 | `.tmp/local0316/labelu.sqlite`，与生产隔离 |
| 服务 | `http://127.0.0.1:9899` |
| 启动参数 | `python -m labelu.main dev --host 127.0.0.1 --port 9899 --static-only --skip-install --media-host http://127.0.0.1:9899` |
| 后台任务 | `LABELU_DISABLE_BACKGROUND_WATCHDOGS=true`、`YOLO_TRAIN_ENABLED=false` |
| 构建命令 | `python -m labelu.main frontend-build` |
| 构建结果 | 前端生产构建完成，73 个 gzip 静态资源同步至 `labelu/internal/statics` |

未连接生产、MySQL、S3、AICM、TPU-MLIR、GPU 或 BM1688；未执行写入、迁移、提交、推送和部署。

## 3. 验收结果

### 3.1 平台壳浏览器验收

```text
$env:LABELU_PLATFORM_URL='http://127.0.0.1:9899'
python -m pytest labelu/tests/e2e/test_7gai_platform_shell_frontend.py -q --disable-warnings --tb=short
9 passed, 114 warnings
```

覆盖六菜单顺序、模型详情弹窗、进化中心详情弹窗、旧部署/工作流兼容跳转和中文页面入口。

### 3.2 登录后全路由与后端文档验收

```text
$env:LABELU_PLATFORM_LIVE_URL='http://127.0.0.1:9899'
$env:LABELU_PLATFORM_LIVE_USERNAME='admin@123.com'
$env:LABELU_PLATFORM_LIVE_PASSWORD='Admin@123'
python -m pytest labelu/tests/e2e/test_7gai_platform_local_live_frontend.py -q --disable-warnings --tb=short
2 passed, 114 warnings
```

覆盖工作台、任务与标注、数据集兼容页、训练、模型、发布兼容页、进化中心、系统管理、任务兼容页，以及 `/docs` 和 `/openapi.json` 后端响应。

## 4. 本轮修正

之前该组浏览器测试的 9 个失败来自旧静态产物仍位于 `labelu/internal/statics`，不是前端源码路由逻辑缺失。本轮重新构建后，实际服务加载的新产物与源码一致。

同时将本地 live 测试中已经过时的 `/api/v1/projects` 断言改为：

- 必须存在 `/api/v1/datasets`；
- 必须存在 `/api/v1/model-versions`；
- 不再要求 `/api/v1/projects`，符合一期取消项目/业务空间的方案。

### 4.1 统一本地门禁

```text
python scripts/verify_staging_and_hil.py --environment local --evidence-root .tmp/evidence/run0316 --run-local --check
blocking=0
```

8 项门禁全部通过，覆盖迁移唯一 head、空库重复升级、前端外部门禁语义、去项目化审计、证据完整性、预生产探针契约、Provider 契约和板端协议。证据：`.tmp/evidence/run0316/local-gates-20260920T151241Z.json`。

## 5. 结论和边界

- 本地达到 L1：静态构建、真实浏览器、登录后菜单和后端文档契约通过。
- 该结果不代表生产静态文件已经更新；`RUN-0314` 的生产版本漂移仍然是 L0 阻断项。
- 生产放行仍需在用户授权提交后，完成预生产构建部署、两类真实账号、反向代理、MySQL、AICM、S3、TPU-MLIR、BM1688 和下载目录验证。
