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

上传完成后，只重启独立 Web 进程；训练、编译、回流、预标注、边端验证和发布 Worker 由同一份 supervisor 配置管理：

```bash
supervisorctl -c /data/7G_AI/config/supervisord.conf restart 7gai-web
supervisorctl -c /data/7G_AI/config/supervisord.conf status
```

验证入口：

```bash
curl -fsS http://127.0.0.1:18001/healthz
curl -fsS http://127.0.0.1:18001/readyz
curl -fsS http://127.0.0.1:18001/version
```

Python 包目录和命令仍叫 `labelu`，这是兼容现有导入路径、迁移和 Worker 的内部名称；工程目录和维护名称为 `7G_AI_labelU`。
