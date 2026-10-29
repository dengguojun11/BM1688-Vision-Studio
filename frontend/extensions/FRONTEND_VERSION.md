# 前端发版版本号

真源文件：`FRONTEND_VERSION`（单行 semver，如 `1.4.2`）。

- **仅改前端**（extensions 页面/脚本、Kit 组件）：递增 `FRONTEND_VERSION`
- **仅改后端**（Python/API）：递增 `labelu/version.py`
- **前后端都改**：两处都要递增

构建时自动生成 `frontend_version.js` / `backend_version.js`；账号头像下拉菜单展示「前端版本 / 后端版本」。
