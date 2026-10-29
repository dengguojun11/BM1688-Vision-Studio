# 贡献指南（LabelU）

本仓库采用 **方案 B：功能分支 + 受保护的 main**。

## 快速开始

1. 从最新 `main` 切分支：`feat/<你的名字>-<功能简述>`
2. 开发、自测、`git commit`
3. **`git push origin feat/...`**（不要 push main）
4. 请负责人 review 后合并
5. 负责人 `deploy.sh update` 发版

完整流程、命令示例与 hook 说明见：

- [`docs/06-运维与协作/运维、协作与发布手册.md`](docs/06-运维与协作/运维、协作与发布手册.md)
- [`docs/README.md`](docs/README.md)

## 提交前检查

- [ ] 只 `git add` 与本任务相关的文件
- [ ] 未包含 `test.db`、`.env`、`logs/`、临时脚本
- [ ] `git config user.name` / `user.email` 已设为自己的名字
- [ ] 相关测试已跑过（如适用）

## 前端扩展

导航、训练页等自研脚本只改 `frontend/extensions/assets/` 与 `frontend/extensions/pages/`，不要手改 Kit 下 `public/assets/` 同步产物。

## 合并与部署（负责人）

```bash
bash scripts/merge_feature_to_main.sh feat/xxx
sudo env INSTALL_EXTRAS=yolo,image-pipeline bash deploy.sh update
```
