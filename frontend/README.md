# LabelU 单仓库前端（Monorepo）

前后端维护在 **同一个 Git 仓库**：

```
frontend/
  labelU-Kit/       # 官方主站 React 源码（纳入 git，不含 node_modules）
  extensions/       # 自研 YOLO / portal / task_*.js
labelu/             # 后端
```

## 首次：把 Kit 提交进仓库（做一次）

若本机已有 `frontend/labelU-Kit`（例如曾 `frontend-clone`）：

```bash
bash scripts/vendor_kit_into_repo.sh
git add frontend/labelU-Kit frontend/extensions
git commit -m "chore: vendor labelU-Kit into monorepo"
git push
```

之后团队成员 **`git clone` / `git pull` 即带完整前端源码**，无需再 `frontend-clone`。

## 本地开发

```bash
pip install -e .
cd frontend/labelU-Kit && pnpm install && cd ../..
labelu --host 127.0.0.1 --port 8002
```

## 服务器（无需手动的流程）

```bash
git pull
sudo ./deploy.sh update
```

`update` 会自动：`pnpm install` → `labelu frontend-build` → 重启服务（`labelu --static-only`）。

**不要**在服务器上 `frontend-clone`；源码应随 git 已在 `frontend/labelU-Kit/`。

## 说明

| 目录 | 是否提交 git |
|------|----------------|
| `frontend/labelU-Kit/` 源码 | 是 |
| `frontend/labelU-Kit/node_modules` | 否 |
| `labelu/internal/statics/` 构建结果 | 否（部署时生成） |
