# LabelU 自研前端扩展（源码，纳入 git）

本目录为 **labelU 仓库自研前端**，与官方 `frontend/labelU-Kit/` 源码配合使用。

## 目录

| 路径 | 说明 |
|------|------|
| `pages/*.html` | YOLO 训练、BM1688、AI 设置等独立页 |
| `assets/labelu_portal.js` | 顶栏导航与工作流提示 |
| `assets/labelu_standalone_shell.css` | 独立页样式 |
| `assets/task_*.js` | 注入主站 React 的扩展（任务列表 YOLO 入口等） |

## 修改方式

- **改独立页**：直接编辑 `pages/`、`assets/labelu_portal.js`
- **勿直接改** `labelU-Kit/apps/frontend/public/assets/`（会被 `labelu frontend-build` / `deploy.sh update` 从 `extensions/assets/` 覆盖）
- **服务器勿手改**：生产目录 `/data/labeIU/labeIuproject/labeIU` 下也不要改 Kit 的 `public/assets/labelu_portal.js`，否则 `git pull` 会冲突；应在本机改 `extensions/assets/` 后 push，再 `deploy.sh update`
- **改主站 React**：编辑 `../labelU-Kit/apps/frontend/src/`（需先 `labelu frontend-clone`）

Cursor 规则：`.cursor/rules/frontend-extensions-assets.mdc`

## 相关环境变量（后端 `labelu/internal/common/config.py`）

| 变量 | 说明 |
|------|------|
| `YOLO_TRAIN_ENABLED` | 是否启用 YOLO 训练 / BM1688 / 预标注创建 |
| `YOLO_TRAIN_DEVICE` | Ultralytics `device`（如 `0` / `cpu`） |
| `YOLO_PRETRAINED_WEIGHTS_DIR` | 预训练权重本地目录 |
| `TASK_SUPERUSER_USERNAMES` | 逗号分隔用户名，全局任务超管（BM1688 自定义编译命令等） |
| `PASSWORD_SECRET_KEY` | JWT 密钥（生产必填） |

启动 `labelu` 时会自动：

1. 将 `assets/task_*.js` 同步到 Kit 的 `public/assets` 并注入 `index.html`
2. 启动 Vite；后端代理主站到 Vite，扩展页仍走后端路由

## 生产构建

```bash
labelu frontend-build
```

输出到 `labelu/internal/statics/`（构建产物，已在 .gitignore）。
