# RUN-0124 生产迁移与规范路由门禁

## 1. 本轮目标

生产环境曾出现“健康接口返回 200，但业务路由仍是旧版本”的情况：

- `/healthz`、`/readyz`、`/version` 可以正常返回；
- `/openapi.json` 才是后端真实接口清单；
- `/api-docs/openapi.json` 可能返回前端页面，不能作为接口版本真源；
- 生产只读证据显示数据集、算法目录、模型版本和工作台等一期主链路由缺失，同时仍暴露已退休的设备作业路由。

因此本轮把“进程健康”和“业务路由版本正确”拆成两个独立验收条件。

## 2. 交付内容

### 2.1 共享路由契约

新增 `scripts/ops/7gai_openapi_contract.json`，由预发布验收和部署脚本共同读取，避免两套清单漂移。

一期必需路径为：

- 任务、数据集、算法目录；
- 训练作业、模型版本、已发布模型目录；
- BM1688 编译作业、边端验证作业；
- 第三方 S3 配置、工作台汇总。

以下前缀属于已退休控制面，出现任一路径都应失败：项目、组织、部署发布、边端代理、旧边端设备作业、规则模板、柔性工作流。

### 2.2 预发布验收复用

`scripts/staging_acceptance.py` 新增 `inspect_canonical_openapi()` 纯校验函数：

- `/openapi.json` 非 JSON 或缺少 `paths` 时失败；
- 缺少任一必需路径时失败；
- 暴露任一退休路径前缀时失败；
- 合法契约通过。

脚本仍保持只读，`--mutating` 继续 fail-closed，不会因本轮改造写入业务数据。

### 2.3 部署收尾硬门禁

`deploy.sh` 的部署检查清单新增 `deploy_checklist_canonical_openapi`：

1. 请求 `http://127.0.0.1:${LABELU_PORT}/openapi.json`；
2. 强制 HTTP 200 且 `Content-Type` 为 JSON；
3. 使用系统 Python 解析共享契约和 OpenAPI `paths`；
4. 缺失主链路由或暴露退休路由，记录为 `FAIL`；
5. `deploy_finalize` 在存在任何检查失败时返回非零，不能仅打印普通告警后判定部署成功。

该门禁不读取 `/api-docs/openapi.json`，也不依赖应用虚拟环境中的业务包。

## 3. 菜单、角色和业务影响

- 不新增菜单、按钮、登录角色或项目/业务空间；
- 不改变标注人员、管理人员的业务权限；
- 不改变任务、预标注、训练、模型中心、BM1688 编译、边端验证、发布下载和 S3 回流的业务流程；
- 只增加部署验收约束，防止错误版本进入生产。

## 4. 并发与失败处理

- 路由门禁是部署收尾阶段的单次只读 HTTP 请求，不访问业务表，不增加在线请求并发；
- JSON 下载使用临时文件，校验完成立即清理，不把完整 OpenAPI 文档写入业务目录；
- curl、Python、契约文件、HTTP 状态码、内容类型或 JSON 结构任一缺失，均 fail-closed；
- 该门禁不能替代数据库迁移锁、训练 Worker 租约、BM1688 编译并发控制或真实设备容量验证。

## 5. 本地验证证据

已执行：

```text
python -m py_compile scripts/staging_acceptance.py
python -m pytest labelu/tests/scripts/test_staging_acceptance.py -q
  7 passed
python -m pytest scripts/tests/test_deploy_nginx_backend_routes.py -q
  3 passed
git diff --check -- deploy.sh scripts/staging_acceptance.py scripts/ops/7gai_openapi_contract.json labelu/tests/scripts/test_staging_acceptance.py scripts/tests/test_deploy_nginx_backend_routes.py
  passed
```

未执行：

- `scripts/tests/test_deploy_nginx_backend_routes.sh`：当前 Windows 的 `bash.exe` 仅指向不可用的 WSL 入口，无法启动 `/bin/bash`；
- 真实 MySQL 迁移、生产 systemd/Nginx、生产部署和真实浏览器验收；
- 真实 TPU-MLIR、BM1688、S3、AICM、Worker 并发和节点故障接管。

## 6. 验收等级与后续放行条件

本轮为本地 L1：脚本、共享契约和回归测试通过，但不代表生产完成。

进入生产迁移前必须在同版本 MySQL 沙箱完成迁移演练；部署时必须看到 canonical OpenAPI 门禁通过，并使用 `scripts/staging_acceptance.py` 对生产只读路由进行复核。若主链路由仍缺失或退休路由仍暴露，应停止放行，不得用健康接口 200 替代。

提交、推送、生产迁移和部署均未执行，等待用户明确“提交代码”。
