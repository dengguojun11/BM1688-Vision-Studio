# 7G_AI_labelU 独立生产部署

本项目的生产实例与旧 `/data/labeIU` 项目隔离：

| 项目 | 路径或地址 |
| --- | --- |
| 代码 | `/data/7G_AI/app` |
| 配置 | `/data/7G_AI/config/app.env` |
| 运行环境 | `/data/7G_AI/runtime` |
| 数据 | `/data/7G_AI/data` |
| 媒体和模型 | `/data/7G_AI/media` |
| 日志和临时文件 | `/data/7G_AI/logs`、`/data/7G_AI/tmp` |
| Web | `http://113.141.72.175:18001/` |

上传代码时使用仓库根目录的上传脚本。默认目标已固定为 `/data/7G_AI/app`，不会写入旧项目目录：

```powershell
python scripts/upload_to_server.py --dry-run
python scripts/upload_to_server.py
```

上传完成后，先按生产环境文件重建前端静态资源，再重启独立 Web 进程：

```bash
cd /data/7G_AI/app
LABELU_ENV_FILE=/data/7G_AI/config/app.env \
  /data/7G_AI/runtime/venv/bin/python -m labelu.main frontend-build
supervisorctl -c /data/7G_AI/config/supervisord.conf restart 7gai-web
supervisorctl -c /data/7G_AI/config/supervisord.conf status
```

> 必须显式设置 `LABELU_ENV_FILE=/data/7G_AI/config/app.env`。否则 `frontend-build`
> 会优先读取服务器上遗留的 `/data/labeIU/labeIuMedia/.env`，用其中的
> `SAMPLE_LOCKING_ENABLED=true` 编译出与 18001 后端（默认关闭）不一致的前端：
> 标注页 WebSocket 握手 403、`isMeTheCurrentUser=false`，导致所有预标注/标注按钮被禁用。

验证入口：

```bash
curl -fsS http://127.0.0.1:18001/healthz
curl -fsS http://127.0.0.1:18001/readyz
curl -fsS http://127.0.0.1:18001/version
```

Python 包目录和命令仍叫 `labelu`，这是兼容现有导入路径、迁移和 Worker 的内部名称；工程目录和维护名称为 `7G_AI_labelU`。
