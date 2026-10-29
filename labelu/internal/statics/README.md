# statics（运行时 / 生产构建输出）

本目录 **不再存放未打包源码**。仓库内源码位置：

- 官方主站：`frontend/labelU-Kit/`
- 自研扩展：`frontend/extensions/`

## 开发

```bash
labelu frontend-clone   # 首次
labelu                  # 自动 Vite，无需本目录有文件
```

## 生产

```bash
labelu frontend-build   # 生成 dist 到本目录
```

构建后的 `index.html`、`assets/index-*.js` 等由 `.gitignore` 忽略，不提交 git。
