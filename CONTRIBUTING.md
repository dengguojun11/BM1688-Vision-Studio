# 贡献指南（LabelU）

本仓库采用 **单一总分支 + 多窗口按文件隔离** 的协作方式。

## 总分支与窗口责任

- 唯一总分支是 `codex/7g-ai-labelu`，远程对应 `origin/codex/7g-ai-labelu`。
- 所有窗口和代理只在这个分支工作；禁止新建、切换到或保留其他本地工作分支，也不使用第二个长期工作树。
- 每个窗口开始前必须声明自己负责的文件或目录，并先查看工作区状态。不同窗口不得同时修改同一个文件；同一文件必须串行处理。
- 窗口之间的独立性通过文件责任范围和精确暂存实现，不通过分支实现。A 窗口提交时，B 窗口的未提交修改必须继续留在工作区。

## 快速开始

1. 确认当前分支为 `codex/7g-ai-labelu`，并执行 `git status --short`。
2. 只修改当前窗口声明的文件范围。
3. 本地验证通过后，等待用户明确说 `提交代码`。
4. 只用 `git add -- <path>` 或 `git add -p` 暂存当前窗口的文件/代码块；禁止 `git add .`、`git add -A`、`git commit -am`。
5. 提交前检查 `git status --short`、`git diff`、`git diff --cached`；提交后核对提交文件列表和差异。
6. 按 `AGENTS.md` 的授权流程推送这个总分支、部署并完成生产验证。

规则来源与项目层说明见：

- [`AGENTS.md`](AGENTS.md)
- [`skills/projects/labelU/PROJECT.md`](skills/projects/labelU/PROJECT.md)
- [`skills/projects/labelU/INDEX.md`](skills/projects/labelU/INDEX.md)

## 提交前检查

- [ ] 只 `git add` 与本任务相关的文件
- [ ] 未包含 `test.db`、`.env`、`logs/`、临时脚本
- [ ] `git config user.name` / `user.email` 已设为自己的名字
- [ ] 相关测试已跑过（如适用）

## 前端扩展

导航、训练页等自研脚本只改 `frontend/extensions/assets/` 与 `frontend/extensions/pages/`，不要手改 Kit 下 `public/assets/` 同步产物。

## 旧流程说明

`scripts/merge_feature_to_main.sh`、`scripts/git-hooks/pre-receive` 仍保留历史的“功能分支 + main”实现，但它们与本项目当前单一总分支规则冲突，禁止用于新的修改。迁移这些自动化脚本需要单独授权；生产部署仍按 `AGENTS.md` 的 `18001` 流程执行。
